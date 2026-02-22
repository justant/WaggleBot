"""Phase 3: 물리적 검증 (Hard Limit Enforcement)

LLM 출력을 max_chars 기준으로 검증하고,
초과 문장을 한국어 자연 단위 우선순위에 따라 분할 보정한다.

분할 우선순위: 문장부호 > 쉼표 > 접속사 > 어절 > 강제분할
"""
import logging
from typing import Sequence

from config.settings import MAX_BODY_CHARS, MAX_HOOK_CHARS

logger = logging.getLogger(__name__)

_CONNECTORS = ["근데 ", "그래서 ", "그런데 ", "하지만 ", "그리고 ", "그래도 ", "그러면 "]


def smart_split_korean(text: str, max_chars: int = MAX_BODY_CHARS) -> list[str]:
    """한글 텍스트를 자연스러운 단위로 분할한다.

    우선순위: 문장부호 > 쉼표 > 접속사 > 어절 > 강제분할

    Args:
        text:      분할 대상 텍스트
        max_chars: 최대 글자 수 (기본: MAX_BODY_CHARS)

    Returns:
        분할된 문자열 목록 (항상 1개 이상)
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    remaining = text

    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        cut = False

        # 1순위: 문장 종결 부호 (. ? !)
        for sep in [". ", "? ", "! "]:
            pos = remaining[:max_chars].rfind(sep)
            if pos > max_chars * 0.6:
                chunks.append(remaining[: pos + 1].strip())
                remaining = remaining[pos + 1:].strip()
                cut = True
                break

        if cut:
            continue

        # 2순위: 쉼표
        pos = remaining[:max_chars].rfind(", ")
        if pos > max_chars * 0.6:
            chunks.append(remaining[: pos + 1].strip())
            remaining = remaining[pos + 1:].strip()
            continue

        # 3순위: 접속사
        best = max(
            (remaining[:max_chars].rfind(c) for c in _CONNECTORS),
            default=-1,
        )
        if best > max_chars * 0.5:
            chunks.append(remaining[:best].strip())
            remaining = remaining[best:].strip()
            continue

        # 4순위: 어절(띄어쓰기)
        pos = remaining[:max_chars].rfind(" ")
        if pos > 0:
            chunks.append(remaining[:pos].strip())
            remaining = remaining[pos:].strip()
            continue

        # 5순위: 강제 분할
        chunks.append(remaining[:max_chars])
        remaining = remaining[max_chars:]

    return [c for c in chunks if c]


def validate_and_fix(llm_output: dict) -> dict:
    """LLM 출력을 검증하고, max_chars 초과 lines를 분할 보정한다.

    Args:
        llm_output: {
            "hook": str,
            "body": list[dict],  # [{"line_count": int, "lines": list[str]}, ...]
            "closer": str
        }
        하위 호환: body 항목이 str이면 dict로 자동 변환.

    Returns:
        보정된 동일 구조 dict
    """
    original_body_count = len(llm_output.get("body", []))

    # hook / closer: 초과 시 첫 청크만 사용
    for key, limit in (("hook", MAX_HOOK_CHARS), ("closer", MAX_BODY_CHARS)):
        val = llm_output.get(key, "")
        if len(val) > limit:
            fixed = smart_split_korean(val, limit)[0]
            logger.debug("validate_and_fix: %s 축약 %d→%d자", key, len(val), len(fixed))
            llm_output[key] = fixed

    # body: 각 항목의 lines 요소가 MAX_BODY_CHARS 초과 시 분할
    fixed_body: list[dict] = []
    for item in llm_output.get("body", []):
        lines: list[str] = item.get("lines", []) if isinstance(item, dict) else [item]
        fixed_lines: list[str] = []
        for line in lines:
            if len(line) > MAX_BODY_CHARS:
                parts = smart_split_korean(line, MAX_BODY_CHARS)
                logger.debug("validate_and_fix: body line 분할 %d자 → %d개", len(line), len(parts))
                fixed_lines.extend(parts)
            else:
                fixed_lines.append(line)
        fixed_item: dict = {"line_count": len(fixed_lines), "lines": fixed_lines}
        # 부가 필드 보존 (예: "type": "comment")
        if isinstance(item, dict):
            for k, v in item.items():
                if k not in ("line_count", "lines"):
                    fixed_item[k] = v
        fixed_body.append(fixed_item)
    llm_output["body"] = fixed_body

    if len(fixed_body) != original_body_count:
        logger.info(
            "validate_and_fix: body %d → %d항목 (분할 보정)",
            original_body_count, len(fixed_body),
        )

    return llm_output
