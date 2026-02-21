"""스타일 프리셋 관리."""

import json

from config.settings import load_pipeline_config, save_pipeline_config

_DEFAULT_STYLE_PRESETS: list[dict] = [
    {"name": "기본 (쇼츠 최적화)", "prompt": ""},
    {"name": "자극적", "prompt": "최대한 자극적이고 충격적인 표현을 사용하라. 감탄사와 강렬한 단어로 시작하라."},
    {"name": "공감형", "prompt": "시청자가 깊이 공감할 수 있는 감성적 접근. 따뜻하고 진정성 있는 말투."},
    {"name": "유머러스", "prompt": "가볍고 재미있는 말투, ㅋㅋ/ㄷㄷ 구어체 활용, 이모티콘 1~2개 포함."},
    {"name": "뉴스형", "prompt": "뉴스 앵커 스타일, 객관적 서술, 중립적 어조."},
]


def load_style_presets() -> list[dict]:
    """pipeline.json에서 스타일 프리셋 로드. 없으면 기본값 반환."""
    raw = load_pipeline_config().get("style_presets")
    if raw:
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return list(_DEFAULT_STYLE_PRESETS)


def save_style_presets(presets: list[dict]) -> None:
    """스타일 프리셋을 pipeline.json에 저장."""
    cfg = load_pipeline_config()
    cfg["style_presets"] = json.dumps(presets, ensure_ascii=False)
    save_pipeline_config(cfg)
