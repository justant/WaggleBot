"""편집실 비동기 작업 — LLM 대본 생성 & TTS 미리듣기 백그라운드 워커.

hd_render.py와 동일한 패턴:
  - 모듈 레벨 딕셔너리를 공유 상태로 사용 (프로세스 내 Streamlit 재런 간 유지)
  - post_id 별로 작업 상태를 추적
"""
import logging
import time as _time
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# post_id → {"status": "running"|"done"|"error", ...}
_llm_tasks: dict[int, dict] = {}
_tts_tasks: dict[int, dict] = {}
_lock = threading.Lock()

# 태스크 생성 시각 기록 (post_id → timestamp)
_llm_task_created_at: dict[int, float] = {}
_tts_task_created_at: dict[int, float] = {}

# 완료/오류 태스크 TTL (초) — 5분 후 자동 삭제
_TASK_TTL_SECONDS = 300

# 실행 중 태스크 타임아웃 (초) — 30분 초과 시 error 전환
_TASK_TIMEOUT_SECONDS = 1800


def _auto_save_to_db(post_id: int, script: "ScriptData") -> None:
    """LLM 완료 결과를 Content.summary_text에 즉시 저장."""
    try:
        from db.models import Content
        from db.session import SessionLocal
        with SessionLocal() as session:
            content = session.query(Content).filter(Content.post_id == post_id).first()
            if content is None:
                content = Content(post_id=post_id)
                session.add(content)
            content.summary_text = script.to_json()
            session.commit()
        log.info("LLM 결과 DB 자동저장 완료: post_id=%d", post_id)
    except Exception:
        log.exception("LLM 결과 DB 자동저장 실패: post_id=%d", post_id)


def _gc_tasks() -> None:
    """오래된 완료/오류 태스크를 메모리에서 정리. 30분 초과 running은 error 전환."""
    _now = _time.time()
    for _pid in list(_llm_task_created_at):
        _elapsed = _now - _llm_task_created_at[_pid]
        _status = _llm_tasks.get(_pid, {}).get("status")
        if _status in ("done", "error") and _elapsed > _TASK_TTL_SECONDS:
            _llm_tasks.pop(_pid, None)
            _llm_task_created_at.pop(_pid, None)
        elif _status == "running" and _elapsed > _TASK_TIMEOUT_SECONDS:
            _llm_tasks[_pid] = {"status": "error", "error": "30분 타임아웃 초과"}
            log.warning("LLM 태스크 타임아웃: post_id=%d (%.0f초)", _pid, _elapsed)
    for _pid in list(_tts_task_created_at):
        _elapsed = _now - _tts_task_created_at[_pid]
        _status = _tts_tasks.get(_pid, {}).get("status")
        if _status in ("done", "error") and _elapsed > _TASK_TTL_SECONDS:
            _tts_tasks.pop(_pid, None)
            _tts_task_created_at.pop(_pid, None)
        elif _status == "running" and _elapsed > _TASK_TIMEOUT_SECONDS:
            _tts_tasks[_pid] = {"status": "error", "error": "30분 타임아웃 초과"}
            log.warning("TTS 태스크 타임아웃: post_id=%d (%.0f초)", _pid, _elapsed)


# ---------------------------------------------------------------------------
# LLM 대본 생성
# ---------------------------------------------------------------------------

def get_llm_task(post_id: int) -> dict | None:
    """LLM 작업 상태 조회. 30분 초과 running은 즉시 error 전환."""
    task = _llm_tasks.get(post_id)
    if (
        task is not None
        and task.get("status") == "running"
        and post_id in _llm_task_created_at
        and _time.time() - _llm_task_created_at[post_id] > _TASK_TIMEOUT_SECONDS
    ):
        task = {"status": "error", "error": "30분 타임아웃 초과"}
        _llm_tasks[post_id] = task
        log.warning("LLM 태스크 조회 시 타임아웃 감지: post_id=%d", post_id)
    return task


def clear_llm_task(post_id: int) -> None:
    """LLM 작업 상태 제거."""
    _llm_tasks.pop(post_id, None)


def submit_llm_task(
    post_id: int,
    *,
    title: str,
    body: str,
    comments: list[str],
    model: str | None,
    extra_instructions: str | None,
    call_type: str,
) -> bool:
    """LLM 대본 생성 작업을 백그라운드 스레드로 제출.

    Returns:
        True  — 신규 제출 성공
        False — 이미 실행 중 (중복 제출 차단)
    """
    _gc_tasks()  # 오래된 완료/오류 태스크 정리

    with _lock:
        existing = _llm_tasks.get(post_id)
        if existing and existing.get("status") == "running":
            return False
        _llm_tasks[post_id] = {"status": "running"}
        _llm_task_created_at[post_id] = _time.time()

    def _run() -> None:
        try:
            from ai_worker.llm.client import generate_script
            result = generate_script(
                title=title,
                body=body,
                comments=comments,
                model=model,
                extra_instructions=extra_instructions,
                post_id=post_id,
                call_type=call_type,
            )
            _llm_tasks[post_id] = {"status": "done", "result": result}
            log.info("LLM 대본 생성 완료: post_id=%d", post_id)
            _auto_save_to_db(post_id, result)
        except Exception as exc:
            log.exception("LLM 대본 생성 실패: post_id=%d", post_id)
            _llm_tasks[post_id] = {"status": "error", "error": str(exc)}

    threading.Thread(target=_run, daemon=True, name=f"llm-gen-{post_id}").start()
    return True


# ---------------------------------------------------------------------------
# TTS 미리듣기
# ---------------------------------------------------------------------------

def get_tts_task(post_id: int) -> dict | None:
    """TTS 작업 상태 조회."""
    return _tts_tasks.get(post_id)


def clear_tts_task(post_id: int) -> None:
    """TTS 작업 상태 제거."""
    _tts_tasks.pop(post_id, None)


def submit_tts_task(
    post_id: int,
    *,
    text: str,
    engine_name: str,
    voice: str,
    output_path: Path,
) -> bool:
    """TTS 미리듣기 생성 작업을 백그라운드 스레드로 제출.

    Returns:
        True  — 신규 제출 성공
        False — 이미 실행 중 (중복 제출 차단)
    """
    _gc_tasks()  # 오래된 완료/오류 태스크 정리

    with _lock:
        existing = _tts_tasks.get(post_id)
        if existing and existing.get("status") == "running":
            return False
        _tts_tasks[post_id] = {"status": "running"}
        _tts_task_created_at[post_id] = _time.time()

    def _run() -> None:
        try:
            import asyncio
            from ai_worker.tts import get_tts_engine
            output_path.parent.mkdir(parents=True, exist_ok=True)
            tts_engine = get_tts_engine(engine_name)
            asyncio.run(tts_engine.synthesize(text, voice, output_path))
            _tts_tasks[post_id] = {"status": "done", "path": str(output_path)}
            log.info("TTS 미리듣기 완료: post_id=%d", post_id)
        except Exception as exc:
            log.exception("TTS 미리듣기 실패: post_id=%d", post_id)
            _tts_tasks[post_id] = {"status": "error", "error": str(exc)}

    threading.Thread(target=_run, daemon=True, name=f"tts-prev-{post_id}").start()
    return True
