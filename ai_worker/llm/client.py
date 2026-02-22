import json
import logging
import re

import requests

from config.settings import get_ollama_host, OLLAMA_MODEL
from db.models import ScriptData  # re-export — 기존 import 경로 호환

logger = logging.getLogger(__name__)


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
    {{"line_count": 2, "lines": ["핵심 내용 문장 1 앞부분", "핵심 내용 문장 1 뒷부분"]}},
    {{"line_count": 1, "lines": ["핵심 내용 문장 2 (21자 이하)"]}},
    {{"line_count": 1, "lines": ["(원문의 서사가 끊기지 않도록 길이에 비례하여 항목을 동적으로 확장)"]}},
    {{"line_count": 2, "lines": ["베댓 'OOO'", "계속되는 댓글 내용"]}},
    {{"line_count": 1, "lines": ["(제공된 베스트 댓글을 최대한 모두 포함)"]}}
  ],
  "closer": "여러분들의 생각은 어떤가요?",
  "title_suggestion": "원문 제목 그대로 기입 (수정 절대 금지)",
  "tags": ["태그1", "태그2", "태그3"],
  "mood": "daily"
}}

## 규칙
1. 분량 및 문맥 유지: 정해진 영상 길이에 얽매이지 마세요. 원문의 길이에 비례하여 `body` 배열의 항목 수를 유동적으로 확장해, 글의 핵심 문맥과 서사가 절대 누락되지 않도록 충분히 풀어내세요.
2. 베스트 댓글 최대 반영: 제공된 베스트 댓글은 본문만큼 중요한 대중의 반응입니다. 1개부터 최대 5개까지 주어지는 모든 댓글을 대본 후반부에 최대한 모두 포함하세요 (예: ㅇㅇ(221.144) "80억개가 아니라...").
3. 제목 절대 유지: `title_suggestion` 필드에는 입력된 '원문 제목'을 토씨 하나 바꾸지 말고 100% 그대로 기입하세요.
4. 어조 및 분위기: 원문의 감정선(유머, 분노, 감동, 슬픔 등)을 정확히 파악하여 톤을 맞추세요. 시청자에게 말하듯 친근한 구어체(~했다, ~인데, ~음)를 쓰되, 진지하거나 슬픈 사연에 'ㅋㅋ' 같은 가벼운 표현이나 억지스러운 자극을 더하는 것은 엄격히 금지합니다.
5. 화자 시점 유지: 원문 글쓴이의 1인칭 시점과 감정을 그대로 살려 스토리텔링 하세요.
6. 가독성 및 형식 제한: body 각 항목의 lines 요소는 반드시 '21자 이내'로 작성하세요. 21자를 초과하면 어절 단위로 분할하여 line_count를 늘리세요. 주의: line_count의 숫자와 lines 배열 안의 실제 문장 개수는 반드시 일치해야 하며, 구색을 맞추기 위해 빈 문자열("")을 넣는 것을 절대 금지합니다.
7. 기타: 한국어만 사용하며, 없는 사실을 지어내거나 왜곡하지 마세요.
8. 감정 분류: 글의 분위기를 아래 9가지 중 정확히 하나로 분류하여 `mood` 필드에 기입하세요. 분류 목록은 추후 확장될 수 있으므로, 제시된 값 중 가장 적합한 것을 선택하세요: touching(감동), humor(유머), anger(분노), sadness(슬픔), horror(공포), info(정보), controversy(논란), daily(일상), shock(충격)
"""

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


def _repair_json(s: str) -> str:
    """LLM이 자주 생성하는 JSON 오류를 보정한다."""
    # "value"}}, → "value"]},  (lines 배열을 ] 없이 }} 로 닫은 LLM 오류)
    s = re.sub(r'"(\s*)\}\}(\s*[,\n\r])', r'"\1]}\2', s)
    # ]] → ]} (body 항목 lines 배열 닫기 오류)
    s = re.sub(r"\]\]\s*([,\n\r])", r"]}\1", s)
    s = re.sub(r"\]\]\s*\}", r"]}\n}", s)
    # trailing comma before } or ]
    s = re.sub(r",\s*([}\]])", r"\1", s)
    # leading comma inside array: [, "text"] → ["text"]
    s = re.sub(r"\[\s*,", "[", s)
    # missing value before comma or closing brace/bracket: ": ," → ": ""," / ": }" → ": ""}"
    s = re.sub(r":\s*(?=[,}\]])", ': ""', s)
    return s


def _extract_fields_regex(raw: str) -> ScriptData | None:
    """JSON 파싱 완전 실패 시 regex로 개별 필드를 추출한다 (마지막 폴백)."""
    try:
        def _get_str(key: str) -> str:
            m = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
            return m.group(1) if m else ""

        hook  = _get_str("hook")
        closer = _get_str("closer")
        title  = _get_str("title_suggestion")
        mood   = _get_str("mood") or "daily"

        tags_m = re.search(r'"tags"\s*:\s*\[(.*?)\]', raw, re.DOTALL)
        tags: list[str] = re.findall(r'"((?:[^"\\]|\\.)*)"', tags_m.group(1)) if tags_m else []

        body: list[dict] = []
        # "closer" 를 앵커로 탐욕적 매칭 → 비탐욕 fallback
        body_m = (
            re.search(r'"body"\s*:\s*\[(.*)\]\s*,\s*"closer"', raw, re.DOTALL)
            or re.search(r'"body"\s*:\s*\[(.+?)\]\s*[,}]', raw, re.DOTALL)
        )
        if body_m:
            for item_m in re.finditer(r'\{[^{}]+\}', body_m.group(1)):
                lines_m = re.search(r'"lines"\s*:\s*\[(.*?)\]', item_m.group(0), re.DOTALL)
                if not lines_m:
                    # ] 없이 끝난 lines 배열 (}} 오류 등) — 열기부터 끝까지 문자열 직접 추출
                    lines_m = re.search(r'"lines"\s*:\s*\[(.+)', item_m.group(0), re.DOTALL)
                if lines_m:
                    item_lines = re.findall(r'"((?:[^"\\]|\\.)*)"', lines_m.group(1))
                    if item_lines:
                        body.append({"line_count": len(item_lines), "lines": item_lines})

        if hook:
            logger.info("regex 필드 추출 성공: hook=%.20s...", hook)
            return ScriptData(hook=hook, body=body, closer=closer,
                              title_suggestion=title, tags=tags, mood=mood)
    except Exception as _e:
        logger.debug("regex 추출 실패: %s", _e)
    return None


def _parse_script_json(raw: str) -> ScriptData:
    """LLM 응답에서 JSON 파싱.

    1차: 직접 파싱 → 2차: _repair_json 후 재파싱 → 3차: regex 필드 추출
    모두 실패 시 ValueError 발생.
    """
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

    d: dict | None = None
    try:
        d = json.loads(cleaned)
    except json.JSONDecodeError:
        # 2차 시도: 공통 LLM JSON 오류 보정 후 재파싱
        repaired = _repair_json(cleaned)
        try:
            d = json.loads(repaired)
            logger.info("JSON 보정 후 파싱 성공")
        except json.JSONDecodeError as e2:
            # 3차 시도: regex 개별 필드 추출
            fallback = _extract_fields_regex(raw)
            if fallback is not None:
                return fallback
            raise ValueError(
                f"LLM이 유효하지 않은 JSON을 반환했습니다 ({e2}). "
                "다시 시도하거나 모델을 확인하세요."
            ) from e2

    try:
        # body 정규화: str 항목 → dict 변환 (하위 호환)
        body_raw = list(d.get("body", []))
        body: list[dict] = []
        for item in body_raw:
            if isinstance(item, str):
                body.append({"line_count": 1, "lines": [item]})
            elif isinstance(item, dict):
                lines = item.get("lines", [])
                body.append({"line_count": len(lines), "lines": lines})
        return ScriptData(
            hook=str(d.get("hook", "")),
            body=body,
            closer=str(d.get("closer", "")),
            title_suggestion=str(d.get("title_suggestion", "")),
            tags=list(d.get("tags", [])),
            mood=str(d.get("mood", "daily")),
        )
    except (KeyError, TypeError) as e:
        raise ValueError(f"ScriptData 필드 매핑 실패: {e}") from e


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
    call_type: str = "generate_script",
) -> ScriptData:
    """구조화 대본(ScriptData) 생성.

    Args:
        extra_instructions: 프롬프트 끝에 추가할 보조 지시사항 (스타일, 톤 등).
        post_id:            LLM 로그 연결용 게시글 ID (선택).
        call_type:          LLM 로그 호출 유형 (기본 "generate_script",
                            대시보드 편집실은 "generate_script_editor").
    """
    from ai_worker.llm.logger import LLMCallTimer, log_llm_call

    model = model or OLLAMA_MODEL
    prompt = _SCRIPT_PROMPT_V2.format(
        title=title,
        body=body[:3000],
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
            raw = _call_ollama(prompt, model, num_predict=1024)
        except Exception as exc:
            success = False
            error_msg = str(exc)
            logger.error("Ollama 호출 실패: %s", exc)
            raise
        finally:
            # 성공/실패 모두 로그 기록
            log_llm_call(
                call_type=call_type,
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

    script = _parse_script_json(raw)
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


def call_ollama_raw(
    prompt: str,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.5,
) -> str:
    """범용 Ollama API 호출. JSON 파싱 없이 원시 응답 반환.

    Args:
        prompt: 프롬프트 전체 텍스트
        model: Ollama 모델명 (None이면 기본값)
        max_tokens: 최대 토큰 수
        temperature: 샘플링 온도

    Returns:
        LLM 원시 응답 텍스트
    """
    _model = model or OLLAMA_MODEL
    raw = _call_ollama(prompt, _model, num_predict=max_tokens)
    return raw
