"""TikTok 업로드 실전 테스트 — FHD 영상 1건을 실제 업로드한다.

사용법:
    python scripts/tiktok_upload_test.py
"""

import logging
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import MEDIA_DIR
from uploaders.tiktok import TikTokUploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    # 1. FHD 영상 검색
    fhd_videos = sorted(MEDIA_DIR.glob("video/**/*_FHD.mp4"))
    if not fhd_videos:
        logger.error("FHD 영상이 없습니다: %s/video/", MEDIA_DIR)
        sys.exit(1)

    video_path = fhd_videos[0]
    size_mb = video_path.stat().st_size / (1024 * 1024)
    logger.info("테스트 영상: %s (%.1fMB)", video_path, size_mb)

    # 2. 인증 확인
    uploader = TikTokUploader()
    if not uploader.validate_credentials():
        logger.error("TikTok 인증 실패 — scripts/tiktok_auth.py를 먼저 실행하세요.")
        sys.exit(1)
    logger.info("인증 확인 완료")

    # 3. 업로드
    metadata = {
        "title": "[테스트] WaggleBot TikTok 업로드 테스트",
        "tags": ["테스트", "WaggleBot", "Shorts"],
        "privacy": "SELF_ONLY",
    }

    logger.info("업로드 시작...")
    result = uploader.upload(video_path, metadata)

    logger.info("=" * 50)
    logger.info("업로드 성공!")
    logger.info("  platform_id: %s", result.get("platform_id"))
    logger.info("  url: %s", result.get("url"))
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
