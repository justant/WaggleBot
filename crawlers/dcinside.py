import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler
from crawlers.plugin_manager import CrawlerRegistry

log = logging.getLogger(__name__)

BASE_URL = "https://gall.dcinside.com"
# 댓글은 모바일 AJAX API 사용 (PC 엔드포인트 대비 응답이 안정적)
COMMENT_API_URL = "https://m.dcinside.com/ajax/response-comment"


@CrawlerRegistry.register(
    "dcinside",
    description="디시인사이드 실시간 베스트·HIT 갤러리 크롤러",
    enabled=True,
)
class DcInsideCrawler(BaseCrawler):
    site_code = "dcinside"
    SECTIONS = [
        {"name": "실시간 베스트 (실베)", "url": "https://gall.dcinside.com/board/lists/?id=dcbest"},
        {"name": "HIT 갤러리 (힛갤)",   "url": "https://gall.dcinside.com/board/lists/?id=hit"},
    ]

    def __init__(self) -> None:
        super().__init__()
        self._session.headers["Referer"] = "https://www.dcinside.com/"

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def fetch_listing(self) -> list[dict]:
        posts: list[dict] = []
        seen: set[str] = set()

        for section in self.SECTIONS:
            try:
                resp = self._get(section["url"])
            except requests.RequestException:
                log.exception("Failed to fetch listing: %s", section["url"])
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            section_count = 0

            for row in self._iter_post_rows(soup):
                link = (
                    row.select_one("td.gall_tit a:first-child")
                    or row.select_one("a.newtxt")
                    or row.select_one("a[href*='/board/view/']")
                )
                if not link:
                    continue

                href = link.get("href", "")
                gall_id, post_no = self._parse_board_href(href)
                if not gall_id or not post_no:
                    continue

                origin_id = f"{gall_id}_{post_no}"
                if origin_id in seen:
                    continue
                seen.add(origin_id)

                title = self._clean_listing_title(link.get_text(strip=True))
                if not title:
                    continue

                url = BASE_URL + href if href.startswith("/") else href
                posts.append({"origin_id": origin_id, "title": title, "url": url})
                section_count += 1

            log.info("Section '%s': %d new posts", section["name"], section_count)

        log.info("Total unique posts from listing: %d", len(posts))
        return posts

    def _iter_post_rows(self, soup: BeautifulSoup):
        """테이블·리스트 양쪽 레이아웃을 처리, 공지·광고 제외."""
        # 테이블 기반 레이아웃 (일반 갤러리)
        rows = soup.select("table.gall_list tbody tr.us-post")
        if not rows:
            rows = soup.select("tr.ub-content")
        if rows:
            return (
                r for r in rows
                if "notice" not in " ".join(r.get("class", []))
                and "ad" not in " ".join(r.get("class", []))
            )

        # 리스트 기반 레이아웃 (실베·힛갤 등 일부 큐레이션 갤러리)
        all_li = soup.select("ul.gall-list li") or soup.select("ul li")
        return (li for li in all_li if li.find("a", href=re.compile(r"/board/view/")))

    @staticmethod
    def _parse_board_href(href: str) -> tuple[str, str]:
        """href에서 (id, no) 추출. 예: /board/view/?id=dcbest&no=123"""
        id_m = re.search(r"[?&]id=([^&#]+)", href)
        no_m = re.search(r"[?&]no=(\d+)", href)
        if id_m and no_m:
            return id_m.group(1), no_m.group(1)
        return "", ""

    @staticmethod
    def _clean_listing_title(raw: str) -> str:
        """갤러리 접두어·아이콘 텍스트 제거. 예: '[잡갤] 제목' → '제목'"""
        text = re.sub(r"^\[[^\]]{1,20}\]\s*", "", raw.strip())
        return text.strip()

    # ------------------------------------------------------------------
    # Post detail
    # ------------------------------------------------------------------

    def parse_post(self, url: str) -> dict:
        resp = self._get(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        # 제목
        title_el = (
            soup.select_one("span.title_subject")
            or soup.select_one("h4.title span")
            or soup.select_one("h3.title")
        )
        title = title_el.get_text(strip=True) if title_el else ""

        # 본문
        body_el = soup.select_one("div.writing_view_box")
        content = body_el.get_text("\n", strip=True) if body_el else ""

        # 이미지 (DCInside lazy load: data-lazy > data-src > data-lazy-src > src 순으로 확인)
        images: list[str] = []
        if body_el:
            for img in body_el.select("img"):
                src = (
                    img.get("data-lazy")       # DCInside 실제 이미지 URL
                    or img.get("data-src")
                    or img.get("data-lazy-src")
                    or img.get("src")
                    or ""
                )
                # 프로토콜 상대 URL 처리 (//dcimg...)
                if src.startswith("//"):
                    src = "https:" + src
                # 플레이스홀더 gif 및 비이미지 제외
                if (
                    src.startswith("http")
                    and not src.endswith("img.gif")
                    and "img.gif" not in src
                ):
                    images.append(src)

        # 통계
        page_text = soup.get_text()
        views = self._parse_stat(page_text, r"조회\s*([\d,]+)")
        recommend_el = (
            soup.select_one("p.up_num")
            or soup.select_one("span.vote_r_btn")
            or soup.select_one("em.up_num")
        )
        likes = (
            self._parse_int(recommend_el.get_text(strip=True))
            if recommend_el
            else self._parse_stat(page_text, r"추천\s*([\d,]+)")
        )

        # 댓글
        gall_id, post_no = self._parse_board_href(url)
        comments = self._fetch_comments(gall_id, post_no)
        comment_count = len(comments)
        m = re.search(r"댓글\s*[\(\[]?\s*(\d+)", page_text)
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
    # Comments (모바일 AJAX API)
    # ------------------------------------------------------------------

    def _fetch_comments(self, gall_id: str, post_no: str) -> list[dict]:
        if not gall_id or not post_no:
            return []

        try:
            resp = self._post(
                COMMENT_API_URL,
                data={
                    "id": gall_id,
                    "no": post_no,
                    "cpage": "1",
                    "managerskill": "",
                    "del_scope": "1",
                    "csort": "",
                },
                headers={"X-Requested-With": "XMLHttpRequest"},
            )
        except requests.RequestException:
            log.warning("Failed to fetch comments for %s/%s", gall_id, post_no)
            return []

        return self._parse_comments(BeautifulSoup(resp.text, "html.parser"))

    def _parse_comments(self, soup: BeautifulSoup) -> list[dict]:
        results: list[dict] = []

        # PC: li.ub-content, 모바일 AJAX: li.comment
        comment_items = soup.select("li.ub-content") or soup.select("li.comment")

        for li in comment_items:
            author_el = (
                li.select_one("span.nickname em")
                or li.select_one("a.nick")
                or li.select_one("span.nick")
                or li.select_one("[class*='nick']")
            )
            content_el = (
                li.select_one("span.usertxt_in")
                or li.select_one("p.usertxt_in")
                or li.select_one("p.txt")
                or li.select_one("[class*='usertxt']")
            )
            likes_el = (
                li.select_one("span.rcnt")
                or li.select_one("[class*='reco']")
                or li.select_one("[class*='vote']")
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
