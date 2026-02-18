"""AI Worker 메인 루프 — 파이프라인 병렬 처리.

구조:
  _llm_tts_worker  : APPROVED 폴링 → LLM+TTS (CUDA) → render_queue에 적재
  _render_worker   : render_queue 소비 → 프리뷰 렌더링 (CPU) → PREVIEW_RENDERED

두 워커가 asyncio.gather로 동시 실행되므로
Post A가 CPU로 렌더링되는 동안 Post B는 GPU로 LLM+TTS를 처리할 수 있다.
"""
import asyncio
import logging

from config.settings import AI_POLL_INTERVAL
from db.models import Post, Content, PostStatus
from db.session import SessionLocal, init_db

logger = logging.getLogger(__name__)


def _mark_post_failed(post_id: int) -> None:
    """예외 발생 시 post를 FAILED로 안전하게 마킹한다."""
    try:
        with SessionLocal() as session:
            post = session.query(Post).filter_by(id=post_id).first()
            if post is not None and post.status not in (
                PostStatus.PREVIEW_RENDERED, PostStatus.RENDERED, PostStatus.UPLOADED
            ):
                post.status = PostStatus.FAILED
                session.commit()
    except Exception:
        logger.exception("FAILED 마킹 실패: post_id=%d", post_id)


# CUDA 리소스를 직렬화하는 세마포어 (LLM + TTS 는 순차 실행)
_cuda_sem: asyncio.Semaphore | None = None


def _get_cuda_sem() -> asyncio.Semaphore:
    global _cuda_sem
    if _cuda_sem is None:
        _cuda_sem = asyncio.Semaphore(1)
    return _cuda_sem


# ---------------------------------------------------------------------------
# 파이프라인 워커
# ---------------------------------------------------------------------------

async def _llm_tts_worker(render_queue: asyncio.Queue) -> None:
    """APPROVED 게시글을 폴링해 LLM+TTS 처리 후 render_queue에 적재."""
    from ai_worker.processor import RobustProcessor
    processor = RobustProcessor()
    cuda_sem = _get_cuda_sem()

    while True:
        post_id: int | None = None
        with SessionLocal() as session:
            post = (
                session.query(Post)
                .filter(Post.status == PostStatus.APPROVED)
                .order_by(Post.created_at.asc())
                .first()
            )
            if post is not None:
                post_id = post.id

        if post_id is None:
            await asyncio.sleep(AI_POLL_INTERVAL)
            continue

        async with cuda_sem:
            try:
                script, audio_path = await processor.llm_tts_stage(post_id)
                await render_queue.put((post_id, script, audio_path))
                logger.info("LLM+TTS 완료, 렌더 큐 적재: post_id=%d (큐 크기=%d)",
                            post_id, render_queue.qsize())
            except Exception:
                logger.exception("LLM+TTS 실패: post_id=%d", post_id)
                _mark_post_failed(post_id)
                await asyncio.sleep(5)


async def _render_worker(render_queue: asyncio.Queue) -> None:
    """render_queue에서 꺼내 프리뷰 렌더링 (CPU libx264, GPU 점유 없음)."""
    from ai_worker.processor import RobustProcessor
    processor = RobustProcessor()

    while True:
        post_id, script, audio_path = await render_queue.get()
        try:
            # render_stage는 동기 CPU 작업 — event loop를 블록하지 않도록 executor 사용
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None, processor.render_stage, post_id, script, audio_path
            )
        except Exception:
            logger.exception("렌더링 실패: post_id=%d", post_id)
            _mark_post_failed(post_id)
        finally:
            render_queue.task_done()


# ---------------------------------------------------------------------------
# 업로드 워커 (기존 동작 유지)
# ---------------------------------------------------------------------------

async def upload_once() -> bool:
    """RENDERED 상태 포스트를 찾아 업로드 실행."""
    from uploaders.uploader import upload_post

    with SessionLocal() as session:
        post = (
            session.query(Post)
            .filter(Post.status == PostStatus.RENDERED)
            .order_by(Post.created_at.asc())
            .first()
        )
        if post is None:
            return False

        content = session.query(Content).filter_by(post_id=post.id).first()
        if content is None:
            logger.error("Content 없음: post_id=%d", post.id)
            return False

        try:
            success = upload_post(post, content, session)
            if success:
                post.status = PostStatus.UPLOADED
                session.commit()
                logger.info("업로드 완료: post_id=%d", post.id)
            else:
                post.status = PostStatus.FAILED
                session.commit()
                logger.warning("업로드 실패, FAILED 처리: post_id=%d", post.id)
        except Exception:
            logger.exception("업로드 실패: post_id=%d", post.id)
            session.rollback()

    return True


async def _upload_loop() -> None:
    """RENDERED 게시글을 주기적으로 업로드."""
    while True:
        try:
            found = await upload_once()
        except Exception:
            logger.exception("업로드 루프 예외")
            found = False
        await asyncio.sleep(AI_POLL_INTERVAL if not found else 1)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

async def _main_loop() -> None:
    render_queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    await asyncio.gather(
        _llm_tts_worker(render_queue),
        _render_worker(render_queue),
        _upload_loop(),
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    init_db()
    logger.info("AI Worker 시작 (pipeline 모드, poll_interval=%ds)", AI_POLL_INTERVAL)
    asyncio.run(_main_loop())


if __name__ == "__main__":
    main()
