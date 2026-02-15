import asyncio
import logging

from config.settings import AI_POLL_INTERVAL, MAX_RETRY_COUNT
from db.models import Post, Content, PostStatus
from db.session import SessionLocal, init_db

logger = logging.getLogger(__name__)


async def poll_once() -> bool:
    """
    APPROVED 상태 게시글 1개 처리

    Returns:
        처리할 게시글이 있었는지 여부
    """
    with SessionLocal() as session:
        post = (
            session.query(Post)
            .filter(Post.status == PostStatus.APPROVED)
            .order_by(Post.created_at.asc())
            .first()
        )
        if post is None:
            return False

        from ai_worker.processor import RobustProcessor

        # RobustProcessor가 내부적으로 재시도 및 에러 핸들링 처리
        processor = RobustProcessor()
        try:
            await processor.process_with_retry(post, session)
        except Exception:
            # RobustProcessor 내부에서 이미 에러 처리되었으므로 로그만 남김
            logger.exception("예상치 못한 에러: post_id=%d", post.id)
            session.rollback()

    return True


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
                session.commit()
                logger.warning("일부 플랫폼 업로드 실패: post_id=%d", post.id)
        except Exception:
            logger.exception("업로드 실패: post_id=%d", post.id)
            session.rollback()

    return True


async def _main_loop() -> None:
    while True:
        try:
            found = await poll_once()
        except Exception:
            logger.exception("폴링 루프 예외")
            found = False

        try:
            uploaded = await upload_once()
        except Exception:
            logger.exception("업로드 폴링 예외")
            uploaded = False

        if not found and not uploaded:
            await asyncio.sleep(AI_POLL_INTERVAL)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    init_db()
    logger.info("AI Worker 시작 (poll_interval=%ds)", AI_POLL_INTERVAL)
    asyncio.run(_main_loop())


if __name__ == "__main__":
    main()
