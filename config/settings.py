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

NATE_PANN_SECTIONS = [
    {"name": "톡톡 베스트", "url": "https://pann.nate.com/talk/ranking"},
    {"name": "톡커들의 선택", "url": "https://pann.nate.com/talk/ranking/best"},
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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "eeve-korean:10.8b")
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

_PIPELINE_DEFAULTS: dict[str, str] = {
    "tts_engine": "edge-tts",
    "tts_voice": "ko-KR-SunHiNeural",
    "llm_model": "eeve-korean:10.8b",
    "video_resolution": "1080x1920",
    "video_codec": "h264_nvenc",
    "bgm_volume": "0.15",
    "subtitle_font": "NanumGothic",
    "upload_platforms": '["youtube"]',
    "upload_privacy": "unlisted",
    "youtube_credentials_path": "config/youtube_credentials.json",
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
