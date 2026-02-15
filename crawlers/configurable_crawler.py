"""
Configurable Crawler

YAML 설정만으로 새로운 사이트 크롤러를 추가할 수 있는 범용 크롤러
"""

import logging
import random
import re
import time
from typing import Optional, Dict, List

import requests
from bs4 import BeautifulSoup

from config.settings import REQUEST_HEADERS, REQUEST_TIMEOUT, USER_AGENTS
from crawlers.base import BaseCrawler

log = logging.getLogger(__name__)


class ConfigurableCrawler(BaseCrawler):
    """
    YAML 설정 기반 범용 크롤러

    config/sites.yaml 파일의 설정을 읽어서 자동으로 크롤링을 수행합니다.
    """

    def __init__(self, site_code: str, site_config: dict):
        """
        초기화

        Args:
            site_code: 사이트 코드 (예: 'nate_pann')
            site_config: YAML에서 읽은 사이트 설정
        """
        self.site_code = site_code
        self.config = site_config
        self.selectors = site_config.get('selectors', {})
        self.parsing = site_config.get('parsing', {})
        self.rate_limit = site_config.get('rate_limit', {})

        # HTTP 세션 초기화
        self._session = requests.Session()
        self._session.headers.update(REQUEST_HEADERS)

        log.info(
            "ConfigurableCrawler initialized: %s (%s)",
            site_code,
            site_config.get('description', 'No description')
        )

    def _rotate_ua(self):
        """User-Agent 로테이션"""
        if self.config.get('rotate_user_agent', False):
            self._session.headers["User-Agent"] = random.choice(USER_AGENTS)

    def _delay(self):
        """Rate Limiting 딜레이"""
        delay = self.rate_limit.get('delay_between_posts', 0.3)
        time.sleep(delay)

    def fetch_listing(self) -> List[dict]:
        """
        게시글 목록 가져오기

        Returns:
            게시글 목록 [{'origin_id', 'title', 'url'}]
        """
        listing_url = self.config.get('listing_url')
        if not listing_url:
            log.error("listing_url not configured for %s", self.site_code)
            return []

        max_pages = self.config.get('max_pages', 1)
        posts = []
        seen = set()

        for page in range(1, max_pages + 1):
            try:
                self._rotate_ua()

                # 페이지 URL (페이징 지원)
                page_url = listing_url
                if '{page}' in listing_url:
                    page_url = listing_url.format(page=page)

                # 요청
                resp = self._session.get(page_url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()

                # HTML 파싱
                soup = BeautifulSoup(resp.text, 'html.parser')

                # 게시글 목록 추출
                listing_items_selector = self.selectors.get('listing_items')
                if not listing_items_selector:
                    log.error("listing_items selector not configured")
                    continue

                items = soup.select(listing_items_selector)
                log.debug("Found %d items on page %d", len(items), page)

                for item in items:
                    # 링크 추출
                    link_selector = self.selectors.get('listing_link', 'a')
                    link = item.select_one(link_selector)
                    if not link:
                        continue

                    href = link.get('href', '')

                    # origin_id 추출
                    origin_id = self._extract_origin_id(href)
                    if not origin_id or origin_id in seen:
                        continue
                    seen.add(origin_id)

                    # 제목 추출
                    title_selector = self.selectors.get('listing_title', link_selector)
                    title_elem = item.select_one(title_selector) if title_selector != link_selector else link
                    title = title_elem.get('title') or title_elem.get_text(strip=True) if title_elem else ''

                    if not title:
                        continue

                    # 전체 URL 생성
                    post_url = self._build_post_url(origin_id, href)

                    posts.append({
                        'origin_id': origin_id,
                        'title': title,
                        'url': post_url
                    })

                log.info("Page %d: %d posts", page, len(posts))
                self._delay()

            except requests.RequestException:
                log.exception("Failed to fetch listing page %d", page)
                continue

        log.info("Total unique posts: %d", len(posts))
        return posts

    def parse_post(self, url: str) -> dict:
        """
        개별 게시글 파싱

        Args:
            url: 게시글 URL

        Returns:
            게시글 데이터
        """
        self._rotate_ua()

        try:
            resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException:
            log.exception("Failed to fetch post: %s", url)
            raise

        soup = BeautifulSoup(resp.text, 'html.parser')

        # 제목
        title = self._extract_text(soup, 'title')

        # 본문
        content = self._extract_text(soup, 'content')

        # 이미지
        images = self._extract_images(soup)

        # 통계
        stats = self._extract_stats(soup)

        # 댓글
        comments = self._extract_comments(soup)

        self._delay()

        return {
            'title': title,
            'content': content,
            'images': images or None,
            'stats': stats,
            'comments': comments
        }

    def _extract_origin_id(self, url: str) -> Optional[str]:
        """
        URL에서 origin_id 추출

        Args:
            url: URL 또는 경로

        Returns:
            origin_id 또는 None
        """
        pattern = self.parsing.get('origin_id_pattern', r'/(\d+)')
        match = re.search(pattern, url)
        return match.group(1) if match else None

    def _build_post_url(self, origin_id: str, href: str) -> str:
        """
        게시글 전체 URL 생성

        Args:
            origin_id: 게시글 ID
            href: 링크 (상대 또는 절대 경로)

        Returns:
            전체 URL
        """
        # 템플릿이 있으면 사용
        url_template = self.config.get('post_url_template')
        if url_template:
            return url_template.format(origin_id=origin_id)

        # 절대 URL이면 그대로 반환
        if href.startswith('http'):
            return href

        # 상대 URL이면 base와 결합
        listing_url = self.config.get('listing_url', '')
        base_url = '/'.join(listing_url.split('/')[:3])
        return base_url + href

    def _extract_text(self, soup: BeautifulSoup, field: str) -> str:
        """
        텍스트 추출

        Args:
            soup: BeautifulSoup 객체
            field: 필드명 ('title', 'content' 등)

        Returns:
            추출된 텍스트
        """
        selector = self.selectors.get(field)
        if not selector:
            return ''

        elem = soup.select_one(selector)
        if not elem:
            return ''

        return elem.get_text('\n', strip=True)

    def _extract_images(self, soup: BeautifulSoup) -> List[str]:
        """
        이미지 URL 추출

        Args:
            soup: BeautifulSoup 객체

        Returns:
            이미지 URL 리스트
        """
        selector = self.selectors.get('images')
        if not selector:
            return []

        images = []
        image_attrs = self.parsing.get('image_attrs', ['src'])

        for img in soup.select(selector):
            for attr in image_attrs:
                src = img.get(attr)
                if src and src.startswith('http'):
                    images.append(src)
                    break

        return images

    def _extract_stats(self, soup: BeautifulSoup) -> dict:
        """
        통계 추출 (조회수, 좋아요 등)

        Args:
            soup: BeautifulSoup 객체

        Returns:
            통계 딕셔너리
        """
        stats = {}

        # 조회수
        views_selector = self.selectors.get('views')
        if views_selector:
            views_text = self._extract_text(soup, 'views')
            stats['views'] = self._parse_int(views_text)

        # 좋아요
        likes_selector = self.selectors.get('likes')
        if likes_selector:
            likes_text = self._extract_text(soup, 'likes')
            stats['likes'] = self._parse_int(likes_text)

        # 댓글 수 (나중에 댓글 파싱에서 카운트)
        stats['comments_count'] = 0

        return stats

    def _extract_comments(self, soup: BeautifulSoup) -> List[dict]:
        """
        댓글 추출

        Args:
            soup: BeautifulSoup 객체

        Returns:
            댓글 리스트
        """
        comments_section_selector = self.selectors.get('comments_section')
        if not comments_section_selector:
            return []

        comments_section = soup.select_one(comments_section_selector)
        if not comments_section:
            return []

        comment_item_selector = self.selectors.get('comment_item', 'li')
        comment_items = comments_section.select(comment_item_selector)

        comments = []
        for item in comment_items:
            # 작성자
            author_selector = self.selectors.get('comment_author', '.author')
            author_elem = item.select_one(author_selector)
            author = author_elem.get_text(strip=True) if author_elem else ''

            # 내용
            content_selector = self.selectors.get('comment_content', '.content')
            content_elem = item.select_one(content_selector)
            content = content_elem.get_text(strip=True) if content_elem else ''

            # 좋아요
            likes_selector = self.selectors.get('comment_likes', '.likes')
            likes_elem = item.select_one(likes_selector)
            likes_text = likes_elem.get_text(strip=True) if likes_elem else '0'
            likes = self._parse_int(likes_text)

            if author and content:
                comments.append({
                    'author': author,
                    'content': content,
                    'likes': likes
                })

        return comments

    def _parse_int(self, text: str) -> int:
        """
        텍스트에서 정수 추출

        Args:
            text: 텍스트 (예: "조회 1,234")

        Returns:
            정수
        """
        if self.parsing.get('stats_extract_digits', False):
            digits = re.sub(r'[^\d]', '', text)
            return int(digits) if digits else 0
        else:
            try:
                return int(text)
            except ValueError:
                return 0
