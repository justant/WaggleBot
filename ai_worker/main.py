import asyncio
import logging

from config.settings import AI_POLL_INTERVAL, MAX_RETRY_COUNT
from db.models import Post, Content, PostStatus
from db.session import SessionLocal, init_db

logger = logging.getLogger(__name__)


async def poll_once() -> bool:
    with SessionLocal() as session:
        post = (
            session.query(Post)
            .filter(Post.status == PostStatus.APPROVED)
            .order_by(Post.created_at.asc())
            .first()
        )
        if post is None:
            return False

        from ai_worker.processor import process

        try:
            await process(post, session)
        except Exception:
            logger.exception("처리 실패: post_id=%d", post.id)
            session.rollback()
            post = session.query(Post).get(post.id)
            if post and post.status == PostStatus.PROCESSING:
                post.retry_count = (post.retry_count or 0) + 1
                if post.retry_count >= MAX_RETRY_COUNT:
                    post.status = PostStatus.FAILED
                    logger.error(
                        "최대 재시도 초과 → FAILED: post_id=%d (retries=%d)",
                        post.id, post.retry_count,
                    )
                else:
                    post.status = PostStatus.APPROVED
                    logger.warning(
                        "재시도 대기: post_id=%d (retry=%d/%d)",
                        post.id, post.retry_count, MAX_RETRY_COUNT,
                    )
                session.commit()

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
