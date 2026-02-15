"""
Crawler Plugin System Test

플러그인 시스템 테스트 스크립트
"""

import logging

from crawlers.plugin_manager import CrawlerRegistry, auto_discover
from crawlers.base import BaseCrawler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

log = logging.getLogger(__name__)


def test_auto_discover():
    """자동 발견 테스트"""
    log.info("=== 자동 발견 테스트 ===")

    # 기존 등록 초기화
    CrawlerRegistry.clear()

    # 자동 발견
    count = auto_discover('crawlers')

    log.info("✓ 발견된 크롤러 수: %d", count)
    assert count > 0, "크롤러가 발견되지 않았습니다"


def test_list_crawlers():
    """크롤러 목록 조회 테스트"""
    log.info("\n=== 크롤러 목록 조회 테스트 ===")

    crawlers = CrawlerRegistry.list_crawlers()

    log.info("등록된 크롤러:")
    for crawler_info in crawlers:
        log.info(
            "  - %s (%s) - %s",
            crawler_info['site_code'],
            crawler_info['class_name'],
            crawler_info['description']
        )

    assert len(crawlers) > 0, "등록된 크롤러가 없습니다"
    log.info("✓ 총 %d개 크롤러 등록됨", len(crawlers))


def test_get_crawler():
    """크롤러 인스턴스 생성 테스트"""
    log.info("\n=== 크롤러 인스턴스 생성 테스트 ===")

    # nate_pann 크롤러 가져오기
    try:
        crawler = CrawlerRegistry.get_crawler('nate_pann')
        log.info("✓ nate_pann 크롤러 인스턴스 생성 성공")
        log.info("  클래스: %s", crawler.__class__.__name__)
        log.info("  사이트 코드: %s", crawler.site_code)
        assert isinstance(crawler, BaseCrawler)
    except Exception as e:
        log.error("❌ 크롤러 생성 실패: %s", e)
        raise


def test_is_registered():
    """등록 확인 테스트"""
    log.info("\n=== 등록 확인 테스트 ===")

    assert CrawlerRegistry.is_registered('nate_pann'), "nate_pann이 등록되지 않았습니다"
    log.info("✓ nate_pann 등록 확인")

    assert not CrawlerRegistry.is_registered('unknown_site'), "존재하지 않는 사이트가 등록되어 있습니다"
    log.info("✓ unknown_site 미등록 확인")


def test_get_enabled_crawlers():
    """활성화된 크롤러 조회 테스트"""
    log.info("\n=== 활성화된 크롤러 조회 테스트 ===")

    enabled = CrawlerRegistry.get_enabled_crawlers()

    log.info("활성화된 크롤러: %s", enabled)
    assert len(enabled) > 0, "활성화된 크롤러가 없습니다"
    log.info("✓ %d개 크롤러가 활성화됨", len(enabled))


def test_error_handling():
    """에러 처리 테스트"""
    log.info("\n=== 에러 처리 테스트 ===")

    # 존재하지 않는 크롤러 요청
    try:
        CrawlerRegistry.get_crawler('nonexistent_crawler')
        log.error("❌ 예외가 발생하지 않았습니다")
        assert False, "ValueError가 발생해야 합니다"
    except ValueError as e:
        log.info("✓ ValueError 정상 발생: %s", e)


def test_manual_registration():
    """수동 등록 테스트"""
    log.info("\n=== 수동 등록 테스트 ===")

    # 테스트용 더미 크롤러
    @CrawlerRegistry.register('test_crawler', description='테스트 크롤러', enabled=False)
    class TestCrawler(BaseCrawler):
        site_code = 'test_crawler'

        def fetch_listing(self):
            return []

        def parse_post(self, url):
            return {}

    # 등록 확인
    assert CrawlerRegistry.is_registered('test_crawler'), "테스트 크롤러가 등록되지 않았습니다"
    log.info("✓ 테스트 크롤러 등록 성공")

    # 인스턴스 생성
    crawler = CrawlerRegistry.get_crawler('test_crawler')
    assert crawler.site_code == 'test_crawler'
    log.info("✓ 테스트 크롤러 인스턴스 생성 성공")

    # 등록 해제
    CrawlerRegistry.unregister('test_crawler')
    assert not CrawlerRegistry.is_registered('test_crawler'), "등록 해제 실패"
    log.info("✓ 테스트 크롤러 등록 해제 성공")


def run_all_tests():
    """모든 테스트 실행"""
    log.info("=" * 80)
    log.info("크롤러 플러그인 시스템 테스트 시작")
    log.info("=" * 80)

    try:
        test_auto_discover()
        test_list_crawlers()
        test_get_crawler()
        test_is_registered()
        test_get_enabled_crawlers()
        test_error_handling()
        test_manual_registration()

        log.info("\n" + "=" * 80)
        log.info("✅ 모든 테스트 통과!")
        log.info("=" * 80)

    except AssertionError as e:
        log.error("\n" + "=" * 80)
        log.error("❌ 테스트 실패: %s", e)
        log.error("=" * 80)
        raise
    except Exception:
        log.exception("\n❌ 테스트 중 예외 발생")
        raise


if __name__ == '__main__':
    run_all_tests()
