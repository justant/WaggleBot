import hashlib
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from functools import wraps
from typing import Callable, TypeVar

import requests
from sqlalchemy.orm import Session

from config.settings import REQUEST_HEADERS, REQUEST_TIMEOUT, USER_AGENTS
from db.models import Post, Comment, PostStatus

log = logging.getLogger(__name__)

T = TypeVar("T")


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (requests.RequestException,),
) -> Callable:
    """HTTP 요청 재시도 데코레이터.

    Args:
        max_attempts: 최대 시도 횟수
        delay: 첫 대기 시간 (초)
        backoff: 대기 시간 배수
        exceptions: 재시도할 예외 타입
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            current_delay = delay
            last_exception: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        log.warning(
                            "%s 재시도 %d/%d (%.1f초 후): %s",
                            func.__name__, attempt, max_attempts,
                            current_delay, e,
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
            raise last_exception  # type: ignore[misc]
        return wrapper
    return decorator


class BaseCrawler(ABC):
    site_code: str = ""

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(REQUEST_HEADERS)

    def _rotate_ua(self) -> None:
        self._session.headers["User-Agent"] = random.choice(USER_AGENTS)

    @retry(max_attempts=3, delay=1.0)
    def _get(self, url: str, **kwargs) -> requests.Response:
        self._rotate_ua()
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        resp = self._session.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    @retry(max_attempts=3, delay=1.0)
    def _post(self, url: str, **kwargs) -> requests.Response:
        self._rotate_ua()
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        resp = self._session.post(url, **kwargs)
        resp.raise_for_status()
        return resp

    @staticmethod
    def _parse_int(s: str) -> int:
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0

    @staticmethod
    def _parse_stat(text: str, pattern: str) -> int:
        m = re.search(pattern, text)
        if not m:
            return 0
        return int(m.group(1).replace(",", ""))

    @staticmethod
    def _text(el) -> str:
        return el.get_text(strip=True) if el else ""

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

            try:
                with session.begin_nested():
                    self._upsert(session, origin_id, detail)
            except Exception as e:
                # 중복 키 등 제약 위반 — 해당 포스트만 건너뜀, 배치 계속 진행
                log.warning(
                    "[%s] upsert 건너뜀: origin_id=%s — %s",
                    self.site_code, origin_id, e,
                )

        session.commit()
        log.info("[%s] Crawl batch committed", self.site_code)

    @staticmethod
    def calculate_engagement_score(
        stats: dict, comments: list[dict], age_hours: float
    ) -> float:
        """
        시간 감쇠(time-decay) 적용 인기도 점수.
        최신 + 반응 좋은 게시글이 높은 점수.
        6시간 반감기: 24시간 후 원점수의 6.25% 수준으로 감소.
        """
        views             = float(stats.get("views",          0) or 0)
        likes             = float(stats.get("likes",          0) or 0)
        comment_count     = float(stats.get("comments_count", 0) or 0)
        top_comment_likes = sum(float(c.get("likes", 0) or 0) for c in (comments or [])[:5])

        raw_score = (
            views         * 0.1
            + likes       * 2.0
            + comment_count * 1.5
            + top_comment_likes * 0.5
        )

        decay = 0.5 ** (age_hours / 6.0)
        return round(raw_score * decay, 1)

    def _upsert(self, session: Session, origin_id: str, detail: dict):
        post = (
            session.query(Post)
            .filter_by(site_code=self.site_code, origin_id=origin_id)
            .first()
        )

        raw_stats = detail.get("stats") or {}
        comments  = detail.get("comments", [])
        now       = datetime.now(timezone.utc)

        if post:
            # 기존 게시글: created_at 기준 나이 계산
            created = post.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_hours = (now - created).total_seconds() / 3600.0

            score = self.calculate_engagement_score(raw_stats, comments, age_hours)
            post.stats = dict(raw_stats)          # 새 dict 할당 (JSON 변경 감지)
            post.engagement_score = score
            if detail.get("images"):
                post.images = detail["images"]
            log.debug(
                "Updated %s:%s (age=%.1fh score=%.1f)",
                self.site_code, origin_id, age_hours, score,
            )
        else:
            # 신규 게시글: 본문 10자 미만이면 추론 불가 → 수집 제외
            content = detail.get("content") or ""
            if len(content) < 10:
                log.debug(
                    "Skip %s:%s — 본문 %d자 (10자 미만)",
                    self.site_code, origin_id, len(content),
                )
                return

            # 방금 수집 → age=0, decay=1.0
            score = self.calculate_engagement_score(raw_stats, comments, age_hours=0.0)
            post = Post(
                site_code=self.site_code,
                origin_id=origin_id,
                title=detail["title"],
                content=detail.get("content"),
                images=detail.get("images"),
                stats=dict(raw_stats),
                engagement_score=score,
                status=PostStatus.COLLECTED,
            )
            session.add(post)
            session.flush()
            log.info(
                "New post: %s:%s — %s (score=%.1f)",
                self.site_code, origin_id, detail["title"], score,
            )

        self._sync_comments(session, post, detail.get("comments", []))

    def _sync_comments(self, session: Session, post: Post, raw_comments: list[dict]):
        existing = {c.content_hash: c for c in post.comments}
        seen_in_batch: set[str] = set()  # 이번 raw_comments 내 중복 방지

        for rc in raw_comments:
            chash = hashlib.sha256(
                f"{rc['author']}:{rc['content']}".encode()
            ).hexdigest()[:32]

            if chash in existing:
                # 기존 댓글: 공감수만 최신화
                existing[chash].likes = rc.get("likes", 0)
            elif chash in seen_in_batch:
                # 크롤링된 raw 데이터 내 중복 댓글 — 건너뜀
                log.debug(
                    "중복 댓글 건너뜀 (raw 중복): post_id=%s hash=%.8s…",
                    post.id, chash,
                )
            else:
                session.add(Comment(
                    post_id=post.id,
                    author=rc["author"],
                    content=rc["content"],
                    content_hash=chash,
                    likes=rc.get("likes", 0),
                ))
                seen_in_batch.add(chash)
