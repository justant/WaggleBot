"""Phase 2: LLM 청킹 (의미 단위 분절)

ResourceProfile을 기반으로 전략별 프롬프트를 생성하고
Ollama JSON 모드로 구어체 대본을 분절 생성한다.

출력 형식:
    {
        "hook": "...",
        "body": [
            {"line_count": 2, "lines": ["문장1 앞", "문장1 뒤"]},
            {"line_count": 1, "lines": ["문장2"]},
            ...
        ],
        "closer": "..."
    }
    body 항목의 lines 요소는 각 21자 이내.
    21자 초과 시 어절 단위로 분리해 line_count 2로 설정.
"""
import json
import logging

import requests

from ai_worker.pipeline.resource_analyzer import ResourceProfile
from config.settings import OLLAMA_MODEL, MAX_BODY_CHARS, get_llm_constraints_prompt, get_ollama_host

logger = logging.getLogger(__name__)

_STRATEGY_GUIDE: dict[str, str] = {
    "img_heavy":  "각 문장 짧고 임팩트 있게. 이미지마다 한 문장.",
    "balanced":   "핵심 문장과 보조 문장을 구분해서 작성.",
    "text_heavy": "텍스트만으로 몰입되도록 자세히 작성.",
}


def create_chunking_prompt(
    post_content: str,
    profile: ResourceProfile,
    *,
    extended: bool = False,
) -> str:
    """ResourceProfile을 반영한 LLM 청킹 프롬프트를 생성한다.

    Args:
        extended: True이면 title_suggestion/tags/mood 필드도 출력 형식에 포함한다.
    """
    guide = _STRATEGY_GUIDE.get(profile.strategy, "")
    constraints = get_llm_constraints_prompt()

    extended_fields = (
        f'  "title_suggestion": "YouTube 쇼츠 제목 (50자 이내, 이모지 포함)",\n'
        f'  "tags": ["태그1", "태그2", "태그3"],\n'
        f'  "mood": "funny | serious | shocking | heartwarming 중 하나"\n'
        if extended else ""
    )

    return (
        "당신은 유튜브 쇼츠 대본 작가입니다.\n"
        "아래 커뮤니티 게시글을 구어체 쇼츠 대본(JSON)으로 변환하세요.\n\n"
        "## 원문\n"
        f"{post_content[:2000]}\n\n"
        "## 자원 상황\n"
        f"- 이미지: {profile.image_count}장 / 예상 문장: {profile.estimated_sentences}개\n"
        f"- 전략: {profile.strategy} — {guide}\n\n"
        f"{constraints}\n\n"
        "## 출력 형식 (JSON 만 출력)\n"
        "{\n"
        f'  "hook": "첫 3초 후킹 문장 (최대 {MAX_BODY_CHARS}자)",\n'
        '  "body": [\n'
        '    {"line_count": 2, "lines": ["21자 이하 줄 1", "21자 이하 줄 2"]},\n'
        '    {"line_count": 1, "lines": ["21자 이하 단일 줄"]},\n'
        '    {"line_count": 1, "lines": ["닉네임: 댓글 내용"], "type": "comment"}\n'
        '  ],\n'
        f'  "closer": "마무리 멘트 (최대 {MAX_BODY_CHARS}자)",\n'
        f"{extended_fields}"
        "}\n\n"
        "## 작성 규칙\n"
        "1. 한 숨에 읽을 수 있는 호흡 단위로 끊기\n"
        "2. 반말 구어체 (~했어, ~인데, ~ㅋㅋ)\n"
        "3. 문장 중간 절대 끊지 말 것\n"
        f"4. body 각 항목의 lines 요소는 21자 이내\n"
        "5. 21자 초과 시 자연스러운 어절 단위로 분리해 line_count 2로 설정\n"
        "6. body는 최소 6개, 최대 23개 항목 (원문 분량에 비례하여 조절)\n"
        '7. 댓글을 직접 읽어주는 항목에는 "type": "comment" 추가\n'
        "8. 일반 본문 항목은 type 필드 생략\n"
    )


def _call_ollama_json(prompt: str, model: str) -> dict:
    """Ollama API를 JSON 모드로 호출하고 파싱된 dict를 반환한다."""
    url = f"{get_ollama_host()}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"num_predict": 1500, "temperature": 0.7},
    }
    resp = requests.post(url, json=payload, timeout=180)
    resp.raise_for_status()
    raw = resp.json().get("response", "").strip()
    return json.loads(raw)


async def chunk_with_llm(
    post_content: str,
    profile: ResourceProfile,
    *,
    post_id: int | None = None,
    extended: bool = False,
) -> dict:
    """ResourceProfile 기반 LLM 청킹을 수행하고 raw 대본 dict를 반환한다.

    Args:
        post_content: 게시글 본문
        profile:      Phase 1에서 생성된 ResourceProfile
        post_id:      LLM 로그 연결용 게시글 ID (선택)
        extended:     True이면 title_suggestion/tags/mood 추가 필드도 반환

    Returns:
        {"hook": str, "body": list[dict], "closer": str}
        body 항목: {"line_count": int, "lines": list[str]}
        extended=True 시 위에 더해 {"title_suggestion": str, "tags": list[str], "mood": str}

    Raises:
        requests.RequestException: Ollama 통신 오류
        json.JSONDecodeError: 응답 파싱 실패
        ValueError: 필수 키 누락
    """
    import asyncio
    from ai_worker.llm.logger import LLMCallTimer, log_llm_call

    prompt = create_chunking_prompt(post_content, profile, extended=extended)
    logger.info("LLM 청킹 요청: 전략=%s, 본문=%d자, extended=%s", profile.strategy, len(post_content), extended)

    raw_str = ""
    result: dict = {}
    success = True
    error_msg: str | None = None

    with LLMCallTimer() as timer:
        try:
            result = await asyncio.to_thread(_call_ollama_json, prompt, OLLAMA_MODEL)
            raw_str = json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            success = False
            error_msg = str(exc)
            logger.error("LLM 청킹 실패: %s", exc)
            raise
        finally:
            log_llm_call(
                call_type="chunk",
                post_id=post_id,
                model_name=OLLAMA_MODEL,
                prompt_text=prompt,
                raw_response=raw_str,
                parsed_result=result if result else None,
                strategy=profile.strategy,
                image_count=profile.image_count,
                content_length=len(post_content),
                success=success,
                error_message=error_msg,
                duration_ms=timer.elapsed_ms,
            )

    # 필수 키 검증
    for key in ("hook", "body", "closer"):
        if key not in result:
            raise ValueError(f"LLM 응답에 필수 키 누락: '{key}' | 응답={result}")

    if not isinstance(result["body"], list):
        result["body"] = [str(result["body"])]

    # extended 모드: 선택 필드 기본값 보정
    if extended:
        result.setdefault("title_suggestion", "")
        result.setdefault("tags", [])
        result.setdefault("mood", "funny")
        if not isinstance(result["tags"], list):
            result["tags"] = []

    logger.info(
        "LLM 청킹 완료: hook=%d자, body=%d문장, closer=%d자 (%dms)",
        len(result["hook"]), len(result["body"]), len(result["closer"]), timer.elapsed_ms,
    )
    return result
