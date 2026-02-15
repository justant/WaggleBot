"""
WaggleBot Main Entry Point

크롤러 실행 및 스케줄링
"""

import argparse
import logging
import sys

from config.settings import ENABLED_CRAWLERS
from crawlers.plugin_manager import CrawlerRegistry, auto_discover
from crawlers.site_loader import load_site_configs
from db.session import init_db, SessionLocal
from scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def run_once():
    """
    모든 활성화된 크롤러를 1회 실행

    Returns:
        None
    """
    # YAML 설정 기반 크롤러 로드
    yaml_count = load_site_configs()
    log.info("Loaded %d YAML-based crawlers", yaml_count)

    # 코드 기반 크롤러 자동 발견
    discovered_count = auto_discover('crawlers')
    log.info("Discovered %d code-based crawlers", discovered_count)

    # 등록된 크롤러 목록 출력
    crawlers = CrawlerRegistry.list_crawlers()
    log.info("Available crawlers:")
    for crawler_info in crawlers:
        status = "✓" if crawler_info['enabled'] else "✗"
        log.info(
            "  %s %s (%s) - %s",
            status,
            crawler_info['site_code'],
            crawler_info['class_name'],
            crawler_info['description']
        )

    # 활성화된 크롤러만 실행
    enabled_sites = [s.strip() for s in ENABLED_CRAWLERS if s.strip()]
    log.info("Enabled crawlers: %s", enabled_sites)

    session = SessionLocal()
    try:
        for site_code in enabled_sites:
            try:
                log.info("=" * 60)
                log.info("Running crawler: %s", site_code)
                log.info("=" * 60)

                crawler = CrawlerRegistry.get_crawler(site_code)
                crawler.run(session)

                log.info("Crawler '%s' completed successfully", site_code)

            except ValueError as e:
                log.error("Failed to get crawler '%s': %s", site_code, e)
            except Exception:
                log.exception("Error running crawler '%s'", site_code)

    finally:
        session.close()

    log.info("All crawlers completed")


def list_available_crawlers():
    """
    사용 가능한 크롤러 목록 출력

    Returns:
        None
    """
    load_site_configs()
    auto_discover('crawlers')

    crawlers = CrawlerRegistry.list_crawlers()

    if not crawlers:
        print("No crawlers found.")
        return

    print("\n" + "=" * 80)
    print("Available Crawlers")
    print("=" * 80)

    for crawler_info in crawlers:
        status = "ENABLED" if crawler_info['enabled'] else "DISABLED"
        print(f"\n[{crawler_info['site_code']}] ({status})")
        print(f"  Class: {crawler_info['class_name']}")
        print(f"  Module: {crawler_info['module']}")
        print(f"  Description: {crawler_info['description']}")

    print("\n" + "=" * 80)
    print(f"Total: {len(crawlers)} crawlers")
    print("=" * 80 + "\n")

    print("To enable/disable crawlers, set ENABLED_CRAWLERS in .env:")
    print('  ENABLED_CRAWLERS=nate_pann,nate_tok')
    print()


def main():
    """메인 진입점"""
    parser = argparse.ArgumentParser(
        description="WaggleBot Content Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --once          # Run all enabled crawlers once
  python main.py --list          # List available crawlers
  python main.py                 # Start scheduler (continuous)
        """
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single crawl cycle and exit",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all available crawlers and exit",
    )
    args = parser.parse_args()

    # 크롤러 목록 출력
    if args.list:
        list_available_crawlers()
        return

    # DB 초기화
    #log.info("Initializing database…")
    #init_db()

    # 1회 실행 또는 스케줄러 시작
    if args.once:
        log.info("Running single crawl cycle…")
        run_once()
        log.info("Done.")
    else:
        log.info("Starting scheduler…")
        start_scheduler()


if __name__ == "__main__":
    main()
