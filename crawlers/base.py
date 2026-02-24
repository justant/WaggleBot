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

from config.settings import (
    BLOCK_RETRY_BACKOFF,
    BLOCK_RETRY_BASE_DELAY,
    BLOCK_RETRY_MAX,
    BLOCK_RETRY_MAX_DELAY,
    BROWSER_PROFILES,
    REQUEST_HEADERS,
    REQUEST_TIMEOUT,
)
from db.models import Post, Comment, PostStatus

log = logging.getLogger(__name__)

T = TypeVar("T")


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (requests.RequestException,),
    skip_on_status: tuple[int, ...] = (),
) -> Callable:
    """HTTP 요청 재시도 데코레이터.

    Args:
        max_attempts: 최대 시도 횟수
        delay: 첫 대기 시간 (초)
        backoff: 대기 시간 배수
        exceptions: 재시도할 예외 타입
        skip_on_status: 재시도 없이 즉시 raise할 HTTP 상태코드 (봇 차단 등)
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
                    # 봇 차단 등 즉시 포기할 상태코드는 재시도 없이 raise
                    if (
                        skip_on_status
                        and isinstance(e, requests.HTTPError)
                        and e.response is not None
                        and e.response.status_code in skip_on_status
                    ):
                        raise
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
        self._apply_browser_profile()

    # ------------------------------------------------------------------
    # Browser fingerprint helpers
    # ------------------------------------------------------------------

    def _apply_browser_profile(self) -> None:
        """세션 시작 시 1회 호출. UA + Client Hints 세트를 일관되게 적용."""
        profile = random.choice(BROWSER_PROFILES)
        self._session.headers["User-Agent"] = profile["user_agent"]
        # Chromium 계열만 Client Hints 전송 (Firefox/Safari는 키 자체가 없음)
        if "sec_ch_ua" in profile:
            self._session.headers["Sec-CH-UA"] = profile["sec_ch_ua"]
            self._session.headers["Sec-CH-UA-Mobile"] = profile["sec_ch_ua_mobile"]
            self._session.headers["Sec-CH-UA-Platform"] = profile["sec_ch_ua_platform"]
        else:
            # Firefox/Safari 프로필 → Client Hints 헤더 제거
            for key in ("Sec-CH-UA", "Sec-CH-UA-Mobile", "Sec-CH-UA-Platform"):
                self._session.headers.pop(key, None)

    @staticmethod
    def _human_delay(delay_range: tuple[float, float]) -> None:
        """사람처럼 불규칙한 대기."""
        time.sleep(random.uniform(delay_range[0], delay_range[1]))

    def _get_with_block_retry(
        self, url: str, max_retries: int = BLOCK_RETRY_MAX, **kwargs
    ) -> requests.Response:
        """429/430 봇 차단 시 지수적 대기 후 재시도. 일반 _get() 실패는 그대로 raise."""
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        current_delay = BLOCK_RETRY_BASE_DELAY

        for attempt in range(max_retries + 1):
            try:
                resp = self._session.get(url, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.HTTPError as e:
                if (
                    e.response is not None
                    and e.response.status_code in (429, 430)
                    and attempt < max_retries
                ):
                    # Retry-After 헤더 존중
                    retry_after = e.response.headers.get("Retry-After")
                    wait = (
                        min(float(retry_after), BLOCK_RETRY_MAX_DELAY)
                        if retry_after
                        else min(current_delay, BLOCK_RETRY_MAX_DELAY)
                    )
                    log.warning(
                        "봇 차단 %d — %s (재시도 %d/%d, %.0f초 대기)",
                        e.response.status_code, url,
                        attempt + 1, max_retries, wait,
                    )
                    time.sleep(wait)
                    current_delay *= BLOCK_RETRY_BACKOFF
                    # 재시도마다 브라우저 프로필 교체
                    self._apply_browser_profile()
                    continue
                raise

        # unreachable, but satisfy type checker
        raise requests.RequestException(f"Block retry exhausted for {url}")

    @retry(max_attempts=3, delay=1.0, skip_on_status=(429, 430))
    def _get(self, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        resp = self._session.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    @retry(max_attempts=3, delay=1.0)
    def _post(self, url: str, **kwargs) -> requests.Response:
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

        saved = 0
        skipped = 0
        for item in listings:
            origin_id = str(item["origin_id"])
            try:
                detail = self.parse_post(item["url"])
            except Exception:
                log.exception("Failed to parse %s", item["url"])
                skipped += 1
                continue

            try:
                self._upsert(session, origin_id, detail)
                session.commit()
                saved += 1
            except Exception as e:
                session.rollback()
                # 중복 키 등 제약 위반 — 해당 포스트만 건너뜀, 배치 계속 진행
                log.warning(
                    "[%s] upsert 건너뜀: origin_id=%s — %s",
                    self.site_code, origin_id, e,
                )
                skipped += 1

        log.info(
            "[%s] Crawl batch done: saved=%d, skipped=%d, total=%d",
            self.site_code, saved, skipped, len(listings),
        )

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
            # 신규 게시글: 본문 30자 미만이면 추론 불가 → 수집 제외
            content = detail.get("content") or ""
            if len(content) < 30:
                log.debug(
                    "Skip %s:%s — 본문 %d자 (30자 미만)",
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
