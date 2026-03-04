"""AI Worker 셧다운 이벤트 싱글톤.

모든 워커 루프가 이 모듈을 참조하여 SIGTERM/SIGINT 수신 여부를 확인한다.
"""

import asyncio
import logging

logger = logging.getLogger(__name__)

_shutdown_event: asyncio.Event | None = None


def get_shutdown_event() -> asyncio.Event:
    """현재 이벤트 루프에 바인딩된 셧다운 이벤트를 반환한다."""
    global _shutdown_event
    if _shutdown_event is None:
        _shutdown_event = asyncio.Event()
    return _shutdown_event


def is_shutting_down() -> bool:
    """셧다운 요청이 들어왔는지 확인한다."""
    if _shutdown_event is None:
        return False
    return _shutdown_event.is_set()


def request_shutdown() -> None:
    """셧다운을 요청한다. 모든 워커 루프가 현재 작업 완료 후 종료한다."""
    event = get_shutdown_event()
    if not event.is_set():
        logger.warning("🛑 Graceful shutdown 요청 수신")
        event.set()
