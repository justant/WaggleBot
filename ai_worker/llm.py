import dataclasses
import json
import logging
import re
from dataclasses import dataclass

import requests

from config.settings import OLLAMA_HOST, OLLAMA_MODEL

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ScriptData — 구조화 대본
# ---------------------------------------------------------------------------

@dataclass
class ScriptData:
    hook: str
    body: list[str]
    closer: str
    title_suggestion: str
    tags: list[str]
    mood: str = "funny"  # funny | serious | shocking | heartwarming (레거시 호환용)

    def to_plain_text(self) -> str:
        return " ".join([self.hook] + self.body + [self.closer])

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "ScriptData":
        d = json.loads(s)
        # 레거시 JSON에 mood 없을 수 있으므로 기본값 처리
        return cls(
            hook=d["hook"],
            body=d["body"],
            closer=d["closer"],
            title_suggestion=d["title_suggestion"],
            tags=d["tags"],
            mood=d.get("mood", "funny"),
        )


# ---------------------------------------------------------------------------
# 프롬프트 템플릿
# ---------------------------------------------------------------------------

_SCRIPT_PROMPT_V2 = """\
당신은 유튜브 쇼츠 대본 작가입니다.
아래 입력을 읽고, 반드시 JSON 형식으로만 응답하세요. 다른 텍스트는 절대 포함하지 마세요.

## 입력
- 제목: {title}
- 본문: {body}
- 베스트 댓글: {comments}

## 출력 형식 (JSON)
{{
  "hook": "시청자가 스크롤을 멈출 한 줄 (15자 이내, 의문형 또는 감탄형)",
  "body": [
    "핵심 내용 문장 1",
    "핵심 내용 문장 2",
    "핵심 내용 문장 3",
    "베스트 댓글 인용: 'OOO'"
  ],
  "closer": "공감 유도 마무리 + 구독/좋아요 CTA",
  "title_suggestion": "YouTube 쇼츠 제목 (50자 이내, 이모지 포함)",
  "tags": ["태그1", "태그2", "태그3"]
}}

## 규칙
- 총 분량: TTS로 읽었을 때 40~55초 분량
- 말투: 반말, ~했다/~인데/~ㅋㅋ 구어체
- 베스트 댓글 최소 1개 인용 필수 (따옴표로 표시)
- body는 정확히 4개 항목
- 자극적이되 사실 왜곡 금지"""


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _call_ollama(prompt: str, model: str, num_predict: int = 400) -> str:
    url = f"{OLLAMA_HOST}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": num_predict, "temperature": 0.7},
    }
    resp = requests.post(url, json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _parse_script_json(raw: str, fallback_text: str) -> ScriptData:
    """LLM 응답에서 JSON 파싱. 실패 시 fallback ScriptData 반환."""
    # ```json ... ``` 블록 제거
    cleaned = re.sub(r"```json\s*", "", raw)
    cleaned = re.sub(r"```\s*", "", cleaned)
    cleaned = cleaned.strip()

    # 첫 { ... } 블록 추출
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        cleaned = match.group(0)

    try:
        d = json.loads(cleaned)
        return ScriptData(
            hook=str(d.get("hook", "")),
            body=list(d.get("body", [])),
            closer=str(d.get("closer", "")),
            title_suggestion=str(d.get("title_suggestion", "")),
            tags=list(d.get("tags", [])),
            mood=str(d.get("mood", "funny")),
        )
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.warning("ScriptData JSON 파싱 실패 (%s) — fallback 사용", e)
        return ScriptData(
            hook=fallback_text,
            body=[],
            closer="",
            title_suggestion="",
            tags=[],
            mood="funny",
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_script(
    title: str,
    body: str,
    comments: list[str],
    *,
    model: str | None = None,
) -> ScriptData:
    """구조화 대본(ScriptData) 생성."""
    model = model or OLLAMA_MODEL
    prompt = _SCRIPT_PROMPT_V2.format(
        title=title,
        body=body[:2000],
        comments="\n".join(f"- {c}" for c in comments[:5]),
    )

    logger.info("Ollama 대본 생성 요청: model=%s", model)
    raw = _call_ollama(prompt, model, num_predict=512)
    logger.info("Ollama 응답 수신: %d자", len(raw))

    script = _parse_script_json(raw, fallback_text=raw)
    logger.info("대본 생성 완료: hook=%s...", script.hook[:30])
    return script


def summarize(
    title: str,
    body: str,
    comments: list[str],
    *,
    model: str | None = None,
) -> str:
    """하위 호환: ScriptData.to_plain_text() 반환."""
    script = generate_script(title, body, comments, model=model)
    return script.to_plain_text()
