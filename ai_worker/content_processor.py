"""Phase 5: 전체 통합 진입점 (Content Processor)

Phase 1 ~ 4를 순서대로 실행해 SceneDecision 목록을 반환한다.

    [Phase 1] analyze_resources  - ResourceProfile
    [Phase 2] chunk_with_llm     - raw script dict
    [Phase 3] validate_and_fix   - validated script dict
    [Phase 4] SceneDirector      - list[SceneDecision]
"""
import collections
import logging
from pathlib import Path

from ai_worker.llm_chunker import chunk_with_llm
from ai_worker.resource_analyzer import ResourceProfile, analyze_resources
from ai_worker.scene_director import SceneDecision, SceneDirector
from ai_worker.text_validator import validate_and_fix

logger = logging.getLogger(__name__)


async def process_content(post, images: list[str]) -> list[SceneDecision]:
    """콘텐츠 처리 전체 파이프라인.

    Args:
        post:   Post 객체 (post.content, post.title 사용)
        images: 이미지 URL/경로 목록

    Returns:
        렌더러에 전달할 SceneDecision 목록

    Raises:
        requests.RequestException: Ollama 통신 오류
        ValueError: 대본 필수 키 누락 등 검증 실패
    """
    # ── Phase 1: 자원 분석 ────────────────────────────────────────
    profile: ResourceProfile = analyze_resources(post, images)
    logger.info(
        "[content_processor] Phase 1 완료: 전략=%s 이미지=%d 예상문장≈%d",
        profile.strategy, profile.image_count, profile.estimated_sentences,
    )

    # ── Phase 2: LLM 청킹 (의미 단위) ────────────────────────────
    llm_output: dict = await chunk_with_llm(post.content or "", profile)

    # ── Phase 3: 물리적 검증 (max_chars 보정) ─────────────────────
    script: dict = validate_and_fix(llm_output)
    logger.info(
        "[content_processor] Phase 3 완료: hook(%d자) + body(%d줄) + closer(%d자)",
        len(script.get("hook", "")),
        len(script.get("body", [])),
        len(script.get("closer", "")),
    )

    # ── Phase 4: 씬 배분 ──────────────────────────────────────────
    director = SceneDirector(profile, images, script)
    scenes: list[SceneDecision] = director.direct()

    counter = collections.Counter(s.type for s in scenes)
    logger.info(
        "[content_processor] Phase 4 완료: %d씬 구성 %s",
        len(scenes), dict(counter),
    )

    return scenes
