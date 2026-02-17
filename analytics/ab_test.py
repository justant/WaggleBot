"""A/B 테스트 프레임워크.

동일 채널에서 대본 스타일·자막을 실험해 데이터로 최적 전략을 검증한다.

워크플로우:
    1. create_test()       : 테스트 설정 생성 → ab_tests.json 저장
    2. assign_variant()    : APPROVED 처리 시 포스트에 랜덤 변형 배정
                             (processor.py가 호출)
    3. evaluate_group()    : 7일 후 A/B 성과 비교 → 승자 결정
    4. apply_winner()      : 승자 설정을 feedback_config 에 반영
"""

import json
import logging
import random
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import AB_TEST_CONFIG_PATH

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 사전 정의 변형 프리셋
# ---------------------------------------------------------------------------

VARIANT_PRESETS: dict[str, dict] = {
    "hook_question": {
        "label": "의문형 후킹",
        "extra_instructions": "hook은 반드시 의문형으로 작성하세요. 예: '왜 이런 일이 벌어졌을까?'",
    },
    "hook_exclamation": {
        "label": "감탄형 후킹",
        "extra_instructions": "hook은 반드시 감탄형으로 작성하세요. 예: '이건 진짜 충격이다!'",
    },
    "body_short": {
        "label": "짧은 body",
        "extra_instructions": "body 각 문장은 15자 이내로 매우 짧고 임팩트 있게 작성하세요.",
    },
    "body_narrative": {
        "label": "서사형 body",
        "extra_instructions": "body는 기승전결 스토리 구조로 서사적으로 흥미롭게 작성하세요.",
    },
    "tone_formal": {
        "label": "뉴스 말투",
        "extra_instructions": "전체 대본을 뉴스 앵커처럼 격식체(~습니다/~했습니다)로 작성하세요.",
    },
    "tone_casual": {
        "label": "구어 말투",
        "extra_instructions": "전체 대본을 친구에게 얘기하듯 반말 구어체(~했는데/~ㅋㅋ)로 작성하세요.",
    },
}


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------

@dataclass
class ABVariantConfig:
    """각 변형(A 또는 B)의 설정."""
    preset_key: str                     # VARIANT_PRESETS 키
    extra_instructions: str = ""        # 대본 생성 시 주입할 지시


@dataclass
class ABTest:
    """A/B 테스트 단위."""
    group_id: str                           # UUID 기반 그룹 식별자
    name: str                               # 사람이 읽기 좋은 이름
    status: str = "active"                  # active | completed | cancelled
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    config_a: dict = field(default_factory=dict)   # variant A 설정
    config_b: dict = field(default_factory=dict)   # variant B 설정
    winner: Optional[str] = None           # "A" | "B" | None
    winner_applied: bool = False
    stats: dict = field(default_factory=dict)  # {A: {posts, avg_views,...}, B: {...}}


# ---------------------------------------------------------------------------
# 영속 스토어 (JSON 파일)
# ---------------------------------------------------------------------------

def _load_tests() -> list[ABTest]:
    if not AB_TEST_CONFIG_PATH.exists():
        return []
    try:
        with open(AB_TEST_CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        return [ABTest(**t) for t in raw]
    except Exception:
        logger.warning("ab_tests.json 파싱 실패 — 빈 목록 반환", exc_info=True)
        return []


def _save_tests(tests: list[ABTest]) -> None:
    AB_TEST_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(AB_TEST_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump([asdict(t) for t in tests], f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_tests() -> list[ABTest]:
    """현재 테스트 목록 반환."""
    return _load_tests()


def get_active_test() -> Optional[ABTest]:
    """현재 활성화된(active) 테스트 1건 반환 (없으면 None)."""
    for t in _load_tests():
        if t.status == "active":
            return t
    return None


def create_test(
    name: str,
    preset_a: str,
    preset_b: str,
) -> ABTest:
    """새 A/B 테스트를 생성하고 저장한다.

    Args:
        name: 테스트 이름 (예: "hook 스타일 테스트 2026-02")
        preset_a: VARIANT_PRESETS 키 (variant A)
        preset_b: VARIANT_PRESETS 키 (variant B)

    Returns:
        생성된 ABTest 인스턴스
    """
    cfg_a = VARIANT_PRESETS.get(preset_a, {})
    cfg_b = VARIANT_PRESETS.get(preset_b, {})

    test = ABTest(
        group_id=f"ab_{uuid.uuid4().hex[:8]}",
        name=name,
        config_a={"preset_key": preset_a, **cfg_a},
        config_b={"preset_key": preset_b, **cfg_b},
    )

    tests = _load_tests()
    tests.append(test)
    _save_tests(tests)
    logger.info("A/B 테스트 생성: %s (group_id=%s)", name, test.group_id)
    return test


def cancel_test(group_id: str) -> bool:
    """테스트를 취소 상태로 변경한다."""
    tests = _load_tests()
    for t in tests:
        if t.group_id == group_id:
            t.status = "cancelled"
            _save_tests(tests)
            logger.info("A/B 테스트 취소: %s", group_id)
            return True
    return False


def assign_variant(post_id: int, session) -> Optional[dict]:
    """활성 A/B 테스트가 있으면 포스트에 랜덤으로 A 또는 B 변형을 배정한다.

    processor.py의 llm_tts_stage에서 호출.
    Content 레코드에 variant_group / variant_label / variant_config를 설정한다.

    Returns:
        {"group_id": str, "label": str, "variant_config": dict} 또는 None
    """
    test = get_active_test()
    if test is None:
        return None

    from db.models import Content

    content = session.query(Content).filter_by(post_id=post_id).first()
    # 이미 변형 배정된 경우 스킵
    if content and content.variant_group:
        return None

    label = "A" if random.random() < 0.5 else "B"
    variant_config = test.config_a if label == "A" else test.config_b

    if content is None:
        content = Content(post_id=post_id)
        session.add(content)

    content.variant_group = test.group_id
    content.variant_label = label
    content.variant_config = variant_config
    session.flush()

    logger.info(
        "A/B 변형 배정: post_id=%d → 그룹=%s 변형=%s preset=%s",
        post_id, test.group_id, label, variant_config.get("preset_key", "?"),
    )
    return {"group_id": test.group_id, "label": label, "variant_config": variant_config}


def evaluate_group(group_id: str, session) -> Optional[str]:
    """A/B 그룹의 성과를 비교해 승자를 결정한다.

    Args:
        group_id: 비교할 그룹 ID

    Returns:
        "A" | "B" | None (데이터 불충분)
    """
    from db.models import Post, Content, PostStatus

    def _avg(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    results: dict[str, list[int]] = {"A": [], "B": []}

    rows = (
        session.query(Post, Content)
        .join(Content, Content.post_id == Post.id)
        .filter(
            Content.variant_group == group_id,
            Post.status == PostStatus.UPLOADED,
        )
        .all()
    )

    for post, content in rows:
        label = content.variant_label
        if label not in results:
            continue
        meta = content.upload_meta or {}
        views = meta.get("youtube", {}).get("analytics", {}).get("views", 0)
        results[label].append(views)

    stats = {
        lbl: {"posts": len(v), "avg_views": _avg(v)}
        for lbl, v in results.items()
    }
    logger.info("A/B 평가 [%s]: %s", group_id, stats)

    min_posts = 3  # 통계적 유의성 최소 샘플
    if stats["A"]["posts"] < min_posts or stats["B"]["posts"] < min_posts:
        logger.warning(
            "A/B 평가 데이터 부족 (A=%d건, B=%d건, 최소 %d건 필요)",
            stats["A"]["posts"], stats["B"]["posts"], min_posts,
        )
        return None

    winner = "A" if stats["A"]["avg_views"] >= stats["B"]["avg_views"] else "B"

    # 결과를 테스트 레코드에 기록
    tests = _load_tests()
    for t in tests:
        if t.group_id == group_id:
            t.winner = winner
            t.status = "completed"
            t.completed_at = datetime.now(timezone.utc).isoformat()
            t.stats = stats
            break
    _save_tests(tests)

    logger.info(
        "A/B 승자 결정 [%s]: %s (A: %.0f vs B: %.0f 평균 조회수)",
        group_id, winner,
        stats["A"]["avg_views"], stats["B"]["avg_views"],
    )
    return winner


def apply_winner(group_id: str) -> bool:
    """승자 변형의 설정을 feedback_config 에 반영하고 winner_applied=True 로 표시한다.

    Returns:
        성공 여부
    """
    from analytics.feedback import apply_feedback

    tests = _load_tests()
    for t in tests:
        if t.group_id != group_id:
            continue
        if t.winner is None:
            logger.warning("승자 미결정: group_id=%s", group_id)
            return False

        winner_config = t.config_a if t.winner == "A" else t.config_b
        insights = {
            "extra_instructions": winner_config.get("extra_instructions", ""),
        }
        apply_feedback(insights)
        t.winner_applied = True
        _save_tests(tests)
        logger.info(
            "A/B 승자 반영 완료: group_id=%s 승자=%s 설정=%s",
            group_id, t.winner, str(insights)[:80],
        )
        return True

    logger.warning("group_id 없음: %s", group_id)
    return False
