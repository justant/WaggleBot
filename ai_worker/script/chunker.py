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
    body 항목의 lines 요소는 각 20자 이내.
    20자 초과 시 어절 단위로 분리해 line_count 2로 설정.
"""
import json
import logging

import requests

from ai_worker.scene.analyzer import ResourceProfile
from config.settings import OLLAMA_MODEL, MAX_BODY_CHARS, get_llm_constraints_prompt, get_ollama_host

logger = logging.getLogger(__name__)

_STRATEGY_GUIDE: dict[str, str] = {
    "image_heavy":  "각 문장 짧고 임팩트 있게. 이미지마다 한 문장.",
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
        f'  "title_suggestion": "YouTube 쇼츠 제목 (50자 이내)",\n'
        f'  "tags": ["태그1", "태그2", "태그3"],\n'
        f'  "mood": "humor | touching | anger | sadness | horror | info | controversy | daily | shock 중 하나"\n'
        if extended else ""
    )

    return (
        "당신은 사람들의 사연을 찰지고 생동감 있게 읽어주는 '썰 전문 유튜브 쇼츠 크리에이터'입니다.\n"
        "아래 입력을 읽고, 마치 카메라 앞에서 시청자에게 열변을 토하듯 자연스럽고 감정선이 살아있는 대본을 작성하세요.\n"
        "반드시 JSON 형식으로만 응답해야 하며, 다른 텍스트는 절대 포함하지 마세요.\n\n"
        "## 입력\n"
        f"{post_content[:2000]}\n\n"
        "## 자원 상황\n"
        f"- 이미지: {profile.image_count}장 / 예상 문장: {profile.estimated_sentences}개\n"
        f"- 전략: {profile.strategy} — {guide}\n\n"
        f"{constraints}\n\n"
        "## 출력 형식 (JSON)\n"
        "{\n"
        f'  "hook": "첫 3초 후킹 문장 (최대 {MAX_BODY_CHARS}자)",\n'
        '  "body": [\n'
        '    {"line_count": 2, "lines": ["20자 이하 줄 1", "20자 이하 줄 2"]},\n'
        '    {"line_count": 1, "lines": ["20자 이하 단일 줄"]},\n'
        '    {"line_count": 1, "lines": ["닉네임: 댓글 내용"], "type": "comment"}\n'
        '  ],\n'
        f'  "closer": "마무리 멘트 (최대 {MAX_BODY_CHARS}자)",\n'
        f"{extended_fields}"
        "}\n\n"
        "## 규칙\n"
        '1. 블록 타입 분리 (필수): 댓글을 직접 읽어주는 항목에는 "type": "comment" 추가. 일반 본문 항목은 type 필드 생략.\n'
        "2. 댓글 분리 규정: 댓글은 본문과 별도의 body 항목으로 분리.\n"
        "3. 자막 분할 및 가독성 (호흡 단위):\n"
        '   - 대본은 기계적으로 자르지 말고, 사람이 말할 때 숨을 쉬는 "호흡 단위"로 끊어주세요.\n'
        "   - 1개 문자열은 20자를 넘지 않되, 의미가 어색하게 끊기지 않게 하세요.\n"
        '     (예: "정유사들은" / "다 똑같아" (O), "정유사들" / "은 다 똑같아" (X))\n'
        "   - 댓글도 동일한 길이 규칙 적용, 최대 3줄(총 60자 이내). 긴 경우 핵심만 55자 이내로 요약.\n"
        "4. 본문 끝까지 작성 (생략/요약 절대 금지): 원문 내용을 빠짐없이 대본으로 변환할 것.\n"
        "5. 베스트 댓글 필수 인용: 입력에 베스트 댓글이 있으면 반드시 body에 포함.\n"
        "6. 어조 및 톤앤매너 (가장 중요):\n"
        '   - 딱딱한 요약이나 뉴스 기사처럼 쓰지 마세요. 원문 작성자의 분노, 어이없음 등의 감정을 200% 살리세요.\n'
        '   - "진짜", "와", "아니 글쎄", "이거 미친 거 아닙니까?" 같은 자연스러운 추임새를 적절히 섞어 실제 사람이 말하는 것처럼 만드세요.\n'
        '   - (X) 나쁜 예: "주유소들이 가격을 담합합니다. 사람들은 모르지 않을 겁니다."\n'
        '   - (O) 좋은 예: "이 인간들 지들끼리 가격 담합하는 거, 우리가 모를 줄 알았나 봐요?"\n'
        "7. 훅(Hook) 작성법:\n"
        '   - 단순 명사형 요약("주유소 가격 담합 의혹")을 절대 쓰지 마세요.\n'
        '   - 시청자가 스크롤을 멈출 수 있게 원문의 매운맛을 살린 도발적인 문장을 쓰세요.\n'
        '   - (예: "기름값 담합하는 양아치 주유소들, 진짜 다 똑같습니다!")\n'
        "8. 감정 분류: mood 필드는 humor | touching | anger | sadness | horror | info | controversy | daily | shock 중 하나.\n"
        "9. 고유명사 및 팩트 보존 (환각 및 오역 금지):\n"
        '   - 한국어 문맥을 정확히 파악하세요. (예: "정유사는 다 틀린데"는 "Wrong"이 아니라 "다르다(Different)"는 뜻.)\n'
        "   - 고유명사는 그대로 유지.\n"
        "10. 검열 해제 (Uncensored):\n"
        '    - 욕설이나 거친 표현도 방송용으로 살짝 순화하거나(예: "이 시벨럼들" -> "이 양아치들") 원문 느낌을 그대로 살려 서사를 완성하세요.\n'
        "11. body 항목 수: 최소 6줄(본문 10자 이상), 최대 30줄(본문 1500자 이상일 경우), 본문 길이에 비례할 것.\n"
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
    from ai_worker.script.logger import LLMCallTimer, log_llm_call

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
