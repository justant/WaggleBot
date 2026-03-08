"""ai_worker/script/normalizer.py — 대본 댓글 후처리·정규화"""

import json
import logging
from pathlib import Path

from db.models import ScriptData

logger = logging.getLogger(__name__)

# ── layout.json에서 글자수 제한 로드 ──
_LAYOUT_PATH = Path("config/layout.json")

def _load_layout_constraints() -> dict:
    """layout.json의 constraints 섹션을 로드한다."""
    try:
        data = json.loads(_LAYOUT_PATH.read_text(encoding="utf-8"))
        return data.get("constraints", {})
    except Exception:
        logger.warning("layout.json 로드 실패 — 기본값 사용")
        return {}

_constraints = _load_layout_constraints()

# 본문 글자수 제한 (layout.json → constraints.body_line)
MAX_LINE_CHARS: int = _constraints.get("body_line", {}).get("max_chars", 21)

# 댓글 글자수 제한 (layout.json → constraints.comment_line)
COMMENT_LINE_CHARS: int = _constraints.get("comment_line", {}).get("max_chars", 20)
COMMENT_MAX_LINES: int = _constraints.get("comment_line", {}).get("max_lines", 3)
COMMENT_MAX_CHARS: int = COMMENT_LINE_CHARS * COMMENT_MAX_LINES  # 60


def split_comment_lines(text: str, max_chars: int = COMMENT_LINE_CHARS) -> list[str]:
    """댓글 텍스트를 자막 줄 단위(20자)로 어절 분할. 최대 3줄."""
    if len(text) <= max_chars:
        return [text]
    words = text.split()
    lines: list[str] = []
    current = ""
    for w_idx, word in enumerate(words):
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
            # 최대 줄 수 도달 시 나머지를 마지막 줄에 합치기
            if len(lines) >= COMMENT_MAX_LINES - 1:
                remaining_words = [current] + words[w_idx + 1:]
                lines.append(" ".join(remaining_words)[:max_chars])
                return lines[:COMMENT_MAX_LINES]
    if current:
        lines.append(current)
    return lines[:COMMENT_MAX_LINES] if lines else [text[:max_chars]]


def summarize_long_comment(
    text: str,
    target_chars: int = 55,
    post_id: int | None = None,
    author: str = "",
) -> str:
    """60자를 초과하는 댓글을 LLM으로 요약한다.

    Args:
        text: 원본 댓글 텍스트 (60자 초과)
        target_chars: 요약 목표 글자수 (기본 55자, 60자 미만으로 여유)
        post_id: LLM 로그 연결용 게시글 ID
        author: 댓글 작성자 (로그용)

    Returns:
        요약된 댓글 텍스트 (target_chars 이하)
    """
    from ai_worker.script.client import call_ollama_raw
    from ai_worker.script.logger import LLMCallTimer, log_llm_call

    prompt = (
        f"다음 댓글을 {target_chars}자 이내로 요약하세요.\n"
        "핵심 의미와 말투를 최대한 유지하세요.\n"
        "요약된 텍스트만 출력하세요.\n\n"
        f"원문: {text}\n\n"
        "요약:"
    )

    try:
        with LLMCallTimer() as timer:
            result = call_ollama_raw(prompt, max_tokens=100, temperature=0.3)
        result = result.strip().strip('"').strip()
        # 안전장치: 요약이 오히려 길어진 경우 강제 절삭
        if len(result) > COMMENT_MAX_CHARS:
            result = result[:COMMENT_MAX_CHARS - 1] + "…"

        log_llm_call(
            call_type="comment_summarize",
            post_id=post_id,
            model_name=None,
            prompt_text=prompt,
            raw_response=result,
            parsed_result={
                "original_text": text,
                "original_length": len(text),
                "summarized_text": result,
                "summarized_length": len(result),
                "author": author,
            },
            content_length=len(text),
            success=True,
            duration_ms=timer.elapsed_ms,
        )
        return result
    except Exception as e:
        logger.warning("댓글 요약 LLM 실패, 강제 절삭: %s", e)
        return text[:COMMENT_MAX_CHARS - 1] + "…"


def ensure_comments(
    script: ScriptData,
    input_comments: list[str],
    min_comments: int = 3,
    post_id: int | None = None,
) -> ScriptData:
    """LLM이 댓글을 누락했을 때 입력 댓글을 body 끝에 자동 추가.

    변경: 60자 초과 댓글은 LLM 요약 후 삽입.
    """
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

        # 60자 초과 댓글 요약
        if len(content) > COMMENT_MAX_CHARS:
            logger.info(
                "댓글 요약 실행: 원문 %d자 → 목표 55자 (author=%s)",
                len(content), author,
            )
            content = summarize_long_comment(
                content, target_chars=55, post_id=post_id, author=author,
            )

        lines = split_comment_lines(content, max_chars=COMMENT_LINE_CHARS)
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
