"""TikTok Content Posting API OAuth2 인증 스크립트.

사용법:
    python scripts/tiktok_auth.py

동작:
    1. secrets/tiktok_client.json에서 client_key/secret 읽기
    2. PKCE (code_verifier, code_challenge) 생성
    3. 브라우저에서 TikTok OAuth 동의 화면 열기
    4. 로컬 콜백 서버(port 8091)로 authorization code 수신
    5. access_token 교환 후 config/tiktok_token.json 저장
"""

import base64
import hashlib
import json
import logging
import random
import string
import sys
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

# 프로젝트 루트를 sys.path에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from config.settings import load_pipeline_config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_CALLBACK_PORT = 8091
_REDIRECT_URI = f"http://localhost:{_CALLBACK_PORT}/callback"

# TikTok Content Posting API 스코프
_SCOPES = "user.info.basic,video.publish,video.upload"

# TikTok API 엔드포인트
_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


def _generate_pkce_pair() -> tuple[str, str]:
    """PKCE 인증을 위한 code_verifier와 code_challenge를 생성한다."""
    # 1. 64자의 랜덤 문자열(code_verifier) 생성
    characters = string.ascii_letters + string.digits + "-._~"
    code_verifier = ''.join(random.choices(characters, k=64))

    # 2. SHA256 해시 생성 후 Base64-URL 인코딩 (code_challenge)
    digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
    code_challenge = base64.urlsafe_b64encode(digest).decode('ascii').rstrip('=')

    return code_verifier, code_challenge


def _load_client_config() -> dict:
    """secrets/tiktok_client.json에서 클라이언트 인증 정보를 로드한다."""
    cfg = load_pipeline_config()
    client_path = _PROJECT_ROOT / cfg.get(
        "tiktok_client_secret_path", "secrets/tiktok_client.json"
    )
    if not client_path.exists():
        logger.error(
            "TikTok 클라이언트 파일이 없습니다: %s\n"
            "  → TikTok Developer Portal에서 앱을 생성하고\n"
            "    client_key와 client_secret을 위 파일에 저장하세요.",
            client_path,
        )
        sys.exit(1)

    data = json.loads(client_path.read_text(encoding="utf-8"))
    if data.get("client_key") in ("", "USER_INPUT") or data.get("client_secret") in ("", "USER_INPUT"):
        logger.error(
            "client_key / client_secret이 설정되지 않았습니다.\n"
            "  → %s 파일을 편집하여 실제 값을 입력하세요.",
            client_path,
        )
        sys.exit(1)

    return data


def _exchange_code_for_token(code: str, client_key: str, client_secret: str, code_verifier: str) -> dict:
    """authorization code와 code_verifier를 access_token으로 교환한다."""
    payload = {
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": _REDIRECT_URI,
        "code_verifier": code_verifier,  # PKCE 보안 키 추가
    }

    # x-www-form-urlencoded 형식으로 전송
    resp = httpx.post(_TOKEN_URL, data=payload, timeout=30)

    if resp.status_code != 200:
        logger.error("토큰 교환 실패: HTTP %d - %s", resp.status_code, resp.text)
        sys.exit(1)

    data = resp.json()

    if "access_token" not in data:
        logger.error("토큰 응답에 access_token이 없습니다: %s", data)
        sys.exit(1)

    return data


def _save_token(token_data: dict, client_key: str, client_secret: str) -> None:
    """토큰을 config/tiktok_token.json에 저장하고 credentials.json에 동기화한다."""
    token_path = _PROJECT_ROOT / "config" / "tiktok_token.json"
    token_path.parent.mkdir(parents=True, exist_ok=True)

    saved = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data.get("refresh_token", ""),
        "open_id": token_data.get("open_id", ""),
        "expires_in": token_data.get("expires_in", 86400),
        "refresh_expires_in": token_data.get("refresh_expires_in", 0),
        "issued_at": int(time.time()),
        "client_key": client_key,
        "client_secret": client_secret,
    }
    token_path.write_text(
        json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("토큰 저장 완료: %s", token_path)

    # credentials.json에 동기화
    creds_path = _PROJECT_ROOT / "config" / "credentials.json"
    creds_config: dict = {}
    if creds_path.exists():
        creds_config = json.loads(creds_path.read_text(encoding="utf-8"))
    creds_config["tiktok"] = {
        "client_key": client_key,
        "client_secret": client_secret,
        "access_token": token_data["access_token"],
    }
    creds_path.write_text(
        json.dumps(creds_config, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("credentials.json 동기화 완료")


def run_oauth_flow() -> None:
    """TikTok OAuth2 인증 플로우를 실행한다."""
    client_cfg = _load_client_config()
    client_key = client_cfg["client_key"]
    client_secret = client_cfg["client_secret"]

    # PKCE 키 쌍 생성
    code_verifier, code_challenge = _generate_pkce_pair()

    # authorization code를 수신할 핸들러
    received_code: list[str] = []

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)

            if "code" in params:
                received_code.append(params["code"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h1>TikTok OAuth</h1>"
                    b"<p>Authorized! You can close this tab and return to your terminal.</p></body></html>"
                )
            elif "error" in params:
                error = params.get("error", ["unknown"])[0]
                error_desc = params.get("error_description", [""])[0]
                logger.error("OAuth 오류: %s - %s", error, error_desc)
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(f"<html><body><h1>Error: {error}</h1></body></html>".encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            """기본 로그 억제."""

    # OAuth 동의 화면 URL 구성 (PKCE 파라미터 추가)
    auth_params = urlencode({
        "client_key": client_key,
        "redirect_uri": _REDIRECT_URI,
        "scope": _SCOPES,
        "response_type": "code",
        "code_challenge": code_challenge,        # 추가된 부분
        "code_challenge_method": "S256",         # 추가된 부분
    })
    auth_url = f"{_AUTH_URL}?{auth_params}"

    logger.info("=" * 60)
    logger.info("브라우저에서 TikTok 로그인 화면을 엽니다.")
    logger.info("만약 브라우저가 자동으로 열리지 않는다면, 아래 URL을 복사하여 직접 접속하세요:\n")
    logger.info(auth_url)
    logger.info("=" * 60)

    # 이전처럼 OS 환경 문제로 브라우저가 열리지 않더라도 진행할 수 있게 조치
    try:
        webbrowser.open(auth_url)
    except Exception as e:
        logger.warning("자동으로 브라우저를 열지 못했습니다: %s", e)

    # 로컬 콜백 서버 시작
    server = HTTPServer(("0.0.0.0", _CALLBACK_PORT), CallbackHandler)
    logger.info("권한 승인 대기 중 (port %d)...", _CALLBACK_PORT)

    while not received_code:
        server.handle_request()

    server.server_close()
    code = received_code[0]
    logger.info("Authorization code 수신 완료")

    # 토큰 교환 (code_verifier 같이 전송)
    logger.info("Access token 교환 중...")
    token_data = _exchange_code_for_token(code, client_key, client_secret, code_verifier)
    _save_token(token_data, client_key, client_secret)

    logger.info("🎉 TikTok OAuth 인증 및 토큰 발급이 성공적으로 완료되었습니다!")


if __name__ == "__main__":
    run_oauth_flow()