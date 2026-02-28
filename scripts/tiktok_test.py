"""TikTok Content Posting API 연동 테스트 스크립트.

사용법:
    python scripts/tiktok_test.py

동작:
    1. tiktok_token.json 존재 확인
    2. TikTokUploader.validate_credentials() 호출
    3. User Info API로 계정 정보 출력
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


def test_tiktok_connection() -> bool:
    """TikTok API 연동을 테스트한다."""
    from uploaders.tiktok import TikTokUploader

    logger.info("=" * 50)
    logger.info("TikTok API 연동 테스트 시작")
    logger.info("=" * 50)

    # 1. 토큰 파일 확인
    token_path = _PROJECT_ROOT / "config" / "tiktok_token.json"
    if not token_path.exists():
        logger.error(
            "토큰 파일이 없습니다: %s\n"
            "  → 먼저 scripts/tiktok_auth.py를 실행하여 인증하세요.",
            token_path,
        )
        return False
    logger.info("[1/3] 토큰 파일 확인: %s", token_path)

    # 2. TikTokUploader 인스턴스 생성 및 인증 검증
    uploader = TikTokUploader()
    logger.info("[2/3] TikTokUploader 인스턴스 생성 완료")

    if not uploader.validate_credentials():
        logger.error("인증 검증 실패 — 토큰이 만료되었거나 유효하지 않습니다.")
        logger.error("  → scripts/tiktok_auth.py를 다시 실행하세요.")
        return False
    logger.info("[2/3] 인증 검증 성공")

    # 3. User Info API로 계정 정보 조회
    try:
        import httpx

        token_data = uploader._load_and_refresh_token()
        access_token = token_data["access_token"]

        resp = httpx.get(
            "https://open.tiktokapis.com/v2/user/info/",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "display_name,avatar_url"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        user_data = data.get("data", {}).get("user", {})

        logger.info("[3/3] 계정 정보 조회 성공!")
        logger.info("  표시 이름: %s", user_data.get("display_name", "알 수 없음"))
        logger.info("  프로필 URL: %s", user_data.get("avatar_url", "없음"))

    except Exception as exc:
        logger.error("계정 정보 조회 실패: %s", exc)
        return False

    logger.info("=" * 50)
    logger.info("TikTok API 연동 테스트 통과!")
    logger.info("=" * 50)
    return True


if __name__ == "__main__":
    success = test_tiktok_connection()
    sys.exit(0 if success else 1)
