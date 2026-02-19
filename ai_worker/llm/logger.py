"""LLM 호출 이력 기록 모듈.

DB 저장 실패 시 logger.warning으로 fallback — 메인 파이프라인을 절대 중단시키지 않는다.

사용 예:
    with LLMCallTimer() as timer:
        raw = call_ollama(prompt)
    log_llm_call(
        call_type="generate_script",
        post_id=post.id,
        ...
        duration_ms=timer.elapsed_ms,
    )
"""
import logging
import time
from contextlib import contextmanager
from typing import Any

from db.models import LLMLog
from db.session import SessionLocal

logger = logging.getLogger(__name__)

# 저장 최대 길이 (TEXT 컬럼 64KB 안전 마진)
_MAX_TEXT_LEN = 60_000


class LLMCallTimer:
    """with 블록으로 사용하는 경과시간 측정기."""

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed_ms: int = 0

    def __enter__(self) -> "LLMCallTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)


def log_llm_call(
    *,
    call_type: str,
    post_id: int | None,
    model_name: str,
    prompt_text: str,
    raw_response: str,
    parsed_result: Any | None = None,
    strategy: str | None = None,
    image_count: int = 0,
    content_length: int = 0,
    success: bool = True,
    error_message: str | None = None,
    duration_ms: int | None = None,
) -> None:
    """LLM 호출 결과를 llm_logs 테이블에 기록한다.

    Args:
        call_type:      'generate_script' 또는 'chunk'
        post_id:        연결 게시글 ID (없으면 None)
        model_name:     Ollama 모델명
        prompt_text:    LLM에 전달한 전체 프롬프트
        raw_response:   Ollama 원시 응답
        parsed_result:  validate_and_fix 후 최종 dict (선택)
        strategy:       자원 전략 (img_heavy|balanced|text_heavy, 선택)
        image_count:    이미지 수
        content_length: 원문 글자 수
        success:        성공 여부
        error_message:  실패 시 에러 메시지
        duration_ms:    응답 소요 밀리초
    """
    try:
        # parsed_result가 dict/list가 아니면 JSON 저장 생략
        safe_parsed = parsed_result if isinstance(parsed_result, (dict, list)) else None

        with SessionLocal() as db:
            entry = LLMLog(
                post_id=post_id,
                call_type=call_type,
                model_name=model_name,
                strategy=strategy,
                image_count=image_count,
                content_length=content_length,
                prompt_text=prompt_text[:_MAX_TEXT_LEN] if prompt_text else None,
                raw_response=raw_response[:_MAX_TEXT_LEN] if raw_response else None,
                parsed_result=safe_parsed,
                success=success,
                error_message=error_message[:2000] if error_message else None,
                duration_ms=duration_ms,
            )
            db.add(entry)
            db.commit()
            logger.debug(
                "LLM 로그 저장: call_type=%s post_id=%s success=%s duration=%sms",
                call_type, post_id, success, duration_ms,
            )
    except Exception as exc:
        # DB 오류는 파이프라인을 멈추지 않음
        logger.warning(
            "LLM 로그 DB 저장 실패 (무시): %s | call_type=%s post_id=%s",
            exc, call_type, post_id,
        )
