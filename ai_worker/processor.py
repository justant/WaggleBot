"""
AI Worker Processor with Robust Error Handling

견고한 에러 핸들링 및 재시도 메커니즘
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ai_worker.gpu_manager import get_gpu_manager, ModelType
from ai_worker.llm import summarize
from ai_worker.tts import get_tts_engine
from ai_worker.video import render_video
from config.settings import MEDIA_DIR, load_pipeline_config, MAX_RETRY_COUNT
from db.models import Content, Post, PostStatus

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

                # ===== Step 1: LLM 요약 =====
                logger.info("[Step 1/3] LLM 요약 생성 중...")
                with self.gpu_manager.managed_inference(ModelType.LLM, "summarizer"):
                    summary_text = self._safe_generate_summary(post)
                logger.info("[Step 1/3] ✓ 요약 완료 (%d자)", len(summary_text))

                # ===== Step 2: TTS 생성 =====
                logger.info("[Step 2/3] TTS 음성 생성 중...")
                with self.gpu_manager.managed_inference(ModelType.TTS, "tts_engine"):
                    audio_path = await self._safe_generate_tts(summary_text, post.id)
                logger.info("[Step 2/3] ✓ 음성 완료: %s", audio_path)

                # ===== Step 3: 영상 렌더링 =====
                logger.info("[Step 3/3] 영상 렌더링 중...")
                video_path = self._safe_render_video(post, audio_path, summary_text)
                logger.info("[Step 3/3] ✓ 렌더링 완료: %s", video_path)

                # ===== Content 저장 =====
                self._save_content(post, session, summary_text, audio_path, video_path)

                # ===== 성공 처리 =====
                post.status = PostStatus.RENDERED
                session.commit()
                logger.info(
                    "✅ 처리 성공: post_id=%d → RENDERED (attempts=%d)",
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

    def _safe_generate_summary(self, post: Post) -> str:
        """
        안전하게 LLM 요약 생성

        Args:
            post: 게시글

        Returns:
            요약 텍스트

        Raises:
            Exception: LLM 에러
        """
        try:
            # 베스트 댓글 추출
            best_comments = sorted(post.comments, key=lambda c: c.likes, reverse=True)[:5]
            comment_texts = [f"{c.author}: {c.content[:100]}" for c in best_comments]

            # LLM 요약
            summary_text = summarize(
                title=post.title,
                body=post.content or "",
                comments=comment_texts,
                model=self.cfg.get("llm_model"),
            )

            # 유효성 검사
            if not summary_text or len(summary_text) < 10:
                raise ValueError("요약 텍스트가 너무 짧습니다")

            return summary_text

        except Exception as e:
            logger.exception("LLM 요약 실패")
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

            # TTS 생성
            await tts_engine.synthesize(text, voice_id, audio_path)

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

    def _safe_render_video(self, post: Post, audio_path: Path, summary_text: str) -> Path:
        """
        안전하게 영상 렌더링

        Args:
            post: 게시글
            audio_path: 음성 파일 경로
            summary_text: 요약 텍스트

        Returns:
            영상 파일 경로

        Raises:
            Exception: 렌더링 에러
        """
        try:
            video_path = render_video(post, audio_path, summary_text, self.cfg)

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
        summary_text: str,
        audio_path: Path,
        video_path: Path
    ):
        """
        Content 레코드 저장

        Args:
            post: 게시글
            session: DB 세션
            summary_text: 요약 텍스트
            audio_path: 음성 파일 경로
            video_path: 영상 파일 경로
        """
        content = session.query(Content).filter(Content.post_id == post.id).first()
        if content is None:
            content = Content(post_id=post.id)
            session.add(content)

        content.summary_text = summary_text
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
