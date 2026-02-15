"""
Nate Tok (네이트 톡) Crawler

네이트 톡 베스트 게시글 크롤러
"""

import logging

from crawlers.base import BaseCrawler
from crawlers.plugin_manager import CrawlerRegistry

log = logging.getLogger(__name__)


@CrawlerRegistry.register(
    'nate_tok',
    description='네이트 톡 베스트 게시글 크롤러',
    enabled=False  # 기본값: 비활성화 (아직 구현 중)
)
class NateTokCrawler(BaseCrawler):
    """
    네이트 톡 크롤러

    Note:
        현재는 예시 구현입니다.
        실제 네이트 톡 사이트 구조에 맞춰 구현 필요.
    """

    site_code = "nate_tok"

    def fetch_listing(self) -> list[dict]:
        """
        게시글 목록 가져오기

        TODO: 실제 네이트 톡 사이트 크롤링 구현

        Returns:
            게시글 목록
        """
        log.warning("NateTokCrawler is not fully implemented yet")
        return []

    def parse_post(self, url: str) -> dict:
        """
        개별 게시글 파싱

        TODO: 실제 네이트 톡 게시글 파싱 구현

        Args:
            url: 게시글 URL

        Returns:
            게시글 데이터
        """
        log.warning("NateTokCrawler.parse_post is not implemented")
        return {
            'title': 'Example Title',
            'content': 'Example Content',
            'images': [],
            'stats': {'views': 0, 'likes': 0, 'comments_count': 0},
            'comments': []
        }
