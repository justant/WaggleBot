import logging
import random
import re
import time

import requests
from bs4 import BeautifulSoup

from config.settings import FMKOREA_SECTIONS, REQUEST_HEADERS, REQUEST_TIMEOUT, USER_AGENTS
from crawlers.base import BaseCrawler
from crawlers.plugin_manager import CrawlerRegistry

log = logging.getLogger(__name__)

BASE_URL = "https://www.fmkorea.com"
# XE(XpressEngine) 표준 댓글 AJAX 액션
COMMENT_API = f"{BASE_URL}/index.php"


@CrawlerRegistry.register(
    "fmkorea",
    description="에펨코리아 포텐 터짐 최신·화제순 크롤러",
    enabled=True,
)
class FMKoreaCrawler(BaseCrawler):
    site_code = "fmkorea"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update({
            **REQUEST_HEADERS,
            "Referer": BASE_URL + "/",
        })

    def _rotate_ua(self) -> None:
        self._session.headers["User-Agent"] = random.choice(USER_AGENTS)

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def fetch_listing(self) -> list[dict]:
        posts: list[dict] = []
        seen: set[str] = set()

        for section in FMKOREA_SECTIONS:
            self._rotate_ua()
            try:
                resp = self._session.get(section["url"], timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
            except requests.RequestException:
                log.exception("Failed to fetch listing: %s", section["url"])
                continue

            soup = BeautifulSoup(resp.content, "html.parser")
            section_count = 0

            # 최신순: /best/{숫자}  |  화제순: ?document_srl={숫자} 혼용 대응
            _href_pat = re.compile(r"/best/\d+|document_srl=\d+")
            for link in soup.find_all("a", href=_href_pat):
                href = link.get("href", "")
                # /best/숫자 경로 우선 추출, 없으면 document_srl 파라미터에서 추출
                m = re.search(r"/best/(\d+)", href) or re.search(r"document_srl=(\d+)", href)
                if not m:
                    continue

                origin_id = m.group(1)
                if origin_id in seen:
                    continue
                seen.add(origin_id)

                title = self._extract_listing_title(link)
                if not title:
                    continue

                url = BASE_URL + href if href.startswith("/") else href
                posts.append({"origin_id": origin_id, "title": title, "url": url})
                section_count += 1

            log.info("Section '%s': %d new posts", section["name"], section_count)
            time.sleep(2)  # 섹션 간 딜레이 — rate limit 방지

        log.info("Total unique posts from listing: %d", len(posts))
        return posts

    @staticmethod
    def _extract_listing_title(link) -> str:
        """링크 내 h3 또는 링크 텍스트에서 제목 추출. 댓글수 [N] 제거."""
        h3 = link.find("h3")
        raw = h3.get_text(strip=True) if h3 else link.get_text(strip=True)
        # 말미 [N] 형태 댓글수 제거
        return re.sub(r"\s*\[\d+\]\s*$", "", raw).strip()

    # ------------------------------------------------------------------
    # Post detail
    # ------------------------------------------------------------------

    def parse_post(self, url: str) -> dict:
        self._rotate_ua()
        resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.content, "html.parser")

        # 제목: h1 > span.np_18px_span 또는 h1 직접
        title_el = (
            soup.select_one("h1 span.np_18px_span")
            or soup.select_one("h1")
            or soup.select_one(".np_18px_span")
        )
        title = title_el.get_text(strip=True) if title_el else ""

        # 본문: div.xe_content (XE 표준) 또는 div.bd
        body_el = (
            soup.select_one("article.xe_content")
            or soup.select_one("div.xe_content")
            or soup.select_one("div.bd")
            or soup.select_one("div#content")
        )
        content = body_el.get_text("\n", strip=True) if body_el else ""

        # 이미지
        images: list[str] = []
        if body_el:
            for img in body_el.select("img"):
                src = (
                    img.get("src")
                    or img.get("data-src")
                    or img.get("data-lazy-src")
                    or ""
                )
                # 투명 플레이스홀더 제외
                if src and src.startswith("http") and "transparent" not in src:
                    images.append(src)

        # 통계: page 텍스트 + 버튼 요소 병행
        page_text = soup.get_text()
        views = self._parse_stat(page_text, r"조회\s*수?\s*([\d,]+)")
        recommend_el = (
            soup.select_one("a.vote_up em")
            or soup.select_one("span.vote_up_n")
            or soup.select_one("em.up_num")
            or soup.select_one("[class*='vote_up']")
        )
        likes = (
            self._parse_int(recommend_el.get_text(strip=True))
            if recommend_el
            else self._parse_stat(page_text, r"추천\s*수?\s*([\d,]+)")
        )

        # JS 변수에서 document_srl과 mid 추출
        doc_srl, mid = self._extract_js_vars(str(soup))

        # 댓글
        comments = self._fetch_comments(doc_srl, mid)
        comment_count = len(comments)
        cm = re.search(r"댓글\s*[\(\[]?\s*(\d+)", page_text)
        if cm:
            comment_count = max(comment_count, int(cm.group(1)))

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

    @staticmethod
    def _extract_js_vars(html: str) -> tuple[str, str]:
        """JS 변수 current_document_srl, current_mid 추출."""
        srl_m = re.search(r"current_document_srl\s*=\s*parseInt\(['\"]?(\d+)['\"]?\)", html)
        mid_m = re.search(r"current_mid\s*=\s*['\"]([^'\"]+)['\"]", html)
        srl = srl_m.group(1) if srl_m else ""
        mid = mid_m.group(1) if mid_m else "best"
        return srl, mid

    # ------------------------------------------------------------------
    # Comments (XE AJAX)
    # ------------------------------------------------------------------

    def _fetch_comments(self, document_srl: str, mid: str) -> list[dict]:
        if not document_srl:
            return []

        try:
            self._rotate_ua()
            resp = self._session.get(
                COMMENT_API,
                params={
                    "act": "dispBoardGetMoreCommentList",
                    "document_srl": document_srl,
                    "mid": mid,
                    "cpage": "1",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
        except requests.RequestException:
            log.warning("Failed to fetch comments for srl=%s", document_srl)
            return []

        return self._parse_comments(BeautifulSoup(resp.text, "html.parser"))

    def _parse_comments(self, soup: BeautifulSoup) -> list[dict]:
        results: list[dict] = []

        # XE 표준: ul.fdb_lst_ul > li.fdb_item
        # fmkorea 커스텀: ul.comment-list > li.item 등 가능
        candidates = (
            soup.select("li.fdb_item")
            or soup.select("li.comment_item")
            or soup.select("li.ub-content")
            or soup.select("li.item")
        )

        for li in candidates:
            author_el = (
                li.select_one("a.user_nick")
                or li.select_one("span.user_nick")
                or li.select_one(".fdb_itm_user a")
                or li.select_one("[class*='nick']")
            )
            content_el = (
                li.select_one("div.xe_content")
                or li.select_one("p.xe_content")
                or li.select_one("[class*='content']")
                or li.select_one("span.txt")
            )
            likes_el = (
                li.select_one("em.vote_up")
                or li.select_one("span.vote_up_n")
                or li.select_one("[class*='vote']")
                or li.select_one("[class*='reco']")
            )

            if not author_el or not content_el:
                continue

            author = author_el.get_text(strip=True)
            content = content_el.get_text(strip=True)
            if not author or not content:
                continue

            results.append({
                "author": author,
                "content": content,
                "likes": self._parse_int(likes_el.get_text(strip=True) if likes_el else "0"),
            })

        return results

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
