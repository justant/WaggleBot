"""
Crawler Plugin Manager

크롤러 등록 및 관리 시스템
"""

import logging
from typing import Dict, Type, List

from crawlers.base import BaseCrawler

log = logging.getLogger(__name__)


class CrawlerRegistry:
    """크롤러 플러그인 레지스트리"""

    _crawlers: Dict[str, Type[BaseCrawler]] = {}
    _metadata: Dict[str, dict] = {}

    @classmethod
    def register(cls, site_code: str, **metadata):
        """크롤러 등록 데코레이터.

        Args:
            site_code: 사이트 코드 (예: 'nate_pann', 'bobaedream')
            **metadata: 추가 메타데이터 (description, enabled 등)

        Example:
            @CrawlerRegistry.register('nate_pann', description='네이트판 크롤러')
            class NatePannCrawler(BaseCrawler):
                pass
        """
        def decorator(crawler_class: Type[BaseCrawler]):
            if not issubclass(crawler_class, BaseCrawler):
                raise TypeError(
                    f"{crawler_class.__name__} must inherit from BaseCrawler"
                )

            cls._crawlers[site_code] = crawler_class
            cls._metadata[site_code] = {
                'class_name': crawler_class.__name__,
                'module': crawler_class.__module__,
                'description': metadata.get('description', ''),
                'enabled': metadata.get('enabled', True),
                **metadata
            }

            log.debug("Registered crawler: %s -> %s", site_code, crawler_class.__name__)
            return crawler_class

        return decorator

    @classmethod
    def get_crawler(cls, site_code: str) -> BaseCrawler:
        """사이트 코드로 크롤러 인스턴스 반환.

        Raises:
            ValueError: 등록되지 않은 사이트 코드
        """
        if site_code not in cls._crawlers:
            available = ", ".join(cls._crawlers.keys())
            raise ValueError(
                f"Unknown site code: '{site_code}'. Available: {available}"
            )
        return cls._crawlers[site_code]()

    @classmethod
    def list_crawlers(cls) -> List[dict]:
        """등록된 모든 크롤러 목록 반환."""
        result = []
        for site_code, crawler_class in cls._crawlers.items():
            metadata = cls._metadata.get(site_code, {})
            result.append({
                'site_code': site_code,
                'class_name': crawler_class.__name__,
                'module': crawler_class.__module__,
                'description': metadata.get('description', ''),
                'enabled': metadata.get('enabled', True),
            })
        return result

    @classmethod
    def get_enabled_crawlers(cls) -> List[str]:
        """활성화된 크롤러 코드 목록 반환."""
        return [
            site_code for site_code, meta in cls._metadata.items()
            if meta.get('enabled', True)
        ]

    @classmethod
    def is_registered(cls, site_code: str) -> bool:
        """사이트 코드가 등록되어 있는지 확인."""
        return site_code in cls._crawlers


# ===========================================================================
# 편의 함수
# ===========================================================================

def get_crawler(site_code: str) -> BaseCrawler:
    """크롤러 인스턴스 반환 (편의 함수)."""
    return CrawlerRegistry.get_crawler(site_code)


def list_crawlers() -> List[dict]:
    """등록된 크롤러 목록 반환 (편의 함수)."""
    return CrawlerRegistry.list_crawlers()
