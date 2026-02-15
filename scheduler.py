"""
Crawler Scheduler

주기적으로 크롤러 실행
"""

import logging
import threading

from apscheduler.schedulers.blocking import BlockingScheduler

from config.settings import CRAWL_INTERVAL_HOURS, ENABLED_CRAWLERS
from crawlers.plugin_manager import CrawlerRegistry, auto_discover
from db.session import SessionLocal

log = logging.getLogger(__name__)
_lock = threading.Lock()


def crawl_job():
    """
    모든 활성화된 크롤러 실행

    Returns:
        None
    """
    if not _lock.acquire(blocking=False):
        log.warning("Previous crawl still running — skipping this cycle")
        return

    try:
        log.info("=" * 80)
        log.info("Starting crawl cycle")
        log.info("=" * 80)

        # 활성화된 크롤러 목록
        enabled_sites = [s.strip() for s in ENABLED_CRAWLERS if s.strip()]
        log.info("Enabled crawlers: %s", enabled_sites)

        session = SessionLocal()
        try:
            for site_code in enabled_sites:
                try:
                    log.info("-" * 60)
                    log.info("Running crawler: %s", site_code)
                    log.info("-" * 60)

                    crawler = CrawlerRegistry.get_crawler(site_code)
                    crawler.run(session)

                    log.info("Crawler '%s' completed", site_code)

                except ValueError as e:
                    log.error("Failed to get crawler '%s': %s", site_code, e)
                except Exception:
                    log.exception("Error running crawler '%s'", site_code)

        finally:
            session.close()

        log.info("=" * 80)
        log.info("Crawl cycle complete")
        log.info("=" * 80)

    finally:
        _lock.release()


def start_scheduler():
    """
    스케줄러 시작

    Returns:
        None
    """
    # 크롤러 자동 발견
    discovered_count = auto_discover('crawlers')
    log.info("Discovered %d crawlers", discovered_count)

    # 등록된 크롤러 목록
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

    # 스케줄러 생성
    scheduler = BlockingScheduler()
    scheduler.add_job(
        crawl_job,
        "interval",
        hours=CRAWL_INTERVAL_HOURS,
        id="crawler_job",
        max_instances=1,
    )

    log.info(
        "Scheduler started — crawling every %d hour(s)",
        CRAWL_INTERVAL_HOURS
    )

    # 즉시 1회 실행 후 스케줄링 시작
    crawl_job()
    scheduler.start()
