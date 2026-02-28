import json
import os
import time as _time_cfg
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# re-export — 기존 import 경로 호환
# ---------------------------------------------------------------------------
from config.crawler import (  # noqa: E402
    BLOCK_RETRY_BACKOFF,
    BLOCK_RETRY_BASE_DELAY,
    BLOCK_RETRY_MAX,
    BLOCK_RETRY_MAX_DELAY,
    BROWSER_PROFILES,
    CRAWL_DELAY_COMMENT,
    CRAWL_DELAY_POST,
    CRAWL_DELAY_SECTION,
    CRAWL_INTERVAL_HOURS,
    ENABLED_CRAWLERS,
    REQUEST_HEADERS,
    REQUEST_TIMEOUT,
    USER_AGENTS,
)
from config.monitoring import (  # noqa: E402
    MONITORING_ENABLED,
    HEALTH_CHECK_INTERVAL,
    GPU_TEMP_WARNING,
    GPU_TEMP_CRITICAL,
    DISK_USAGE_WARNING,
    DISK_USAGE_CRITICAL,
    MEMORY_USAGE_WARNING,
    MEMORY_USAGE_CRITICAL,
    EMAIL_ALERTS_ENABLED,
    SMTP_HOST,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    ALERT_EMAIL_TO,
    SLACK_ALERTS_ENABLED,
    SLACK_WEBHOOK_URL,
)

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://wagglebot:wagglebot@localhost/wagglebot",
)

STREAMLIT_PORT = int(os.getenv("STREAMLIT_PORT", "8501"))

# ---------------------------------------------------------------------------
# AI Worker
# ---------------------------------------------------------------------------
AI_POLL_INTERVAL = int(os.getenv("AI_POLL_INTERVAL", "10"))
# CUDA 세마포어 동시성 제한 (2막 TTS+VIDEO 병렬 실행 시 2로 전환 가능, 기본 1 = 순차)
CUDA_CONCURRENCY = int(os.getenv("CUDA_CONCURRENCY", "1"))
MAX_RETRY_COUNT = int(os.getenv("MAX_RETRY_COUNT", "3"))
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
# OLLAMA_MODEL 기본값: "qwen2.5:14b"
#
# 사용 가능한 모델 옵션 (RTX 3090 24GB 기준):
# - qwen2.5:7b  (4-bit, ~4.5GB)  — 경량, 빠른 처리
# - qwen2.5:7b  (8-bit, ~7.0GB)  — 균형잡힌 품질
# - qwen2.5:14b (4-bit, ~9.0GB)  — 고품질, 경량 양자화
# - qwen2.5:14b (8-bit, ~14.0GB) — ★ 최고 품질 (Quality-First 권장)
# 주의: 14b 8-bit 사용 시 TTS/VIDEO와 동시 로드 불가 → 2막 구조 필수
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")


def get_ollama_host() -> str:
    """Ollama 호스트를 호출 시점의 환경변수에서 읽는다 (컨테이너 재시작 없이도 반영)."""
    return os.getenv("OLLAMA_HOST", "http://localhost:11434")
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", str(_PROJECT_ROOT / "media")))
ASSETS_DIR = Path(os.getenv("ASSETS_DIR", str(_PROJECT_ROOT / "assets")))

# TTS 엔진별 목소리 프리셋
TTS_VOICES: dict[str, list[dict[str, str]]] = {
    "fish-speech": [
        {"id": "default", "name": "기본 남성 내레이터"},
        {"id": "anna",    "name": "Anna (여, 친근한 내레이션)"},
        {"id": "han",     "name": "Han (남, 자연스러운 대화체)"},
        {"id": "krys",    "name": "Krys (여, 뉴스/정보 전달형)"},
        {"id": "sunny",   "name": "Sunny (여, 따뜻한 내레이션)"},
        {"id": "yohan",   "name": "Yohan (남, 깊이 있는 내레이션)"},
        {"id": "yura",    "name": "Yura (여, 활기찬 대화체)"},
        {"id": "manbo",   "name": "Manbo (여, 유쾌한 대화체)"},
    ],
    "edge-tts": [
        {"id": "ko-KR-SunHiNeural",   "name": "선히 (여, 밝음)"},
        {"id": "ko-KR-InJoonNeural",   "name": "인준 (남, 차분)"},
        {"id": "ko-KR-HyunsuNeural",   "name": "현수 (남, 뉴스)"},
        {"id": "ko-KR-BongJinNeural",  "name": "봉진 (남, 따뜻)"},
        {"id": "ko-KR-GookMinNeural",  "name": "국민 (남, 밝음)"},
    ],
}

# ---------------------------------------------------------------------------
# Pipeline config (JSON 파일)
# ---------------------------------------------------------------------------
_PIPELINE_CONFIG_PATH = _PROJECT_ROOT / "config" / "pipeline.json"
FEEDBACK_CONFIG_PATH = _PROJECT_ROOT / "config" / "feedback_config.json"
AB_TEST_CONFIG_PATH  = _PROJECT_ROOT / "config" / "ab_tests.json"

_PIPELINE_DEFAULTS: dict[str, str] = {
    # 모델 관련 기본값 — .env의 값을 읽음. 변경 시 .env만 수정하면 됨.
    "tts_engine": os.getenv("TTS_ENGINE", "fish-speech"),
    "tts_voice":  os.getenv("TTS_VOICE",  "yura"),
    "llm_model":  OLLAMA_MODEL,  # .env의 OLLAMA_MODEL
    "video_resolution": "1080x1920",
    "video_codec": "h264_nvenc",
    "bgm_volume": "0.15",
    "subtitle_font": "NanumGothic",
    "upload_platforms": '["youtube"]',
    "upload_privacy": "unlisted",
    "youtube_credentials_path": "config/youtube_credentials.json",
    "youtube_client_secret_path": "secrets/client_secret.json",
    "tiktok_client_secret_path": "secrets/tiktok_client.json",
    # 수신함 자동 승인
    "auto_approve_enabled": "false",
    "auto_approve_threshold": "80",
    # LLM 파이프라인 (5-Phase content_processor 활성화 여부)
    "use_content_processor": "false",
    # 자동 업로드
    "auto_upload": "false",
}


def get_pipeline_defaults() -> dict[str, str]:
    """파이프라인 기본 설정값 반환."""
    return dict(_PIPELINE_DEFAULTS)


_pipeline_config_cache: dict = {"data": None, "ts": 0.0}
_PIPELINE_CONFIG_TTL = 5  # 5초 캐싱 — 매 rerun마다 디스크 읽기 방지


def load_pipeline_config() -> dict[str, str]:
    """pipeline.json 로드 (5초 인메모리 캐싱으로 반복 파일 I/O 방지)."""
    _now = _time_cfg.time()
    if (
        _pipeline_config_cache["data"] is not None
        and _now - _pipeline_config_cache["ts"] < _PIPELINE_CONFIG_TTL
    ):
        return dict(_pipeline_config_cache["data"])
    if _PIPELINE_CONFIG_PATH.exists():
        with open(_PIPELINE_CONFIG_PATH, encoding="utf-8") as f:
            data = {**_PIPELINE_DEFAULTS, **json.load(f)}
    else:
        data = dict(_PIPELINE_DEFAULTS)
    _pipeline_config_cache.update({"data": data, "ts": _now})
    return dict(data)


def save_pipeline_config(cfg: dict[str, str]) -> None:
    _PIPELINE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PIPELINE_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    # 캐시 즉시 무효화 — 저장 후 바로 반영되도록
    _pipeline_config_cache.update({"data": None, "ts": 0.0})

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
# Layout constraints (layout.json 단일 소스)
# ---------------------------------------------------------------------------
_LAYOUT_CONFIG_PATH = _PROJECT_ROOT / "config" / "layout.json"
with open(_LAYOUT_CONFIG_PATH, encoding="utf-8") as _f:
    _layout_cfg = json.load(_f)

CONSTRAINTS: dict = _layout_cfg.get("constraints", {})

MAX_TITLE_CHARS: int   = CONSTRAINTS.get("post_title",      {}).get("max_chars", 40)
MAX_HOOK_CHARS: int    = CONSTRAINTS.get("hook_text",        {}).get("max_chars", 50)
MAX_BODY_CHARS: int    = CONSTRAINTS.get("body_sentence",    {}).get("max_chars", 45)
MAX_CAPTION_CHARS: int = CONSTRAINTS.get("img_text_caption", {}).get("max_chars", 60)


def get_llm_constraints_prompt() -> str:
    """LLM 프롬프트에 삽입할 텍스트 길이 제약 문자열."""
    return (
        f"## 텍스트 길이 제약 (엄수 필수)\n"
        f"- 후킹 문장: 최대 {MAX_HOOK_CHARS}자\n"
        f"- 본문 한 줄: 최대 {MAX_BODY_CHARS}자\n"
        f"- 이미지 캡션: 최대 {MAX_CAPTION_CHARS}자\n"
        f"\n**초과 시 화면에서 잘립니다.**"
    )


# ────────────────────────────────────────────
# TTS — Fish Speech 1.5
# ────────────────────────────────────────────
FISH_SPEECH_URL = os.getenv("FISH_SPEECH_URL", "http://fish-speech:8080")
FISH_SPEECH_TIMEOUT = 120  # seconds (4B 모델 첫 생성 느림, 동시 요청 시 write 대기 포함)

# 참조 오디오 프리셋
# key: voice_key, value: assets/voices/ 내 파일명
VOICE_PRESETS: dict[str, str] = {
    "default": "korean_man_default.wav",
    "anna":    "voice_preview_anna.mp3",
    "han":     "voice_preview_han.mp3",
    "krys":    "voice_preview_krys.mp3",
    "sunny":   "voice_preview_sunny.mp3",
    "yohan":   "voice_preview_yohan.mp3",
    "yura":    "voice_preview_yura.mp3",
    "manbo":   "voice_preview_manbo.mp3",
}
VOICE_DEFAULT = "default"

# 참조 오디오 스크립트 (fish-speech가 언어를 인식하는 데 사용)
# 실제 녹음 내용과 정확히 일치할수록 음성 클로닝 품질이 좋아짐
# 정확한 스크립트를 모를 경우 한국어 샘플 텍스트라도 반드시 입력 (빈 문자열 → 중국어 출력)
VOICE_REFERENCE_TEXTS: dict[str, str] = {
    "default": "안녕하세요. 반갑습니다. 오늘도 좋은 하루 되세요.",
    "anna":  "안녕하세요, 오늘 여러분과 나눌 이야기는 그 누구에게도 들어볼 수 없었던 신기한 이야기입니다. 이야기를 들어보시기 전에 먼저 구독과 좋아요 부탁드립니다.",
    "han":   "떡볶이가 먹고 싶은 영감은 그냥 찾아와요. 내가 할 수 있는 건 자유의지라고 굳이 부르자면 떡볶이를 먹고 싶은 나를 받아들일까 말까 그게 내가 할 수 있는 유일한 것이고 내가 나를 보살피기 위한 유일한 게 그거 딱 하나라는 거를 요즘에 좀 실천하고 있습니다.",
    "krys":  "안녕하세요. 유익하고 좋은 내용을 알기 쉽고 빠르게 전해드리겠습니다. 오늘 전해드릴 소식은요, 여러분들이 가장 필요한 정보들로 준비해 보았는데요. 먼저 구독과 좋아요 부탁드립니다.",
    "sunny": "인생은 늘 예측할 수 없는 방향으로 흐르지만 그 속에서 희망과 기회를 찾을 수 있습니다. 행복은 거창한 순간에만 있는 것이 아니라 매일의 작은 순간 속에서 피어납니다. 오늘도 감사하는 마음으로 하루를 보냅니다.",
    "yohan": "여러분, 우리가 하루를 살면서 수많은 생각과 경험을 합니다. 그런데 그 중 몇 가지를 진짜로 기억하고 있나요? 기록은 단순히 정보를 적는 행위가 아닙니다. 삶의 흔적을 남기는 일입니다. 지나간 시간의 소중한 순간, 깨달음, 감정을 미래의 나에게 전달하는 다리입니다.",
    "yura":  "제가 요즘 매일 쓰고 있는 게 바로 이거예요. 처음엔 그냥 궁금해서 써봤는데 왜 이렇게 인기인지 알겠더라고요. 아침에 바쁘잖아요, 그냥 툭 사용하면 끝. 아래 링크에서 확인해 보세요. 할인도 들어가 있어서 놓치면 후회할 수도 있어요. 다음 영상에서 또 꿀템 들고 올게요.",
    "manbo":  "여돌 중에 제일 이쁘지 않음? 키키 하음 발언이 여돌 중에 제일 이물감 없고 유니크하다고 함 특히 금발 생머리가 잘 어울림 성격도 웃기고 몸선 춤선도 예쁘다고 함",
}

# 감정 태그 매핑 — Fish Speech 1.5는 (tag) 형식 미지원, 참조 오디오로 톤 결정
# 향후 지원 모델 전환 시 재활성화 예정
EMOTION_TAGS: dict[str, str] = {
    "intro":     "",
    "img_text":  "",
    "text_only": "",
    "outro":     "",
}

# 오디오 출력 설정
TTS_OUTPUT_FORMAT = "wav"
TTS_SAMPLE_RATE   = 44100

# ---------------------------------------------------------------------------
# LTX-Video 설정
# ---------------------------------------------------------------------------
COMFYUI_URL: str = os.getenv("COMFYUI_URL", "http://comfyui:8188")
VIDEO_GEN_ENABLED: bool = os.getenv("VIDEO_GEN_ENABLED", "false").lower() == "true"
VIDEO_GEN_TIMEOUT: int = int(os.getenv("VIDEO_GEN_TIMEOUT", "300"))
VIDEO_RESOLUTION: tuple[int, int] = (512, 512)
VIDEO_RESOLUTION_FALLBACK: tuple[int, int] = (384, 384)
VIDEO_NUM_FRAMES: int = 81           # ~3.2초 @25fps
VIDEO_NUM_FRAMES_FALLBACK: int = 61  # 다운그레이드용
VIDEO_STEPS: int = 20
VIDEO_CFG_SCALE: float = 3.5
VIDEO_I2V_THRESHOLD: float = 0.6     # image_filter 적합성 임계값
VIDEO_I2V_DENOISE: float = 0.75
VIDEO_MAX_CLIPS_PER_POST: int = 8    # 글당 최대 생성 클립 수
VIDEO_MAX_RETRY: int = 3             # 씬당 최대 재시도 횟수
VIDEO_OUTPUT_DIR: str = os.getenv("VIDEO_OUTPUT_DIR", str(MEDIA_DIR / "tmp" / "videos"))


def get_comfyui_url() -> str:
    """ComfyUI 서버 URL을 반환한다."""
    return COMFYUI_URL


def load_video_styles() -> dict:
    """config/video_styles.json을 로드한다."""
    _path = Path(__file__).parent / "video_styles.json"
    with open(_path, encoding="utf-8") as _vf:
        return json.load(_vf)
