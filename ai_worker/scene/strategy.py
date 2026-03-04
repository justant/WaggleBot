"""ai_worker/scene/strategy.py — 씬 구성 전략 데이터클래스"""

from dataclasses import dataclass, field


@dataclass
class SceneMix:
    """LLM 또는 규칙 기반으로 결정된 씬 구성 전략.

    향후 Phase 2에서 LLM이 대본 생성 시 씬 구성 힌트도 반환하면,
    이 데이터클래스에 담겨 Phase 4 SceneDirector로 전달된다.
    """
    image_text_indices: list[int] = field(default_factory=list)
    video_indices: list[int] = field(default_factory=list)
    text_only_indices: list[int] = field(default_factory=list)
    strategy: str = "rule_based"  # "llm_decided" | "rule_based" | "image_heavy" | ...

    @property
    def total_scenes(self) -> int:
        return len(self.image_text_indices) + len(self.video_indices) + len(self.text_only_indices)
