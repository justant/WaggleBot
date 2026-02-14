import logging
import threading

from apscheduler.schedulers.blocking import BlockingScheduler

from config.settings import CRAWL_INTERVAL_HOURS
from crawlers.nate_pann import NatePannCrawler
from db.session import SessionLocal

log = logging.getLogger(__name__)
_lock = threading.Lock()


def crawl_job():
    if not _lock.acquire(blocking=False):
        log.warning("Previous crawl still running — skipping this cycle")
        return

    try:
        log.info("Starting crawl cycle")
        session = SessionLocal()
        try:
            crawler = NatePannCrawler()
            crawler.run(session)
        finally:
            session.close()
        log.info("Crawl cycle complete")
    finally:
        _lock.release()


def start_scheduler():
    scheduler = BlockingScheduler()
    scheduler.add_job(
        crawl_job,
        "interval",
        hours=CRAWL_INTERVAL_HOURS,
        id="nate_pann_crawl",
        max_instances=1,
    )
    log.info("Scheduler started — crawling every %d hour(s)", CRAWL_INTERVAL_HOURS)
    # Run once immediately, then let the scheduler handle intervals
    crawl_job()
    scheduler.start()
