import logging

import requests

from config.settings import OLLAMA_HOST, OLLAMA_MODEL

logger = logging.getLogger(__name__)

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


def summarize(
    title: str,
    body: str,
    comments: list[str],
    *,
    model: str | None = None,
) -> str:
    model = model or OLLAMA_MODEL
    prompt = _SUMMARY_PROMPT_TEMPLATE.format(
        title=title,
        body=body[:2000],
        comments="\n".join(f"- {c}" for c in comments[:5]),
    )

    url = f"{OLLAMA_HOST}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 300, "temperature": 0.7},
    }

    logger.info("Ollama 요약 요청: model=%s", model)
    resp = requests.post(url, json=payload, timeout=180)
    resp.raise_for_status()

    result = resp.json().get("response", "").strip()
    logger.info("Ollama 요약 완료: %d자", len(result))
    return result
