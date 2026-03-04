"""ai_worker/script/normalizer.py — 대본 댓글 후처리·정규화"""

import logging

from db.models import ScriptData

logger = logging.getLogger(__name__)

MAX_LINE_CHARS: int = 20


def split_comment_lines(text: str, max_chars: int = MAX_LINE_CHARS) -> list[str]:
    """댓글 텍스트를 자막 줄 단위(20자)로 어절 분할."""
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines if lines else [text[:max_chars]]


def ensure_comments(
    script: ScriptData,
    input_comments: list[str],
    min_comments: int = 3,
) -> ScriptData:
    """LLM이 댓글을 누락했을 때 입력 댓글을 body 끝에 자동 추가."""
    if not input_comments:
        return script

    existing_count = sum(
        1 for item in script.body if item.get("type") == "comment"
    )
    target = min(min_comments, len(input_comments))
    if existing_count >= target:
        return script

    # 이미 포함된 댓글 내용 (중복 방지)
    existing_texts: set[str] = set()
    for item in script.body:
        if item.get("type") == "comment":
            existing_texts.add(" ".join(item.get("lines", [])))

    needed = target - existing_count
    for comment_str in input_comments:
        if needed <= 0:
            break
        # "author: content" 형식 파싱
        parts = comment_str.split(":", 1)
        if len(parts) == 2:
            author = parts[0].strip()
            content = parts[1].strip()
        else:
            author = ""
            content = comment_str.strip()

        if not content or content in existing_texts:
            continue

        lines = split_comment_lines(content)
        script.body.append({
            "type": "comment",
            "author": author,
            "line_count": len(lines),
            "lines": lines,
        })
        existing_texts.add(content)
        needed -= 1
        logger.debug("댓글 자동 추가: author=%s lines=%d", author, len(lines))

    if existing_count == 0 and target > 0:
        logger.warning(
            "LLM이 댓글을 전혀 생성하지 않음 — %d개 자동 주입",
            target - needed,
        )

    return script
