"""Phase 4: 씬 배분 알고리즘 (Scene Director)

ResourceProfile 전략과 이미지 잔여량을 추적하며
img_text / text_only 씬을 순서대로 배분한다.

씬 흐름:
    intro(hook) → [img_text | text_only] × N → outro(closer + 남은이미지)

- 이미지 있으면 → img_text 우선
- 이미지 소진 후 → text_only (전략에 따라 1~3줄 스태킹)
- 마지막 이미지 남으면 → outro 1장
"""
import logging
from dataclasses import dataclass, field
from typing import Literal

from ai_worker.resource_analyzer import ResourceProfile

logger = logging.getLogger(__name__)

SceneType = Literal["intro", "img_text", "text_only", "outro"]

# 반전/충격 키워드가 등장하면 단독 강조 처리
_HIGHLIGHT_KEYWORDS = ["반전", "충격", "결과", "결론", "사실", "진짜", "알고보니"]

# 전략별 text_only 기본 스택 크기
_STACK_BY_STRATEGY: dict[str, int] = {
    "img_heavy": 1,
    "balanced":  2,
    "text_heavy": 3,
}


@dataclass
class SceneDecision:
    type: SceneType
    text_lines: list[str]
    image_url: str | None           # img_text / outro 에서 사용
    text_only_stack: int = 1        # text_only 씬의 실제 스택 줄 수


class SceneDirector:
    """ResourceProfile과 대본 script를 받아 씬 목록을 결정한다."""

    def __init__(
        self,
        profile: ResourceProfile,
        images: list[str],
        script: dict,
    ) -> None:
        self.profile = profile
        self._images: list[str] = list(images)   # 소모 추적용 복사본
        self.script = script

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def direct(self) -> list[SceneDecision]:
        """씬 배분 목록을 생성해 반환한다."""
        scenes: list[SceneDecision] = []

        # ── Intro ──────────────────────────────────────────────────────
        scenes.append(SceneDecision(
            type="intro",
            text_lines=[self.script.get("hook", "")],
            image_url=None,
        ))

        # ── Body ───────────────────────────────────────────────────────
        body = list(self.script.get("body", []))
        while body:
            if self._images:
                scenes.append(self._make_img_text(body))
            else:
                scenes.append(self._make_text_only(body))

        # ── Outro ──────────────────────────────────────────────────────
        closer = self.script.get("closer", "")
        if self._images:
            scenes.append(SceneDecision(
                type="outro",
                text_lines=[closer] if closer else [],
                image_url=self._images.pop(0),
            ))
        elif closer:
            # 이미지 없을 때 closer를 text_only로 처리
            scenes.append(SceneDecision(
                type="text_only",
                text_lines=[closer],
                image_url=None,
                text_only_stack=1,
            ))

        logger.debug(
            "씬 배분: 총 %d개 (%s)",
            len(scenes),
            ", ".join(s.type for s in scenes),
        )
        return scenes

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_img_text(self, body: list[str]) -> SceneDecision:
        return SceneDecision(
            type="img_text",
            text_lines=[body.pop(0)],
            image_url=self._images.pop(0),
        )

    def _make_text_only(self, body: list[str]) -> SceneDecision:
        n = self._decide_stack(body)
        count = min(n, len(body))
        lines = [body.pop(0) for _ in range(count)]
        return SceneDecision(
            type="text_only",
            text_lines=lines,
            image_url=None,
            text_only_stack=len(lines),
        )

    def _decide_stack(self, remaining: list[str]) -> int:
        """전략과 잔여 문장 수를 고려해 text_only 스택 크기를 결정한다."""
        base = _STACK_BY_STRATEGY.get(self.profile.strategy, 2)

        # 마지막 2문장 이하이면 최대 2줄로 제한
        if len(remaining) <= 2:
            return min(len(remaining), 2)

        # 반전/충격 키워드가 포함된 문장은 단독 강조
        if any(kw in remaining[0] for kw in _HIGHLIGHT_KEYWORDS):
            return 1

        return base
