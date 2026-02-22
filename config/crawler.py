"""크롤러 공통 설정."""
import os

from dotenv import load_dotenv

load_dotenv()

ENABLED_CRAWLERS: list[str] = os.getenv("ENABLED_CRAWLERS", "nate_pann").split(",")

CRAWL_INTERVAL_HOURS: int = int(os.getenv("CRAWL_INTERVAL_HOURS", "1"))

BROWSER_PROFILES: list[dict[str, str]] = [
    {   # Chrome 131 / Windows
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
    },
    {   # Chrome 131 / Mac
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"macOS"',
    },
    {   # Firefox 132 / Windows — Client Hints 미전송
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    },
    {   # Safari 18.1 / Mac — Client Hints 미전송
        "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    },
    {   # Edge 131 / Windows
        "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
        "sec_ch_ua": '"Chromium";v="131", "Microsoft Edge";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Windows"',
    },
    {   # Chrome 131 / Linux
        "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "sec_ch_ua": '"Chromium";v="131", "Google Chrome";v="131", "Not_A Brand";v="24"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_platform": '"Linux"',
    },
]

# 기존 import 호환
USER_AGENTS: list[str] = [p["user_agent"] for p in BROWSER_PROFILES]

# 딜레이 / 백오프 설정
CRAWL_DELAY_SECTION: tuple[float, float] = (1.5, 4.0)
CRAWL_DELAY_POST: tuple[float, float] = (0.3, 1.2)
CRAWL_DELAY_COMMENT: tuple[float, float] = (0.2, 0.8)
BLOCK_RETRY_MAX: int = 2
BLOCK_RETRY_BASE_DELAY: float = 30.0
BLOCK_RETRY_BACKOFF: float = 2.0
BLOCK_RETRY_MAX_DELAY: float = 300.0

REQUEST_HEADERS: dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "15"))
