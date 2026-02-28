"""YouTube API 연동 테스트 스크립트.

사용법:
    python scripts/youtube_test.py

동작:
    1. youtube_token.json 로드 및 유효성 확인
    2. YouTube Data API v3 채널 정보 조회
    3. 연동 상태 리포트 출력
"""

import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def test_youtube_connection() -> bool:
    """YouTube API 연동을 테스트한다."""
    from uploaders.youtube import YouTubeUploader

    logger.info("=" * 50)
    logger.info("YouTube API 연동 테스트 시작")
    logger.info("=" * 50)

    # 1. 토큰 파일 확인
    token_path = _PROJECT_ROOT / "config" / "youtube_token.json"
    if not token_path.exists():
        logger.error(
            "토큰 파일이 없습니다: %s\n"
            "  → 먼저 scripts/youtube_auth.py를 실행하여 인증하세요.",
            token_path,
        )
        return False
    logger.info("[1/3] 토큰 파일 확인: %s", token_path)

    # 2. YouTubeUploader 인스턴스 생성 및 인증 검증
    uploader = YouTubeUploader()
    logger.info("[2/3] YouTubeUploader 인스턴스 생성 완료")

    if not uploader.validate_credentials():
        logger.error("인증 검증 실패 — 토큰이 만료되었거나 유효하지 않습니다.")
        logger.error("  → scripts/youtube_auth.py를 다시 실행하세요.")
        return False
    logger.info("[2/3] 인증 검증 성공")

    # 3. 채널 정보 조회
    try:
        svc = uploader._get_service()
        response = svc.channels().list(
            part="snippet,statistics",
            mine=True,
        ).execute()

        items = response.get("items", [])
        if not items:
            logger.warning("채널 정보를 가져올 수 없습니다.")
            return False

        channel = items[0]
        snippet = channel.get("snippet", {})
        stats = channel.get("statistics", {})

        logger.info("[3/3] 채널 정보 조회 성공!")
        logger.info("  채널명: %s", snippet.get("title", "알 수 없음"))
        logger.info("  구독자: %s명", stats.get("subscriberCount", "비공개"))
        logger.info("  총 영상: %s개", stats.get("videoCount", "0"))
        logger.info("  총 조회수: %s회", stats.get("viewCount", "0"))

    except Exception as exc:
        logger.error("채널 정보 조회 실패: %s", exc)
        return False

    logger.info("=" * 50)
    logger.info("YouTube API 연동 테스트 통과!")
    logger.info("=" * 50)
    return True


if __name__ == "__main__":
    success = test_youtube_connection()
    sys.exit(0 if success else 1)
