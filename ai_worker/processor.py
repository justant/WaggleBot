"""
AI Worker Processor with Robust Error Handling

견고한 에러 핸들링 및 재시도 메커니즘
"""

import hashlib
import json
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ai_worker.gpu_manager import get_gpu_manager, ModelType
from ai_worker.llm import ScriptData, generate_script, summarize
from ai_worker.thumbnail import generate_thumbnail, get_thumbnail_path
from ai_worker.tts import get_tts_engine
from ai_worker.video import render_preview
from config.settings import MEDIA_DIR, load_pipeline_config, MAX_RETRY_COUNT
from db.models import Content, Post, PostStatus
from db.session import SessionLocal

logger = logging.getLogger(__name__)


# ===========================================================================
# 에러 타입 정의
# ===========================================================================

class FailureType(Enum):
    """처리 실패 타입"""
    LLM_ERROR = "llm_error"              # LLM 요약 실패 (재시도 불가)
    TTS_ERROR = "tts_error"              # TTS 생성 실패 (재시도 가능)
    RENDER_ERROR = "render_error"        # 영상 렌더링 실패 (재시도 가능)
    NETWORK_ERROR = "network_error"      # 네트워크 오류 (재시도 가능)
    RESOURCE_ERROR = "resource_error"    # 리소스 부족 (VRAM 등, 재시도 가능)
    UNKNOWN_ERROR = "unknown_error"      # 알 수 없는 오류 (재시도 가능)


@dataclass
class RetryPolicy:
    """재시도 정책"""
    max_attempts: int = MAX_RETRY_COUNT   # 최대 시도 횟수
    backoff_factor: float = 2.0           # 백오프 배수
    initial_delay: float = 5.0            # 초기 대기 시간 (초)


# ===========================================================================
# Robust Processor
# ===========================================================================

class RobustProcessor:
    """견고한 게시글 처리기"""

    def __init__(self, retry_policy: Optional[RetryPolicy] = None):
        self.retry_policy = retry_policy or RetryPolicy()
        self.cfg = load_pipeline_config()
        self.gpu_manager = get_gpu_manager()

    async def process_with_retry(self, post: Post, session: Session) -> bool:
        """
        재시도 메커니즘을 포함한 게시글 처리

        Args:
            post: 처리할 게시글
            session: DB 세션

        Returns:
            성공 여부
        """
        attempt = 0
        last_error = None
        failure_type = None

        # 상태를 PROCESSING으로 변경
        post.status = PostStatus.PROCESSING
        post.retry_count = (post.retry_count or 0) + 1
        session.commit()
        logger.info(
            "처리 시작: post_id=%d title=%s (attempt=%d/%d)",
            post.id, post.title[:40], post.retry_count, self.retry_policy.max_attempts
        )

        while attempt < self.retry_policy.max_attempts:
            try:
                # GPU 메모리 상태 로그
                self.gpu_manager.log_memory_status()

                # ===== Step 1: LLM 대본 생성 =====
                logger.info("[Step 1/3] LLM 대본 생성 중...")
                with self.gpu_manager.managed_inference(ModelType.LLM, "summarizer"):
                    script = self._safe_generate_summary(post, session)
                logger.info("[Step 1/3] ✓ 대본 완료 (%d자)", len(script.to_plain_text()))

                # ===== Step 2: TTS 생성 =====
                logger.info("[Step 2/3] TTS 음성 생성 중...")
                with self.gpu_manager.managed_inference(ModelType.TTS, "tts_engine"):
                    audio_path = await self._safe_generate_tts(script.to_plain_text(), post.id)
                logger.info("[Step 2/3] ✓ 음성 완료: %s", audio_path)

                # ===== Step 3: 렌더링 (render_style에 따라 분기) =====
                render_style = json.loads(script.to_json()).get("render_style", "layout")
                logger.info("[Step 3/3] 렌더링 중 (style=%s)...", render_style)
                if render_style == "layout":
                    from ai_worker.layout_renderer import render_layout_video
                    video_path = render_layout_video(post, script)
                else:
                    video_path = self._safe_render_video(post, audio_path, script.to_json())
                logger.info("[Step 3/3] ✓ 렌더링 완료: %s", video_path)

                # ===== Content 저장 =====
                self._save_content(post, session, script, audio_path, video_path)

                # ===== 썸네일 생성 =====
                try:
                    images = post.images if isinstance(post.images, list) else []
                    thumb_path = get_thumbnail_path(post.id)
                    _MOOD_TO_STYLE = {
                        "funny": "funny",
                        "shocking": "dramatic",
                        "serious": "news",
                        "heartwarming": "question",
                    }
                    thumb_style = _MOOD_TO_STYLE.get(script.mood, "dramatic")
                    generate_thumbnail(script.hook, images, thumb_path, style=thumb_style)
                    content = session.query(Content).filter(Content.post_id == post.id).first()
                    if content is not None:
                        upload_meta = dict(content.upload_meta or {})
                        upload_meta["thumbnail_path"] = str(thumb_path)
                        content.upload_meta = upload_meta
                        session.flush()
                        logger.info("썸네일 생성 완료: %s", thumb_path)
                except Exception:
                    logger.warning("썸네일 생성 실패 (비치명적)", exc_info=True)

                # ===== 성공 처리 =====
                post.status = PostStatus.PREVIEW_RENDERED
                session.commit()
                logger.info(
                    "✅ 처리 성공: post_id=%d → PREVIEW_RENDERED (attempts=%d)",
                    post.id, attempt + 1
                )
                return True

            except Exception as e:
                attempt += 1
                last_error = e
                failure_type = self._classify_error(e)

                # 에러 로깅
                logger.error(
                    "❌ 처리 실패: post_id=%d (attempt=%d/%d) error_type=%s",
                    post.id, attempt, self.retry_policy.max_attempts,
                    failure_type.value,
                    exc_info=True
                )

                # 에러 상세 로그
                self._log_failure(post.id, failure_type, str(e), attempt)

                # 재시도 불가능한 에러면 즉시 중단
                if failure_type == FailureType.LLM_ERROR:
                    logger.critical(
                        "🚫 재시도 불가: post_id=%d (LLM 에러 - 즉시 중단)",
                        post.id
                    )
                    break

                # 최대 시도 횟수 도달 전이면 재시도
                if attempt < self.retry_policy.max_attempts:
                    delay = self._calculate_backoff_delay(attempt)
                    logger.warning(
                        "🔄 재시도 대기: post_id=%d (%.1f초 후 재시도)",
                        post.id, delay
                    )
                    time.sleep(delay)
                    session.rollback()  # 트랜잭션 롤백
                else:
                    logger.error(
                        "⛔ 최대 재시도 초과: post_id=%d (attempts=%d)",
                        post.id, attempt
                    )

        # ===== 최종 실패 처리 =====
        self._mark_as_failed(post, session, failure_type, last_error, attempt)
        return False

    def _safe_generate_summary(self, post: Post, session: Session) -> ScriptData:
        """
        LLM 대본 생성. DB에 기존 JSON 대본이 있으면 재사용한다.

        Args:
            post: 게시글
            session: DB 세션

        Returns:
            ScriptData

        Raises:
            Exception: LLM 에러
        """
        try:
            # 기존 대본 확인 (편집실에서 저장된 JSON)
            existing = session.query(Content).filter(Content.post_id == post.id).first()
            if existing and existing.summary_text:
                try:
                    script = ScriptData.from_json(existing.summary_text)
                    if script.hook and len(script.hook) >= 5:
                        logger.info("[Step 1/3] 기존 대본 재사용 (LLM 스킵): post_id=%d", post.id)
                        return script
                except Exception:
                    logger.debug("기존 summary_text JSON 파싱 실패 — 새로 생성")

            # 베스트 댓글 추출
            best_comments = sorted(post.comments, key=lambda c: c.likes, reverse=True)[:5]
            comment_texts = [f"{c.author}: {c.content[:100]}" for c in best_comments]

            # 피드백 설정 로드 (feedback_config.json)
            extra_instructions: str | None = None
            try:
                from analytics.feedback import load_feedback_config
                fb = load_feedback_config()
                extra_instructions = fb.get("extra_instructions") or None
            except Exception:
                logger.debug("feedback_config 로드 실패 — 무시", exc_info=True)

            # A/B 변형 설정 우선 적용 (variant_config > feedback)
            try:
                existing_content = session.query(Content).filter(
                    Content.post_id == post.id
                ).first()
                if existing_content and existing_content.variant_config:
                    variant_extra = existing_content.variant_config.get("extra_instructions")
                    if variant_extra:
                        extra_instructions = variant_extra
                        logger.info(
                            "[A/B] 변형 설정 적용: post_id=%d label=%s",
                            post.id, existing_content.variant_label,
                        )
            except Exception:
                logger.debug("variant_config 로드 실패 — 무시", exc_info=True)

            # LLM 대본 생성
            script = generate_script(
                title=post.title,
                body=post.content or "",
                comments=comment_texts,
                model=self.cfg.get("llm_model"),
                extra_instructions=extra_instructions,
            )

            # 유효성 검사
            plain = script.to_plain_text()
            if not plain or len(plain) < 10:
                raise ValueError("대본 텍스트가 너무 짧습니다")

            return script

        except Exception:
            logger.exception("LLM 대본 생성 실패")
            raise

    async def _safe_generate_tts(self, text: str, post_id: int) -> Path:
        """
        안전하게 TTS 음성 생성

        Args:
            text: 요약 텍스트
            post_id: 게시글 ID

        Returns:
            음성 파일 경로

        Raises:
            Exception: TTS 에러
        """
        try:
            tts_engine = get_tts_engine(self.cfg["tts_engine"])
            voice_id = self.cfg["tts_voice"]

            audio_dir = MEDIA_DIR / "audio"
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio_path = audio_dir / f"post_{post_id}.mp3"

            # TTS 캐시 확인 (동일 텍스트+목소리 → 재합성 스킵)
            tts_cache_dir = MEDIA_DIR / "tmp" / "tts_cache"
            tts_cache_dir.mkdir(parents=True, exist_ok=True)
            cache_hash = hashlib.md5(f"{voice_id}:{text}".encode()).hexdigest()
            cached_audio = tts_cache_dir / f"{cache_hash}.mp3"
            if cached_audio.exists():
                shutil.copy2(cached_audio, audio_path)
                logger.info("[TTS 캐시 히트] post_id=%d", post_id)
            else:
                # TTS 생성
                await tts_engine.synthesize(text, voice_id, audio_path)
                shutil.copy2(audio_path, cached_audio)  # 캐시 저장

            # 파일 존재 확인
            if not audio_path.exists():
                raise FileNotFoundError(f"음성 파일 생성 실패: {audio_path}")

            # 파일 크기 확인 (최소 1KB)
            if audio_path.stat().st_size < 1024:
                raise ValueError(f"음성 파일이 너무 작습니다: {audio_path.stat().st_size} bytes")

            return audio_path

        except Exception as e:
            logger.exception("TTS 생성 실패")
            raise

    def _safe_render_video(
        self, post: Post, audio_path: Path, summary_text: str
    ) -> Path:
        """
        안전하게 프리뷰 영상 렌더링 (480×854, libx264 CPU)

        Args:
            post: 게시글
            audio_path: 음성 파일 경로
            summary_text: 요약 텍스트 JSON

        Returns:
            프리뷰 영상 파일 경로

        Raises:
            Exception: 렌더링 에러
        """
        try:
            video_path = render_preview(post, audio_path, summary_text, self.cfg)

            # 파일 존재 확인
            if not video_path.exists():
                raise FileNotFoundError(f"영상 파일 생성 실패: {video_path}")

            # 파일 크기 확인 (최소 100KB)
            if video_path.stat().st_size < 100 * 1024:
                raise ValueError(
                    f"영상 파일이 너무 작습니다: {video_path.stat().st_size / 1024:.1f}KB"
                )

            return video_path

        except Exception as e:
            logger.exception("영상 렌더링 실패")
            raise

    def _classify_error(self, error: Exception) -> FailureType:
        """
        에러 분류

        Args:
            error: 발생한 예외

        Returns:
            에러 타입
        """
        error_msg = str(error).lower()
        error_type = type(error).__name__.lower()

        # LLM 에러 (재시도 불가)
        if "ollama" in error_msg or "llm" in error_msg:
            return FailureType.LLM_ERROR

        # TTS 에러
        if "tts" in error_msg or "synthesize" in error_msg or "audio" in error_msg:
            return FailureType.TTS_ERROR

        # 렌더링 에러
        if "render" in error_msg or "video" in error_msg or "ffmpeg" in error_msg:
            return FailureType.RENDER_ERROR

        # 네트워크 에러
        if any(x in error_type for x in ["timeout", "connection", "network"]):
            return FailureType.NETWORK_ERROR

        # 리소스 에러 (VRAM, 디스크)
        if any(x in error_msg for x in ["memory", "vram", "cuda", "disk", "space"]):
            return FailureType.RESOURCE_ERROR

        # 기타
        return FailureType.UNKNOWN_ERROR

    def _calculate_backoff_delay(self, attempt: int) -> float:
        """
        Exponential Backoff 지연 시간 계산

        Args:
            attempt: 시도 횟수 (1부터 시작)

        Returns:
            대기 시간 (초)
        """
        return self.retry_policy.initial_delay * (self.retry_policy.backoff_factor ** (attempt - 1))

    def _log_failure(self, post_id: int, failure_type: FailureType, error_msg: str, attempt: int):
        """
        에러 로그 기록

        Args:
            post_id: 게시글 ID
            failure_type: 에러 타입
            error_msg: 에러 메시지
            attempt: 시도 횟수
        """
        log_file = MEDIA_DIR / "logs" / "failures.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)

        with open(log_file, "a", encoding="utf-8") as f:
            timestamp = datetime.now().isoformat()
            f.write(
                f"{timestamp} | post_id={post_id} | "
                f"failure_type={failure_type.value} | "
                f"attempt={attempt} | "
                f"error={error_msg[:200]}\n"
            )

    def _save_content(
        self,
        post: Post,
        session: Session,
        script: ScriptData,
        audio_path: Path,
        video_path: Path
    ):
        """
        Content 레코드 저장

        Args:
            post: 게시글
            session: DB 세션
            script: 구조화 대본
            audio_path: 음성 파일 경로
            video_path: 영상 파일 경로
        """
        content = session.query(Content).filter(Content.post_id == post.id).first()
        if content is None:
            content = Content(post_id=post.id)
            session.add(content)

        content.summary_text = script.to_json()
        content.audio_path = str(audio_path)
        content.video_path = str(video_path)
        session.flush()

    def _mark_as_failed(
        self,
        post: Post,
        session: Session,
        failure_type: Optional[FailureType],
        last_error: Optional[Exception],
        attempts: int
    ):
        """
        게시글을 FAILED 상태로 마킹

        Args:
            post: 게시글
            session: DB 세션
            failure_type: 에러 타입
            last_error: 마지막 에러
            attempts: 시도 횟수
        """
        post.status = PostStatus.FAILED
        session.commit()

        logger.error(
            "⛔ 최종 실패 처리: post_id=%d → FAILED | "
            "failure_type=%s | attempts=%d | error=%s",
            post.id,
            failure_type.value if failure_type else "unknown",
            attempts,
            str(last_error)[:100] if last_error else "N/A"
        )

    # ===========================================================================
    # 파이프라인 분리 스테이지 (병렬 처리용)
    # ===========================================================================

    async def llm_tts_stage(self, post_id: int) -> tuple[ScriptData, Path]:
        """LLM 대본 생성 + TTS 합성 (CUDA/GPU 단계).

        파이프라인 병렬화에서 독립적으로 호출되는 1단계.
        완료 시 Content에 script/audio 중간 저장 후 (ScriptData, audio_path) 반환.
        """
        with SessionLocal() as session:
            post = session.query(Post).filter_by(id=post_id).first()
            if post is None:
                raise ValueError(f"Post {post_id} 없음")

            post.status = PostStatus.PROCESSING
            post.retry_count = (post.retry_count or 0) + 1
            session.commit()
            logger.info("[Pipeline LLM+TTS] 시작: post_id=%d", post_id)

            # A/B 테스트 변형 배정 (활성 테스트 있을 경우)
            try:
                from analytics.ab_test import assign_variant
                assign_variant(post_id, session)
                session.commit()
            except Exception:
                logger.debug("A/B 변형 배정 실패 — 무시", exc_info=True)

            with self.gpu_manager.managed_inference(ModelType.LLM, "summarizer"):
                script = self._safe_generate_summary(post, session)
            logger.info("[Pipeline LLM+TTS] ✓ 대본 완료 (%d자)", len(script.to_plain_text()))

            with self.gpu_manager.managed_inference(ModelType.TTS, "tts_engine"):
                audio_path = await self._safe_generate_tts(script.to_plain_text(), post_id)
            logger.info("[Pipeline LLM+TTS] ✓ 음성 완료: %s", audio_path)

            # 중간 결과 저장 (렌더 단계에서 재사용)
            content = session.query(Content).filter_by(post_id=post_id).first()
            if content is None:
                content = Content(post_id=post_id)
                session.add(content)
            content.summary_text = script.to_json()
            content.audio_path = str(audio_path)
            session.commit()

        return script, audio_path

    def render_stage(self, post_id: int, script: ScriptData, audio_path: Path) -> Path:
        """영상 렌더링 + 썸네일 생성 (CPU 단계).

        Content.summary_text의 render_style 필드로 렌더러를 선택한다:
        - 기본값: Ken Burns 슬라이드쇼 (기존 방식)

        파이프라인 병렬화에서 독립적으로 호출되는 2단계.
        완료 시 post.status → PREVIEW_RENDERED.
        """
        _MOOD_TO_STYLE = {
            "funny": "funny",
            "shocking": "dramatic",
            "serious": "news",
            "heartwarming": "question",
        }

        with SessionLocal() as session:
            post = session.query(Post).filter_by(id=post_id).first()
            if post is None:
                raise ValueError(f"Post {post_id} 없음")

            # render_style 확인: DB에 저장된 summary_text JSON에서 읽는다
            render_style = "layout"
            try:
                existing = session.query(Content).filter_by(post_id=post_id).first()
                if existing and existing.summary_text:
                    _d = json.loads(existing.summary_text)
                    render_style = _d.get("render_style", "layout")
            except Exception:
                logger.debug("render_style 파싱 실패 — layout 기본값 사용")

            logger.info("[Pipeline Render] 시작: post_id=%d render_style=%s", post_id, render_style)

            if render_style == "layout":
                from ai_worker.layout_renderer import render_layout_video
                video_path = render_layout_video(post, script)
            else:
                video_path = self._safe_render_video(post, audio_path, script.to_json())

            self._save_content(post, session, script, audio_path, video_path)
            logger.info("[Pipeline Render] ✓ 영상 완료: %s", video_path)

            # 썸네일 생성
            try:
                images = post.images if isinstance(post.images, list) else []
                thumb_path = get_thumbnail_path(post_id)
                thumb_style = _MOOD_TO_STYLE.get(script.mood, "dramatic")
                generate_thumbnail(script.hook, images, thumb_path, style=thumb_style)
                content = session.query(Content).filter_by(post_id=post_id).first()
                if content is not None:
                    upload_meta = dict(content.upload_meta or {})
                    upload_meta["thumbnail_path"] = str(thumb_path)
                    content.upload_meta = upload_meta
                    session.flush()
            except Exception:
                logger.warning("썸네일 생성 실패 (비치명적)", exc_info=True)

            post.status = PostStatus.PREVIEW_RENDERED
            session.commit()
            logger.info("[Pipeline Render] ✓ 완료: post_id=%d → PREVIEW_RENDERED", post_id)

        return video_path


# ===========================================================================
# 편의 함수
# ===========================================================================

async def process(post: Post, session: Session) -> None:
    """
    게시글 처리 (하위 호환성 유지)

    Args:
        post: 처리할 게시글
        session: DB 세션

    Raises:
        Exception: 처리 실패 시
    """
    processor = RobustProcessor()
    success = await processor.process_with_retry(post, session)
    if not success:
        raise RuntimeError(f"Post {post.id} processing failed after retries")
