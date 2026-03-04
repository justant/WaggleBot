"""ai_worker/script/parser.py — LLM 응답 JSON 파싱·복구"""

import json
import logging
import re

from db.models import ScriptData

logger = logging.getLogger(__name__)


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
                        # type / author 추출 (하위 호환: 없으면 "body")
                        type_m = re.search(r'"type"\s*:\s*"((?:[^"\\]|\\.)*)"', item_m.group(0))
                        author_m = re.search(r'"author"\s*:\s*"((?:[^"\\]|\\.)*)"', item_m.group(0))
                        entry: dict = {
                            "type": type_m.group(1) if type_m else "body",
                            "line_count": len(item_lines),
                            "lines": item_lines,
                        }
                        if author_m:
                            entry["author"] = author_m.group(1)
                        body.append(entry)

        if hook:
            logger.info("regex 필드 추출 성공: hook=%.20s...", hook)
            return ScriptData(hook=hook, body=body, closer=closer,
                              title_suggestion=title, tags=tags, mood=mood)
    except Exception as _e:
        logger.debug("regex 추출 실패: %s", _e)
    return None


def parse_script_json(raw: str) -> ScriptData:
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
                body.append({"type": "body", "line_count": 1, "lines": [item]})
            elif isinstance(item, dict):
                lines = item.get("lines", [])
                block_type = item.get("type", "body")  # 하위 호환: 없으면 "body"
                author = item.get("author")             # comment일 때만 존재
                entry = {
                    "type": block_type,
                    "line_count": len(lines),
                    "lines": lines,
                }
                if author:
                    entry["author"] = author
                body.append(entry)
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
