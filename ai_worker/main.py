import logging
import time

from config.settings import AI_POLL_INTERVAL
from db.models import Post, PostStatus
from db.session import SessionLocal, init_db

logger = logging.getLogger(__name__)


def poll_once() -> bool:
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
            process(post, session)
        except Exception:
            logger.exception("처리 실패: post_id=%d", post.id)
            session.rollback()
            post = session.query(Post).get(post.id)
            if post and post.status == PostStatus.PROCESSING:
                post.status = PostStatus.APPROVED
                session.commit()

    return True


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    init_db()
    logger.info("AI Worker 시작 (poll_interval=%ds)", AI_POLL_INTERVAL)

    while True:
        try:
            found = poll_once()
        except Exception:
            logger.exception("폴링 루프 예외")
            found = False

        if not found:
            time.sleep(AI_POLL_INTERVAL)


if __name__ == "__main__":
    main()
