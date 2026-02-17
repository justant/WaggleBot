import logging
import random
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

from config.settings import BOBAEDREAM_SECTIONS, REQUEST_HEADERS, REQUEST_TIMEOUT, USER_AGENTS
from crawlers.base import BaseCrawler
from crawlers.plugin_manager import CrawlerRegistry

log = logging.getLogger(__name__)

BASE_URL = "https://m.bobaedream.co.kr"


@CrawlerRegistry.register(
    "bobaedream",
    description="보배드림 베스트 게시글 크롤러",
    enabled=True,
)
class BobaedrreamCrawler(BaseCrawler):
    site_code = "bobaedream"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(REQUEST_HEADERS)

    def _rotate_ua(self) -> None:
        self._session.headers["User-Agent"] = random.choice(USER_AGENTS)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def fetch_listing(self) -> list[dict]:
        posts: list[dict] = []
        seen: set[str] = set()

        for section in BOBAEDREAM_SECTIONS:
            self._rotate_ua()
            try:
                resp = self._session.get(section["url"], timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException:
                log.exception("Failed to fetch listing: %s", section["url"])
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            section_count = 0

            for li in soup.select("ul li"):
                link = li.select_one("a[href*='/board/bbs_view/']")
                if not link:
                    continue

                href = link.get("href", "")
                match = re.search(r"/board/bbs_view/(\w+)/(\d+)/", href)
                if not match:
                    continue

                board_code = match.group(1)
                post_num = match.group(2)
                origin_id = f"{board_code}_{post_num}"

                if origin_id in seen:
                    continue
                seen.add(origin_id)

                title = self._extract_title(link.get_text(separator=" ", strip=True))
                if not title:
                    continue

                url = BASE_URL + href if href.startswith("/") else href
                posts.append({"origin_id": origin_id, "title": title, "url": url})
                section_count += 1

            log.info("Section '%s': %d new posts", section["name"], section_count)

        log.info("Total unique posts from listing: %d", len(posts))
        return posts

    # ------------------------------------------------------------------
    # Post detail
    # ------------------------------------------------------------------

    def parse_post(self, url: str) -> dict:
        self._rotate_ua()
        resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title_el = soup.select_one("h3")
        title = title_el.get_text(strip=True) if title_el else ""

        body_el = soup.select_one("div#body_frame")
        content = body_el.get_text("\n", strip=True) if body_el else ""

        images: list[str] = []
        if body_el:
            for img in body_el.select("img"):
                src = img.get("src") or img.get("data-src") or ""
                if src and src.startswith("http"):
                    images.append(src)

        page_text = soup.get_text()
        views = self._parse_stat(page_text, r"조회\s+([\d,]+)")
        likes = self._parse_stat(page_text, r"추천\s+([\d,]+)")

        comments = self._fetch_comments(soup, url)
        comment_count = len(comments)
        # 페이지에 댓글 수가 명시된 경우 우선 사용
        m = re.search(r"댓글\s*[\(\[]?\s*(\d+)\s*[\)\]]?", page_text)
        if m:
            comment_count = max(comment_count, int(m.group(1)))

        time.sleep(0.5)

        return {
            "title": title,
            "content": content,
            "images": images or None,
            "stats": {
                "views": views,
                "likes": likes,
                "comments_count": comment_count,
            },
            "comments": comments,
        }

    # ------------------------------------------------------------------
    # Comments (AJAX)
    # ------------------------------------------------------------------

    def _fetch_comments(self, soup: BeautifulSoup, post_url: str) -> list[dict]:
        """JS comment_call() 인자를 파싱해 댓글 AJAX 엔드포인트를 호출."""
        match = re.search(
            r"comment_call\(\s*'([^']+)'\s*,\s*'([^']+)'\s*,\s*'(\d+)'",
            str(soup),
        )
        if not match:
            log.debug("comment_call not found in %s", post_url)
            return []

        table = match.group(1)    # e.g. uni_cmt_2602
        board = match.group(2)    # e.g. freeb
        post_id = match.group(3)  # e.g. 3372254

        comment_url = (
            f"{BASE_URL}/board/comment_call/"
            f"{table}/{board}/{post_id}/{board}/{post_id}"
        )

        try:
            self._rotate_ua()
            resp = self._session.get(comment_url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException:
            log.warning("Failed to fetch comments from %s", comment_url)
            return []

        return self._parse_comments(BeautifulSoup(resp.text, "html.parser"))

    def _parse_comments(self, soup: BeautifulSoup) -> list[dict]:
        results: list[dict] = []

        for li in soup.select("li"):
            author_el = (
                li.select_one("span.nick")
                or li.select_one("span.author")
                or li.select_one("[class*='name']")
            )
            content_el = (
                li.select_one("p.txt")
                or li.select_one("p.content")
                or li.select_one("span.txt")
                or li.select_one("[class*='content']")
            )
            likes_el = (
                li.select_one("[class*='reco']")
                or li.select_one("span.likes")
                or li.select_one("[class*='good']")
            )

            if not author_el or not content_el:
                continue

            author = author_el.get_text(strip=True)
            content = content_el.get_text(strip=True)
            if not author or not content:
                continue

            likes_text = likes_el.get_text(strip=True) if likes_el else "0"
            results.append({
                "author": author,
                "content": content,
                "likes": self._parse_int(likes_text),
            })

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title(raw: str) -> str:
        """링크 텍스트에서 제목만 추출. 날짜·작성자·통계 제거."""
        # [이미지], [동영상] 등 미디어 태그 제거
        text = re.sub(r"\[(?:이미지|동영상|캡처|영상|링크|사진)\]", "", raw)
        # 날짜(MM/DD 또는 YY.MM.DD) 이후는 작성자·통계 — 잘라냄
        text = re.split(r"\s+\d{2}[\./]\d{2}", text)[0]
        return text.strip()

    @staticmethod
    def _parse_stat(text: str, pattern: str) -> int:
        m = re.search(pattern, text)
        if not m:
            return 0
        return int(m.group(1).replace(",", ""))

    @staticmethod
    def _parse_int(s: str) -> int:
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else 0
