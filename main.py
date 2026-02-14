import argparse
import logging
import sys

from db.session import init_db, SessionLocal
from crawlers.nate_pann import NatePannCrawler
from scheduler import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def run_once():
    session = SessionLocal()
    try:
        crawler = NatePannCrawler()
        crawler.run(session)
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="WaggleBot Content Pipeline")
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single crawl cycle and exit",
    )
    args = parser.parse_args()

    log.info("Initializing database…")
    init_db()

    if args.once:
        log.info("Running single crawl…")
        run_once()
        log.info("Done.")
    else:
        log.info("Starting scheduler…")
        start_scheduler()


if __name__ == "__main__":
    main()
