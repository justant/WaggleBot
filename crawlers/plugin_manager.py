"""
Crawler Plugin Manager

동적 크롤러 등록 및 관리 시스템
"""

import importlib
import inspect
import logging
from pathlib import Path
from typing import Dict, Type, List, Optional

from crawlers.base import BaseCrawler

log = logging.getLogger(__name__)


class CrawlerRegistry:
    """크롤러 플러그인 레지스트리"""

    _crawlers: Dict[str, Type[BaseCrawler]] = {}
    _metadata: Dict[str, dict] = {}

    @classmethod
    def register(cls, site_code: str, **metadata):
        """
        크롤러 등록 데코레이터

        Args:
            site_code: 사이트 코드 (예: 'nate_pann', 'nate_tok')
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

            log.debug(
                "Registered crawler: %s -> %s",
                site_code,
                crawler_class.__name__
            )
            return crawler_class

        return decorator

    @classmethod
    def get_crawler(cls, site_code: str) -> BaseCrawler:
        """
        사이트 코드로 크롤러 인스턴스 반환

        Args:
            site_code: 사이트 코드

        Returns:
            크롤러 인스턴스

        Raises:
            ValueError: 등록되지 않은 사이트 코드
        """
        if site_code not in cls._crawlers:
            available = ", ".join(cls._crawlers.keys())
            raise ValueError(
                f"Unknown site code: '{site_code}'. "
                f"Available: {available}"
            )

        crawler_class = cls._crawlers[site_code]
        return crawler_class()

    @classmethod
    def list_crawlers(cls) -> List[dict]:
        """
        등록된 모든 크롤러 목록 반환

        Returns:
            크롤러 정보 리스트
        """
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
        """
        활성화된 크롤러 코드 목록 반환

        Returns:
            활성화된 사이트 코드 리스트
        """
        enabled = []
        for site_code, metadata in cls._metadata.items():
            if metadata.get('enabled', True):
                enabled.append(site_code)
        return enabled

    @classmethod
    def is_registered(cls, site_code: str) -> bool:
        """
        사이트 코드가 등록되어 있는지 확인

        Args:
            site_code: 사이트 코드

        Returns:
            등록 여부
        """
        return site_code in cls._crawlers

    @classmethod
    def unregister(cls, site_code: str) -> bool:
        """
        크롤러 등록 해제

        Args:
            site_code: 사이트 코드

        Returns:
            해제 성공 여부
        """
        if site_code in cls._crawlers:
            del cls._crawlers[site_code]
            if site_code in cls._metadata:
                del cls._metadata[site_code]
            log.info("Unregistered crawler: %s", site_code)
            return True
        return False

    @classmethod
    def auto_discover(cls, package_name: str = 'crawlers') -> int:
        """
        지정된 패키지에서 크롤러 자동 발견

        Args:
            package_name: 패키지 이름 (기본: 'crawlers')

        Returns:
            발견된 크롤러 수
        """
        discovered = 0

        try:
            # 패키지 디렉토리 탐색
            package_path = Path(__file__).parent
            log.info("Auto-discovering crawlers in: %s", package_path)

            for file_path in package_path.glob("*.py"):
                # __init__.py, base.py, plugin_manager.py 제외
                if file_path.stem in ['__init__', 'base', 'plugin_manager']:
                    continue

                module_name = f"{package_name}.{file_path.stem}"

                try:
                    # 모듈 임포트
                    module = importlib.import_module(module_name)

                    # 모듈 내 클래스 탐색
                    for name, obj in inspect.getmembers(module, inspect.isclass):
                        # BaseCrawler 상속 확인 (BaseCrawler 자체 제외)
                        if (issubclass(obj, BaseCrawler) and
                                obj is not BaseCrawler and
                                obj.__module__ == module_name):

                            # 이미 등록되지 않은 경우에만 카운트
                            # (데코레이터로 이미 등록되었을 수 있음)
                            if obj not in cls._crawlers.values():
                                log.warning(
                                    "Found unregistered crawler: %s.%s "
                                    "(use @CrawlerRegistry.register decorator)",
                                    module_name, name
                                )
                            else:
                                discovered += 1

                except ImportError:
                    log.exception("Failed to import module: %s", module_name)
                except Exception:
                    log.exception(
                        "Error while inspecting module: %s",
                        module_name
                    )

            log.info("Auto-discovery complete: %d crawlers found", discovered)
            return discovered

        except Exception:
            log.exception("Auto-discovery failed")
            return 0

    @classmethod
    def clear(cls):
        """모든 등록 해제 (테스트용)"""
        cls._crawlers.clear()
        cls._metadata.clear()
        log.debug("Cleared all registered crawlers")


# ===========================================================================
# 편의 함수
# ===========================================================================

def get_crawler(site_code: str) -> BaseCrawler:
    """
    크롤러 인스턴스 반환 (편의 함수)

    Args:
        site_code: 사이트 코드

    Returns:
        크롤러 인스턴스
    """
    return CrawlerRegistry.get_crawler(site_code)


def list_crawlers() -> List[dict]:
    """
    등록된 크롤러 목록 반환 (편의 함수)

    Returns:
        크롤러 정보 리스트
    """
    return CrawlerRegistry.list_crawlers()


def auto_discover(package_name: str = 'crawlers') -> int:
    """
    크롤러 자동 발견 (편의 함수)

    Args:
        package_name: 패키지 이름

    Returns:
        발견된 크롤러 수
    """
    return CrawlerRegistry.auto_discover(package_name)
