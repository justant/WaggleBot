"""성과 기반 피드백 루프.

YouTube 성과 데이터 → LLM 구조화 분석 → pipeline 설정 자동 반영.

흐름:
    1. collect_analytics()  : UPLOADED 포스트의 YouTube 통계 수집 → upload_meta 저장
    2. build_performance_summary() : DB에서 성과 데이터 집계
    3. generate_structured_insights() : LLM으로 JSON 인사이트 생성
    4. apply_feedback()     : 인사이트를 feedback_config.json 에 저장
       → processor.py가 next LLM 대본 생성 시 extra_instructions 주입
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from config.settings import FEEDBACK_CONFIG_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 기본값
# ---------------------------------------------------------------------------

_FEEDBACK_DEFAULTS: dict = {
    "version": 1,
    "updated_at": None,
    "extra_instructions": "",
    "mood_weights": {
        "shocking": 1.0,
        "funny": 1.0,
        "serious": 1.0,
        "heartwarming": 1.0,
    },
    "subtitle_style": "default",
}

# LLM 구조화 인사이트 프롬프트
_STRUCTURED_PROMPT = """\
당신은 유튜브 쇼츠 채널 성과 분석 전문가입니다.
아래 영상 성과 데이터를 바탕으로 대본 생성 파이프라인에 자동 반영할 구조화 설정을 JSON으로 출력하세요.
반드시 JSON만 출력하고 다른 텍스트는 포함하지 마세요.

## 영상 성과 데이터 (상위→하위 정렬)
{data}

## 출력 형식
{{
  "extra_instructions": "대본 작가를 위한 구체적 지시 (2~3문장, 한국어, 성과 패턴 기반)",
  "mood_weights": {{
    "shocking": 1.0,
    "funny": 1.0,
    "serious": 1.0,
    "heartwarming": 1.0
  }},
  "subtitle_style": "default"
}}

## 규칙
- extra_instructions: 상위 영상의 hook/body/스타일 패턴을 분석해 다음 대본에 반영할 구체적 지시
- mood_weights: 0.5 ~ 2.0 범위. 조회수 높은 mood → 높게, 낮은 mood → 낮게
- subtitle_style: "default" | "impact" | "minimal" 중 성과 좋은 스타일 선택
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_feedback_config() -> dict:
    """피드백 설정 파일 로드. 파일 없으면 기본값 반환."""
    if FEEDBACK_CONFIG_PATH.exists():
        try:
            with open(FEEDBACK_CONFIG_PATH, encoding="utf-8") as f:
                return {**_FEEDBACK_DEFAULTS, **json.load(f)}
        except Exception:
            logger.warning("feedback_config.json 파싱 실패 — 기본값 사용", exc_info=True)
    return dict(_FEEDBACK_DEFAULTS)


def save_feedback_config(cfg: dict) -> None:
    """피드백 설정을 JSON 파일에 저장."""
    FEEDBACK_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cfg["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(FEEDBACK_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    logger.info("피드백 설정 저장 완료: %s", FEEDBACK_CONFIG_PATH)


def build_performance_summary(session, days_back: int = 30) -> list[dict]:
    """DB에서 성과 데이터를 집계해 반환한다.

    Returns:
        [{"title", "mood", "views", "likes", "comments", "variant_label"}, ...]
        조회수 내림차순 정렬.
    """
    from datetime import timedelta
    from db.models import Post, Content, PostStatus

    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    rows = (
        session.query(Post, Content)
        .join(Content, Content.post_id == Post.id)
        .filter(
            Post.status == PostStatus.UPLOADED,
            Post.updated_at >= cutoff,
        )
        .all()
    )

    results: list[dict] = []
    for post, content in rows:
        meta = content.upload_meta or {}
        yt = meta.get("youtube", {})
        analytics = yt.get("analytics", {})

        script = content.get_script()
        mood = script.mood if script else "unknown"

        results.append({
            "post_id": post.id,
            "title": post.title,
            "mood": mood,
            "views": analytics.get("views", 0),
            "likes": analytics.get("likes", 0),
            "comments": analytics.get("comments", 0),
            "avg_watch_pct": analytics.get("avg_watch_pct", 0.0),
            "variant_label": content.variant_label,
            "variant_group": content.variant_group,
        })

    results.sort(key=lambda r: r["views"], reverse=True)
    return results


def generate_structured_insights(
    performance_data: list[dict],
    llm_model: str | None = None,
) -> dict:
    """LLM으로 구조화된 피드백 인사이트를 생성한다.

    Args:
        performance_data: build_performance_summary() 결과
        llm_model: Ollama 모델명 (None이면 기본값 사용)

    Returns:
        {"extra_instructions": str, "mood_weights": dict, "subtitle_style": str}
    """
    from ai_worker.llm import call_ollama_raw

    if not performance_data:
        logger.warning("성과 데이터 없음 — 인사이트 생성 불가")
        return {}

    data_lines = []
    for i, r in enumerate(performance_data[:15], 1):
        line = (
            f"{i}. [{r['mood']}] {r['title'][:50]} "
            f"| 조회수 {r['views']:,} | 좋아요 {r['likes']:,}"
        )
        if r.get("avg_watch_pct"):
            line += f" | 시청유지율 {r['avg_watch_pct']:.1f}%"
        data_lines.append(line)

    prompt = _STRUCTURED_PROMPT.format(data="\n".join(data_lines))

    raw = call_ollama_raw(prompt, model=llm_model, max_tokens=512, temperature=0.5)

    # JSON 파싱
    cleaned = re.sub(r"```json\s*", "", raw)
    cleaned = re.sub(r"```\s*", "", cleaned).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        insights = json.loads(cleaned)
        logger.info("구조화 인사이트 생성 완료: %s", str(insights)[:100])
        return insights
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning("인사이트 JSON 파싱 실패 (%s) — 원문 반환", e)
        return {"extra_instructions": raw[:500], "mood_weights": {}, "subtitle_style": "default"}


def apply_feedback(insights: dict) -> None:
    """인사이트를 feedback_config.json 에 병합 저장한다.

    mood_weights는 현재 값에 새 값을 덮어쓰고, extra_instructions는 교체한다.
    """
    current = load_feedback_config()

    if insights.get("extra_instructions"):
        current["extra_instructions"] = insights["extra_instructions"]

    if insights.get("mood_weights"):
        for mood, w in insights["mood_weights"].items():
            # 0.5 ~ 2.0 범위 클램핑
            current["mood_weights"][mood] = max(0.5, min(2.0, float(w)))

    if insights.get("subtitle_style"):
        current["subtitle_style"] = insights["subtitle_style"]

    save_feedback_config(current)
    logger.info(
        "피드백 반영 완료: extra_instructions=%s...",
        current["extra_instructions"][:50],
    )
