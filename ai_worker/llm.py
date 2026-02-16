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
    mood: str  # funny | serious | shocking | heartwarming

    def to_plain_text(self) -> str:
        return " ".join([self.hook] + self.body + [self.closer])

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "ScriptData":
        d = json.loads(s)
        return cls(**d)


# ---------------------------------------------------------------------------
# 프롬프트 템플릿
# ---------------------------------------------------------------------------

_SCRIPT_PROMPT_V2 = """\
당신은 한국어 숏츠 영상 대본 전문가입니다.
아래 게시글과 베스트 댓글을 읽고, 반드시 아래 JSON 형식으로만 응답하세요.
다른 텍스트(설명, 마크다운 코드블록 제외)는 절대 포함하지 마세요.

JSON 형식:
{{
  "hook": "시청자를 즉시 사로잡는 첫 문장 (30자 내외, 반말)",
  "body": ["본문 문장1", "본문 문장2", "본문 문장3"],
  "closer": "마무리 한 줄 (30자 내외, 반말)",
  "title_suggestion": "유튜브 제목 제안 (30자 내외)",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"],
  "mood": "funny"
}}

mood 값은 funny / serious / shocking / heartwarming 중 하나만 사용.
body는 2~4개 문장으로 구성. 전체 글자 수 200자 내외.

---
[제목]
{title}

[본문]
{body}

[베스트 댓글]
{comments}
---"""

_SUMMARY_PROMPT_TEMPLATE = """\
당신은 한국어 쇼츠 영상 대본 작가입니다.
아래 커뮤니티 게시글과 베스트 댓글을 읽고, 200자 내외의 재미있는 쇼츠 대본을 작성하세요.

규칙:
- 반말 사용, 친근한 톤
- 핵심만 간결하게 전달
- 시청자의 흥미를 유발하는 도입부
- 200자 내외 (최대 250자)

---
[제목]
{title}

[본문]
{body}

[베스트 댓글]
{comments}
---

대본:"""


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
    raw = _call_ollama(prompt, model, num_predict=400)
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
