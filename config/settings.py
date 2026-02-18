import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://wagglebot:wagglebot@localhost/wagglebot",
)

CRAWL_INTERVAL_HOURS = int(os.getenv("CRAWL_INTERVAL_HOURS", "1"))

# 크롤러 설정
ENABLED_CRAWLERS = os.getenv("ENABLED_CRAWLERS", "nate_pann").split(",")

NATE_PANN_SECTIONS = [
    {"name": "톡톡 베스트", "url": "https://pann.nate.com/talk/ranking"},
    {"name": "톡커들의 선택", "url": "https://pann.nate.com/talk/ranking/best"},
]

BOBAEDREAM_SECTIONS = [
    {"name": "자유게시판 베스트", "url": "https://m.bobaedream.co.kr/board/best/freeb"},
    {"name": "전체 베스트", "url": "https://m.bobaedream.co.kr/board/new_writing/best"},
]

DCINSIDE_SECTIONS = [
    {"name": "실시간 베스트 (실베)", "url": "https://gall.dcinside.com/board/lists/?id=dcbest"},
    {"name": "HIT 갤러리 (힛갤)",   "url": "https://gall.dcinside.com/board/lists/?id=hit"},
]

FMKOREA_SECTIONS = [
    {"name": "포텐 터짐 최신순", "url": "https://www.fmkorea.com/index.php?mid=best"},
    {"name": "포텐 터짐 화제순", "url": "https://www.fmkorea.com/index.php?mid=best2&sort_index=pop&order_type=desc"},
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

REQUEST_HEADERS = {
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "15"))

STREAMLIT_PORT = int(os.getenv("STREAMLIT_PORT", "8501"))

# ---------------------------------------------------------------------------
# AI Worker
# ---------------------------------------------------------------------------
AI_POLL_INTERVAL = int(os.getenv("AI_POLL_INTERVAL", "10"))
MAX_RETRY_COUNT = int(os.getenv("MAX_RETRY_COUNT", "3"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")


def get_ollama_host() -> str:
    """Ollama 호스트를 호출 시점의 환경변수에서 읽는다 (컨테이너 재시작 없이도 반영)."""
    return os.getenv("OLLAMA_HOST", "http://localhost:11434")
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", str(_PROJECT_ROOT / "media")))
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", str(_PROJECT_ROOT / "assets")))

# TTS 엔진별 목소리 프리셋
TTS_VOICES: dict[str, list[dict[str, str]]] = {
    "edge-tts": [
        {"id": "ko-KR-SunHiNeural",   "name": "선히 (여, 밝음)"},
        {"id": "ko-KR-InJoonNeural",   "name": "인준 (남, 차분)"},
        {"id": "ko-KR-HyunsuNeural",   "name": "현수 (남, 뉴스)"},
        {"id": "ko-KR-BongJinNeural",  "name": "봉진 (남, 따뜻)"},
        {"id": "ko-KR-GookMinNeural",  "name": "국민 (남, 밝음)"},
    ],
    "kokoro": [
        {"id": "af_heart",   "name": "Heart (여, 기본)"},
        {"id": "af_bella",   "name": "Bella (여, 부드러움)"},
        {"id": "af_sarah",   "name": "Sarah (여, 명랑)"},
        {"id": "am_adam",    "name": "Adam (남, 차분)"},
        {"id": "am_michael", "name": "Michael (남, 깊음)"},
    ],
    "gpt-sovits": [
        {"id": "default_korean_f", "name": "한국어 여성 기본"},
        {"id": "default_korean_m", "name": "한국어 남성 기본"},
        {"id": "custom_1",         "name": "커스텀 음성 1"},
        {"id": "custom_2",         "name": "커스텀 음성 2"},
        {"id": "custom_3",         "name": "커스텀 음성 3"},
    ],
}

# ---------------------------------------------------------------------------
# Pipeline config (JSON 파일)
# ---------------------------------------------------------------------------
_PIPELINE_CONFIG_PATH = _PROJECT_ROOT / "config" / "pipeline.json"
FEEDBACK_CONFIG_PATH = _PROJECT_ROOT / "config" / "feedback_config.json"
AB_TEST_CONFIG_PATH  = _PROJECT_ROOT / "config" / "ab_tests.json"

_PIPELINE_DEFAULTS: dict[str, str] = {
    "tts_engine": "edge-tts",
    "tts_voice": "ko-KR-SunHiNeural",
    "llm_model": OLLAMA_MODEL,
    "video_resolution": "1080x1920",
    "video_codec": "h264_nvenc",
    "bgm_volume": "0.15",
    "subtitle_font": "NanumGothic",
    "upload_platforms": '["youtube"]',
    "upload_privacy": "unlisted",
    "youtube_credentials_path": "config/youtube_credentials.json",
    # 수신함 자동 승인
    "auto_approve_enabled": "false",
    "auto_approve_threshold": "80",
}


def load_pipeline_config() -> dict[str, str]:
    if _PIPELINE_CONFIG_PATH.exists():
        with open(_PIPELINE_CONFIG_PATH, encoding="utf-8") as f:
            return {**_PIPELINE_DEFAULTS, **json.load(f)}
    return dict(_PIPELINE_DEFAULTS)


def save_pipeline_config(cfg: dict[str, str]) -> None:
    _PIPELINE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PIPELINE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

# ---------------------------------------------------------------------------
# Platform credentials (API keys / OAuth tokens)
# ---------------------------------------------------------------------------
_CREDENTIALS_PATH = _PROJECT_ROOT / "config" / "credentials.json"

# 플랫폼별 인증 필드 정의 — 새 플랫폼 추가 시 여기에 항목 추가
PLATFORM_CREDENTIAL_FIELDS: dict[str, list[dict]] = {
    "youtube": [
        {"key": "client_id",     "label": "Client ID",        "type": "text"},
        {"key": "client_secret", "label": "Client Secret",     "type": "password"},
        {"key": "token_json",    "label": "OAuth2 Token JSON", "type": "textarea",
         "help": "Google OAuth2 인증 후 발급된 token.json 파일 내용 전체를 붙여넣으세요"},
    ],
    "tiktok": [
        {"key": "client_key",    "label": "Client Key",    "type": "text"},
        {"key": "client_secret", "label": "Client Secret", "type": "password"},
        {"key": "access_token",  "label": "Access Token",  "type": "password"},
    ],
    "instagram": [
        {"key": "app_id",       "label": "App ID",        "type": "text"},
        {"key": "app_secret",   "label": "App Secret",    "type": "password"},
        {"key": "access_token", "label": "Access Token",  "type": "password"},
    ],
}


def load_credentials_config() -> dict[str, dict]:
    if _CREDENTIALS_PATH.exists():
        with open(_CREDENTIALS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_credentials_config(creds: dict[str, dict]) -> None:
    _CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_CREDENTIALS_PATH, "w", encoding="utf-8") as f:
        json.dump(creds, f, ensure_ascii=False, indent=2)

AUDIO_DIR: Path = Path(os.getenv(
    "AUDIO_DIR",
    str(_PROJECT_ROOT / "assets" / "audio"),
))
TTS_VOICE: str = os.getenv("TTS_VOICE", "ko-KR-SunHiNeural")
TTS_RATE: str = os.getenv("TTS_RATE", "+25%")
FONT_BODY: Path = Path(os.getenv(
    "FONT_BODY",
    str(_PROJECT_ROOT / "assets" / "fonts" / "NotoSansKR-Medium.ttf"),
))

# 효과음 타이밍 오프셋 (음수 = 앞당김)
SFX_OFFSET: float = float(os.getenv("SFX_OFFSET", "-0.15"))

# ==================== 썰 렌더러 v4 ====================

# 폰트 크기 (대폭 확대 — 모바일 쇼츠 가독성)
SSUL_FONT_SIZE_BODY: int = int(os.getenv("SSUL_FONT_SIZE_BODY", "85"))
SSUL_FONT_SIZE_COMMENT: int = int(os.getenv("SSUL_FONT_SIZE_COMMENT", "70"))
SSUL_FONT_SIZE_TITLE: int = int(os.getenv("SSUL_FONT_SIZE_TITLE", "52"))
SSUL_FONT_SIZE_META: int = int(os.getenv("SSUL_FONT_SIZE_META", "32"))

# 레이아웃
SSUL_TEXT_Y_START: int = int(os.getenv("SSUL_TEXT_Y_START", "500"))
SSUL_MAX_TEXT_WIDTH: int = int(os.getenv("SSUL_MAX_TEXT_WIDTH", "950"))

# 페이지 넘김 (3줄 초과 시 화면 완전 Clear)
SSUL_MAX_LINES_PER_PAGE: int = int(os.getenv("SSUL_MAX_LINES_PER_PAGE", "3"))

# 색상
SSUL_PREV_TEXT_COLOR: str = os.getenv("SSUL_PREV_TEXT_COLOR", "#666666")
SSUL_NEW_TEXT_COLOR: str = os.getenv("SSUL_NEW_TEXT_COLOR", "#000000")

# 댓글 스타일
SSUL_COMMENT_BG_ENABLE: bool = os.getenv("SSUL_COMMENT_BG_ENABLE", "true").lower() == "true"
SSUL_COMMENT_BG_COLOR: str = os.getenv("SSUL_COMMENT_BG_COLOR", "#F5F5F5")
SSUL_COMMENT_BORDER_COLOR: str = os.getenv("SSUL_COMMENT_BORDER_COLOR", "#DDDDDD")
SSUL_COMMENT_BORDER_RADIUS: int = int(os.getenv("SSUL_COMMENT_BORDER_RADIUS", "15"))

# 효과음 타이밍 오프셋 (음수 = 앞당김)
SSUL_SFX_OFFSET: float = float(os.getenv("SSUL_SFX_OFFSET", "-0.15"))

# ---------------------------------------------------------------------------
# Monitoring & Alerting
# ---------------------------------------------------------------------------
MONITORING_ENABLED = os.getenv("MONITORING_ENABLED", "true").lower() == "true"
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "300"))  # 5분

# 임계값
GPU_TEMP_WARNING = int(os.getenv("GPU_TEMP_WARNING", "75"))
GPU_TEMP_CRITICAL = int(os.getenv("GPU_TEMP_CRITICAL", "80"))
DISK_USAGE_WARNING = int(os.getenv("DISK_USAGE_WARNING", "80"))
DISK_USAGE_CRITICAL = int(os.getenv("DISK_USAGE_CRITICAL", "90"))
MEMORY_USAGE_WARNING = int(os.getenv("MEMORY_USAGE_WARNING", "85"))
MEMORY_USAGE_CRITICAL = int(os.getenv("MEMORY_USAGE_CRITICAL", "95"))

# 이메일 알림 설정
EMAIL_ALERTS_ENABLED = os.getenv("EMAIL_ALERTS_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "").split(",") if os.getenv("ALERT_EMAIL_TO") else []

# 슬랙 알림 설정
SLACK_ALERTS_ENABLED = os.getenv("SLACK_ALERTS_ENABLED", "false").lower() == "true"
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
