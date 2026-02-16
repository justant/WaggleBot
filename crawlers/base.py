import hashlib
import logging
from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from db.models import Post, Comment, PostStatus

log = logging.getLogger(__name__)


class BaseCrawler(ABC):
    site_code: str = ""

    @abstractmethod
    def fetch_listing(self) -> list[dict]:
        """Return a list of dicts with at least {origin_id, title, url}."""

    @abstractmethod
    def parse_post(self, url: str) -> dict:
        """Return {title, content, stats: {views, likes, comments_count}, comments: [...]}."""

    def run(self, session: Session):
        listings = self.fetch_listing()
        log.info("[%s] Found %d posts in listing", self.site_code, len(listings))

        for item in listings:
            origin_id = str(item["origin_id"])
            try:
                detail = self.parse_post(item["url"])
            except Exception:
                log.exception("Failed to parse %s", item["url"])
                continue

            self._upsert(session, origin_id, detail)

        session.commit()
        log.info("[%s] Crawl batch committed", self.site_code)

    @staticmethod
    def _compute_score(stats: dict, comments: list[dict]) -> float:
        """인기도 스코어 계산 (views, likes, comments, 베스트댓글 좋아요 가중 합산)"""
        views  = float(stats.get("views",          0) or 0)
        likes  = float(stats.get("likes",          0) or 0)
        cmts   = float(stats.get("comments_count", 0) or 0)
        top_lk = sum(float(c.get("likes", 0) or 0) for c in (comments or [])[:5])
        raw = views * 0.1 + likes * 2.0 + cmts * 1.5 + top_lk * 0.5
        return round(raw, 1)

    def _upsert(self, session: Session, origin_id: str, detail: dict):
        post = (
            session.query(Post)
            .filter_by(site_code=self.site_code, origin_id=origin_id)
            .first()
        )

        raw_stats = detail.get("stats") or {}
        score = self._compute_score(raw_stats, detail.get("comments", []))
        stats_with_score = {**raw_stats, "score": score}

        if post:
            post.stats = stats_with_score  # 새 dict 할당 (JSON 칼럼 변경 감지)
            if detail.get("images"):
                post.images = detail["images"]
            log.debug("Updated stats for %s:%s (score=%.1f)", self.site_code, origin_id, score)
        else:
            post = Post(
                site_code=self.site_code,
                origin_id=origin_id,
                title=detail["title"],
                content=detail.get("content"),
                images=detail.get("images"),
                stats=stats_with_score,
                status=PostStatus.COLLECTED,
            )
            session.add(post)
            session.flush()
            log.info("New post: %s:%s — %s (score=%.1f)", self.site_code, origin_id, detail["title"], score)

        self._sync_comments(session, post, detail.get("comments", []))

    def _sync_comments(self, session: Session, post: Post, raw_comments: list[dict]):
        existing = {c.content_hash: c for c in post.comments}

        for rc in raw_comments:
            chash = hashlib.sha256(
                f"{rc['author']}:{rc['content']}".encode()
            ).hexdigest()[:32]

            if chash in existing:
                existing[chash].likes = rc.get("likes", 0)
            else:
                session.add(Comment(
                    post_id=post.id,
                    author=rc["author"],
                    content=rc["content"],
                    content_hash=chash,
                    likes=rc.get("likes", 0),
                ))
