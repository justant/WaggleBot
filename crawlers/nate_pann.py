import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from crawlers.base import BaseCrawler
from crawlers.plugin_manager import CrawlerRegistry

log = logging.getLogger(__name__)

POST_BASE = "https://pann.nate.com/talk/"


@CrawlerRegistry.register(
    'nate_pann',
    description='네이트판 인기글 크롤러',
    enabled=True
)
class NatePannCrawler(BaseCrawler):
    site_code = "nate_pann"
    SECTIONS = [
        {"name": "톡톡 베스트", "url": "https://pann.nate.com/talk/ranking"},
        {"name": "톡커들의 선택", "url": "https://pann.nate.com/talk/ranking/best"},
    ]

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def fetch_listing(self) -> list[dict]:
        posts = []
        seen = set()

        for section in self.SECTIONS:
            try:
                resp = self._get(section["url"])
            except requests.RequestException:
                log.exception("Failed to fetch listing: %s", section["url"])
                continue

            soup = BeautifulSoup(resp.text, "html.parser")

            for li in soup.select("div.cntList ul.post_wrap li"):
                link = li.select_one("dl dt h2 a")
                if not link:
                    continue

                href = link.get("href", "")
                match = re.search(r"/talk/(\d+)", href)
                if not match:
                    continue

                origin_id = match.group(1)
                if origin_id in seen:
                    continue
                seen.add(origin_id)

                title = link.get("title") or link.get_text(strip=True)
                if not title:
                    continue

                posts.append({
                    "origin_id": origin_id,
                    "title": title,
                    "url": POST_BASE + origin_id,
                })

            log.info("Section '%s': %d new posts", section["name"], len(posts))

        log.info("Total unique posts from listing: %d", len(posts))
        return posts

    # ------------------------------------------------------------------
    # Post detail
    # ------------------------------------------------------------------

    def parse_post(self, url: str) -> dict:
        resp = self._get(url)
        soup = BeautifulSoup(resp.text, "html.parser")

        title = self._text(soup.select_one("div.post-tit-info h1"))
        content_area = soup.select_one("div#contentArea")
        content = content_area.get_text("\n", strip=True) if content_area else ""

        images = []
        if content_area:
            for img in content_area.select("img"):
                src = img.get("src") or img.get("data-src") or ""
                if src and src.startswith("http"):
                    images.append(src)

        views_el = soup.select_one("div.post-tit-info div.info span.count")
        views = self._parse_int(self._text(views_el).replace("조회", ""))
        likes = self._parse_int(self._text(soup.select_one("div.btnbox.up span.count span")))
        comment_count = self._parse_int(
            self._text(soup.select_one("div#bepleDiv div.cmt_tit span.num strong"))
            or self._text(soup.select_one("div.cmt_tit strong"))
        )

        comments = self._parse_comments(soup)

        time.sleep(0.3)

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
    # Comments (inline in page HTML)
    # ------------------------------------------------------------------

    def _parse_comments(self, soup: BeautifulSoup) -> list[dict]:
        results = []

        best_block = soup.select_one("div#bepleDiv div.cmt_best")
        if not best_block:
            return results

        for item in best_block.select("dl.cmt_item"):
            author_el = item.select_one("span.nameui")
            body_el = item.select_one("dd.usertxt")
            likes_el = item.select_one("dd.n_good")

            if not author_el or not body_el:
                continue

            results.append({
                "author": author_el.get_text(strip=True),
                "content": body_el.get_text(strip=True),
                "likes": self._parse_int(self._text(likes_el)),
            })

        return results
