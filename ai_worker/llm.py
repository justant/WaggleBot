import dataclasses
import json
import logging
import re
from dataclasses import dataclass

import requests

from config.settings import get_ollama_host, OLLAMA_MODEL

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
    "베댓 'OOO' 1",
    "베댓 'OOO' 2"
  ],
  "closer": "여러분들의 생각은 어떤가요?",
  "title_suggestion": "YouTube 쇼츠 제목 (50자 이내, 이모지 포함)",
  "tags": ["태그1", "태그2", "태그3"]
}}

## 규칙
- 총 분량: TTS로 읽었을 때 40~55초 분량
- 말투: 반말, ~했다/~인데/~ㅋㅋ/~했음/~함 구어체
- 베스트 댓글 최소 1개 인용 필수 (따옴표로 표시)
- body는 정확히 4개 항목
- 자극적이되 사실 왜곡 금지"""


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _call_ollama(prompt: str, model: str, num_predict: int = 400) -> str:
    url = f"{get_ollama_host()}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": num_predict, "temperature": 0.7},
    }
    resp = requests.post(url, json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _fix_control_chars(s: str) -> str:
    """JSON 문자열 리터럴 내부의 제어 문자를 이스케이프 시퀀스로 변환."""
    result: list[str] = []
    in_string = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and in_string:
            result.append(c)
            i += 1
            if i < len(s):
                result.append(s[i])
            i += 1
            continue
        if c == '"':
            in_string = not in_string
            result.append(c)
        elif in_string and c == "\n":
            result.append("\\n")
        elif in_string and c == "\r":
            result.append("\\r")
        elif in_string and c == "\t":
            result.append("\\t")
        elif in_string and ord(c) < 0x20:
            result.append(f"\\u{ord(c):04x}")
        else:
            result.append(c)
        i += 1
    return "".join(result)


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

    # JSON 문자열 내 제어 문자 정규화
    cleaned = _fix_control_chars(cleaned)

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
    extra_instructions: str | None = None,
    post_id: int | None = None,
) -> ScriptData:
    """구조화 대본(ScriptData) 생성.

    Args:
        extra_instructions: 프롬프트 끝에 추가할 보조 지시사항 (스타일, 톤 등).
        post_id:            LLM 로그 연결용 게시글 ID (선택).
    """
    from ai_worker.llm_logger import LLMCallTimer, log_llm_call

    model = model or OLLAMA_MODEL
    prompt = _SCRIPT_PROMPT_V2.format(
        title=title,
        body=body[:2000],
        comments="\n".join(f"- {c}" for c in comments[:5]),
    )

    if extra_instructions and extra_instructions.strip():
        prompt += f"\n\n## 추가 지시사항\n{extra_instructions.strip()}"

    logger.info("Ollama 대본 생성 요청: model=%s (extra=%s)", model, bool(extra_instructions))

    raw = ""
    success = True
    error_msg: str | None = None
    script: ScriptData | None = None

    with LLMCallTimer() as timer:
        try:
            raw = _call_ollama(prompt, model, num_predict=512)
        except Exception as exc:
            success = False
            error_msg = str(exc)
            logger.error("Ollama 호출 실패: %s", exc)
            raise
        finally:
            # 성공/실패 모두 로그 기록
            log_llm_call(
                call_type="generate_script",
                post_id=post_id,
                model_name=model,
                prompt_text=prompt,
                raw_response=raw,
                parsed_result=None,   # 파싱 후 아래서 갱신 불가 — None 허용
                image_count=0,
                content_length=len(body),
                success=success,
                error_message=error_msg,
                duration_ms=timer.elapsed_ms,
            )

    logger.info("Ollama 응답 수신: %d자 (%dms)", len(raw), timer.elapsed_ms)

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
