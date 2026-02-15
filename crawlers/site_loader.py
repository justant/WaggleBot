"""
Site Configuration Loader

sites.yaml 파일을 읽어서 ConfigurableCrawler를 자동으로 생성하고 등록
"""

import logging
from pathlib import Path
from typing import Dict, Optional

import yaml

from crawlers.configurable_crawler import ConfigurableCrawler
from crawlers.plugin_manager import CrawlerRegistry

log = logging.getLogger(__name__)


class SiteConfigLoader:
    """사이트 설정 로더"""

    def __init__(self, config_path: Optional[Path] = None):
        """
        초기화

        Args:
            config_path: sites.yaml 파일 경로 (기본: config/sites.yaml)
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent / 'config' / 'sites.yaml'

        self.config_path = config_path
        self.sites_config: Dict[str, dict] = {}

    def load(self) -> int:
        """
        sites.yaml 파일을 읽고 크롤러 등록

        Returns:
            등록된 크롤러 수
        """
        if not self.config_path.exists():
            log.warning("sites.yaml not found: %s", self.config_path)
            return 0

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            if not config or 'sites' not in config:
                log.warning("No 'sites' section in %s", self.config_path)
                return 0

            self.sites_config = config['sites']
            log.info("Loaded %d site configurations", len(self.sites_config))

            # 각 사이트별로 크롤러 등록
            registered = 0
            for site_code, site_config in self.sites_config.items():
                if self._register_site(site_code, site_config):
                    registered += 1

            log.info("Registered %d configurable crawlers", registered)
            return registered

        except yaml.YAMLError:
            log.exception("Failed to parse YAML: %s", self.config_path)
            return 0
        except Exception:
            log.exception("Failed to load site config: %s", self.config_path)
            return 0

    def _register_site(self, site_code: str, site_config: dict) -> bool:
        """
        사이트를 크롤러로 등록

        Args:
            site_code: 사이트 코드
            site_config: 사이트 설정

        Returns:
            등록 성공 여부
        """
        try:
            # 이미 등록된 경우 스킵 (코드 기반 크롤러 우선)
            if CrawlerRegistry.is_registered(site_code):
                log.debug(
                    "Site '%s' already registered (code-based crawler), skipping YAML",
                    site_code
                )
                return False

            # ConfigurableCrawler 클래스를 동적으로 생성하여 등록
            crawler_class = self._create_crawler_class(site_code, site_config)

            # 레지스트리에 등록
            enabled = site_config.get('enabled', False)
            description = site_config.get('description', f'{site_code} crawler')

            CrawlerRegistry.register(site_code, description=description, enabled=enabled)(
                crawler_class
            )

            log.info(
                "Registered configurable crawler: %s (enabled=%s)",
                site_code, enabled
            )
            return True

        except Exception:
            log.exception("Failed to register site: %s", site_code)
            return False

    def _create_crawler_class(self, site_code: str, site_config: dict):
        """
        동적으로 크롤러 클래스 생성

        Args:
            site_code: 사이트 코드
            site_config: 사이트 설정

        Returns:
            크롤러 클래스
        """
        # 동적 클래스 생성
        class DynamicConfigurableCrawler(ConfigurableCrawler):
            def __init__(self):
                super().__init__(site_code, site_config)

        # 클래스 이름 설정
        DynamicConfigurableCrawler.__name__ = f'{site_code.title()}ConfigurableCrawler'
        DynamicConfigurableCrawler.__qualname__ = DynamicConfigurableCrawler.__name__

        return DynamicConfigurableCrawler

    def get_site_config(self, site_code: str) -> Optional[dict]:
        """
        사이트 설정 조회

        Args:
            site_code: 사이트 코드

        Returns:
            사이트 설정 또는 None
        """
        return self.sites_config.get(site_code)

    def list_sites(self) -> Dict[str, dict]:
        """
        모든 사이트 설정 반환

        Returns:
            사이트 설정 딕셔너리
        """
        return self.sites_config.copy()


# ===========================================================================
# 편의 함수
# ===========================================================================

_loader: Optional[SiteConfigLoader] = None


def load_site_configs(config_path: Optional[Path] = None) -> int:
    """
    sites.yaml 파일 로드 및 크롤러 등록

    Args:
        config_path: sites.yaml 파일 경로

    Returns:
        등록된 크롤러 수
    """
    global _loader
    _loader = SiteConfigLoader(config_path)
    return _loader.load()


def get_site_config(site_code: str) -> Optional[dict]:
    """
    사이트 설정 조회

    Args:
        site_code: 사이트 코드

    Returns:
        사이트 설정 또는 None
    """
    if _loader is None:
        load_site_configs()
    return _loader.get_site_config(site_code) if _loader else None


def list_site_configs() -> Dict[str, dict]:
    """
    모든 사이트 설정 반환

    Returns:
        사이트 설정 딕셔너리
    """
    if _loader is None:
        load_site_configs()
    return _loader.list_sites() if _loader else {}
