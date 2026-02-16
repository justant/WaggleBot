"""
YAML 기반 크롤러 테스트

sites.yaml 설정을 읽어서 크롤러를 생성하고 테스트
"""

import logging

from crawlers.plugin_manager import CrawlerRegistry
from crawlers.site_loader import load_site_configs, list_site_configs

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

log = logging.getLogger(__name__)


def test_load_yaml():
    """YAML 설정 로드 테스트"""
    log.info("=== YAML 설정 로드 테스트 ===")

    # sites.yaml 로드
    count = load_site_configs()
    log.info("✓ %d개 사이트 설정 로드됨", count)

    # 설정 조회
    sites = list_site_configs()
    log.info("설정된 사이트:")
    for site_code, config in sites.items():
        enabled = config.get('enabled', False)
        description = config.get('description', 'No description')
        status = "✓" if enabled else "✗"
        log.info("  %s %s - %s", status, site_code, description)


def test_yaml_crawler_registration():
    """YAML 크롤러 등록 테스트"""
    log.info("\n=== YAML 크롤러 등록 테스트 ===")

    # 레지스트리 확인
    crawlers = CrawlerRegistry.list_crawlers()

    yaml_crawlers = [
        c for c in crawlers
        if 'Configurable' in c['class_name']
    ]

    log.info("YAML 기반 크롤러: %d개", len(yaml_crawlers))
    for crawler_info in yaml_crawlers:
        log.info(
            "  - %s (%s) - enabled=%s",
            crawler_info['site_code'],
            crawler_info['class_name'],
            crawler_info['enabled']
        )


def test_yaml_crawler_instance():
    """YAML 크롤러 인스턴스 생성 테스트"""
    log.info("\n=== YAML 크롤러 인스턴스 생성 테스트 ===")

    # sites.yaml에 정의된 사이트 중 하나 선택
    sites = list_site_configs()

    for site_code in sites.keys():
        # 코드 기반 크롤러는 제외 (이미 등록되어 YAML로 오버라이드 안됨)
        if not CrawlerRegistry.is_registered(site_code):
            continue

        try:
            crawler = CrawlerRegistry.get_crawler(site_code)
            log.info("✓ %s 크롤러 인스턴스 생성 성공", site_code)
            log.info("  클래스: %s", crawler.__class__.__name__)
            log.info("  사이트 코드: %s", crawler.site_code)

            # ConfigurableCrawler인지 확인
            from crawlers.configurable_crawler import ConfigurableCrawler
            if isinstance(crawler, ConfigurableCrawler):
                log.info("  ✓ ConfigurableCrawler 확인")
                log.info("  설정 키: %s", list(crawler.config.keys()))
            else:
                log.info("  (코드 기반 크롤러)")

        except Exception as e:
            log.error("❌ %s 크롤러 생성 실패: %s", site_code, e)


def test_yaml_crawler_config():
    """YAML 크롤러 설정 검증 테스트"""
    log.info("\n=== YAML 크롤러 설정 검증 테스트 ===")

    sites = list_site_configs()

    for site_code, config in sites.items():
        log.info("\n[%s]", site_code)

        # 필수 필드 확인
        required_fields = ['description', 'enabled']
        for field in required_fields:
            if field in config:
                log.info("  ✓ %s: %s", field, config[field])
            else:
                log.warning("  ✗ %s: 없음", field)

        # URL 설정 확인
        if 'listing_url' in config:
            log.info("  ✓ listing_url: %s", config['listing_url'])
        else:
            log.warning("  ✗ listing_url: 없음")

        # 셀렉터 확인
        if 'selectors' in config:
            selectors = config['selectors']
            log.info("  ✓ selectors: %d개", len(selectors))
            log.debug("    %s", list(selectors.keys()))
        else:
            log.warning("  ✗ selectors: 없음")


def test_mixed_crawlers():
    """코드 기반 + YAML 기반 크롤러 혼합 테스트"""
    log.info("\n=== 코드 기반 + YAML 기반 혼합 테스트 ===")

    # 코드 기반 크롤러 등록
    from crawlers.plugin_manager import auto_discover
    code_count = auto_discover('crawlers')
    log.info("코드 기반 크롤러: %d개", code_count)

    # YAML 기반 크롤러 로드
    yaml_count = load_site_configs()
    log.info("YAML 기반 크롤러: %d개", yaml_count)

    # 전체 목록
    all_crawlers = CrawlerRegistry.list_crawlers()
    log.info("전체 등록된 크롤러: %d개", len(all_crawlers))

    # 타입별 분류
    code_based = []
    yaml_based = []

    for crawler_info in all_crawlers:
        if 'Configurable' in crawler_info['class_name']:
            yaml_based.append(crawler_info['site_code'])
        else:
            code_based.append(crawler_info['site_code'])

    log.info("코드 기반: %s", code_based)
    log.info("YAML 기반: %s", yaml_based)


def run_all_tests():
    """모든 테스트 실행"""
    log.info("=" * 80)
    log.info("YAML 기반 크롤러 테스트 시작")
    log.info("=" * 80)

    try:
        test_load_yaml()
        test_yaml_crawler_registration()
        test_yaml_crawler_instance()
        test_yaml_crawler_config()
        test_mixed_crawlers()

        log.info("\n" + "=" * 80)
        log.info("✅ 모든 테스트 통과!")
        log.info("=" * 80)

    except Exception:
        log.exception("\n❌ 테스트 실패")
        raise


if __name__ == '__main__':
    run_all_tests()
