"""YouTube OAuth2 최초 인증 스크립트.

사용법:
    python scripts/youtube_auth.py

동작:
    1. secrets/client_secret.json 읽기
    2. 브라우저에서 Google OAuth 동의 화면 열기
    3. 인증 완료 시 config/youtube_token.json 자동 생성
"""

import json
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import load_pipeline_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# YouTube Data API v3 — 업로드 + 통계 조회 스코프
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


def run_oauth_flow() -> None:
    """OAuth2 인증 플로우를 실행하여 토큰을 생성한다."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    cfg = load_pipeline_config()
    client_secret_path = _PROJECT_ROOT / cfg.get(
        "youtube_client_secret_path", "secrets/client_secret.json"
    )
    token_path = _PROJECT_ROOT / "config" / "youtube_token.json"

    if not client_secret_path.exists():
        logger.error(
            "client_secret.json 파일이 없습니다: %s\n"
            "  → GCP 콘솔에서 OAuth 2.0 클라이언트 ID를 생성하고\n"
            "    JSON 파일을 다운로드하여 위 경로에 저장하세요.",
            client_secret_path,
        )
        sys.exit(1)

    logger.info("OAuth 클라이언트 시크릿 로드: %s", client_secret_path)
    logger.info("브라우저가 열립니다. YouTube 채널이 연결된 Google 계정으로 로그인하세요.")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(client_secret_path),
        scopes=SCOPES,
    )

    # 로컬 서버를 열어 OAuth 콜백을 수신한다.
    # WSL 환경에서는 port=0 (랜덤 포트) 사용 시 문제가 있으므로 8090 고정.
    creds = flow.run_local_server(
        port=8090,
        prompt="consent",
        access_type="offline",  # refresh_token 발급 필수
    )

    # 토큰 저장
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_data = json.loads(creds.to_json())
    token_path.write_text(json.dumps(token_data, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("토큰 저장 완료: %s", token_path)

    # credentials.json에도 동기화 (대시보드 호환)
    creds_config_path = _PROJECT_ROOT / "config" / "credentials.json"
    creds_config: dict = {}
    if creds_config_path.exists():
        creds_config = json.loads(creds_config_path.read_text(encoding="utf-8"))
    creds_config.setdefault("youtube", {})["token_json"] = json.dumps(token_data, ensure_ascii=False)
    creds_config_path.write_text(
        json.dumps(creds_config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("credentials.json 동기화 완료")

    logger.info("YouTube OAuth 인증이 완료되었습니다!")
    logger.info("이제 scripts/youtube_test.py로 연동 테스트를 실행하세요.")


if __name__ == "__main__":
    run_oauth_flow()
