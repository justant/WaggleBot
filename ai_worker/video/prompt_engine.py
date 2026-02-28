"""LTX-Video용 영어 비디오 프롬프트 생성 엔진.

call_ollama_raw()를 사용해 한국어 텍스트를 영화적 영어 프롬프트로 변환한다.
Mood별 시각 스타일은 config/video_styles.json에서 로드한다.

의존성 규칙:
- ai_worker.tts 모듈을 절대 import하지 않는다.
- ai_worker.llm.client의 call_ollama_raw()만 사용한다.
"""

import json
import logging
from pathlib import Path

from ai_worker.llm.client import call_ollama_raw

logger = logging.getLogger(__name__)

_VIDEO_STYLES: dict | None = None


def _load_video_styles() -> dict:
    """config/video_styles.json을 로드한다."""
    global _VIDEO_STYLES
    if _VIDEO_STYLES is None:
        path = Path(__file__).resolve().parent.parent.parent / "config" / "video_styles.json"
        with open(path, encoding="utf-8") as f:
            _VIDEO_STYLES = json.load(f)
    return _VIDEO_STYLES


_T2V_PROMPT_SYSTEM = """You are a video prompt writer for AI-generated 3-second clips.
Write 2-3 English sentences describing an abstract symbolic video clip.

RULES:
- NO people, faces, or human bodies. AI video cannot generate realistic humans.
- Use abstract or symbolic visuals: close-up of objects, nature, textures, light, patterns, typography.
- Match the EMOTION and MOOD of the Korean text, not its literal meaning.
- One continuous moment. No scene changes. No camera movement.
- Be specific about colors, lighting, and motion direction.

Visual style: {style_hint}

Korean text: {korean_text}
Mood: {mood}"""

_I2V_PROMPT_SYSTEM = """You are a subtle motion descriptor for image-to-video AI.
The input image already exists. Write 1-2 English sentences describing ONLY the small natural motions to add.

RULES:
- Do NOT describe a new scene or change the composition.
- Describe ONLY micro-motions: gentle swaying, light flicker, subtle parallax, soft focus shift, particle drift.
- Keep camera STATIC. No zoom, no pan, no dolly.
- Keep it very short and simple.

Korean text context: {korean_text}
Mood: {mood}"""


NEGATIVE_PROMPT = (
    "blurry, low quality, distorted text, watermark, "
    "deformed hands, extra fingers, unrealistic proportions, "
    "static image, no motion, frozen frame, "
    "ugly, duplicate, morbid, mutilated, poorly drawn face, "
    "out of frame, extra limbs, bad anatomy"
)


class VideoPromptEngine:
    """비디오 프롬프트 생성기."""

    def generate_prompt(
        self,
        text_lines: list[str],
        mood: str,
        title: str = "",
        body_summary: str = "",
        has_init_image: bool = False,
    ) -> str:
        """단일 씬의 비디오 프롬프트를 생성한다.

        T2V: 추상적/상징적 비주얼 (사람 없음)
        I2V: 기존 이미지에 미세한 자연스러운 동작 추가

        Args:
            text_lines: 해당 씬의 텍스트 라인 목록
            mood: 감정 분류 (9종 중 하나)
            title: 게시글 제목 (컨텍스트)
            body_summary: 본문 요약 (컨텍스트, 500자 이내)
            has_init_image: I2V 모드 여부

        Returns:
            영어 비디오 프롬프트 문자열
        """
        korean_text = " ".join(text_lines)
        if title:
            korean_text = f"[{title}] {korean_text}"

        if has_init_image:
            system_prompt = _I2V_PROMPT_SYSTEM.format(
                korean_text=korean_text,
                mood=mood,
            )
            prompt = call_ollama_raw(
                prompt=system_prompt,
                max_tokens=80,
                temperature=0.3,
            )
        else:
            styles = _load_video_styles()
            style_data = styles.get(mood, styles.get("daily", {}))
            style_hint = style_data.get("style_hint", "natural daylight, casual aesthetic")

            system_prompt = _T2V_PROMPT_SYSTEM.format(
                style_hint=style_hint,
                korean_text=korean_text,
                mood=mood,
            )
            prompt = call_ollama_raw(
                prompt=system_prompt,
                max_tokens=120,
                temperature=0.5,
            )

        return prompt.strip()

    def simplify_prompt(self, original_prompt: str) -> str:
        """실패 시 재시도용 단순화 프롬프트를 생성한다.

        원본 프롬프트에서 복잡한 카메라 워크와 오디오-비주얼 묘사를 제거하고
        Shot + Scene + Action만 남긴 간결한 버전을 반환한다.
        """
        simplified = call_ollama_raw(
            prompt=(
                "Simplify the following video prompt to just 2-3 sentences. "
                "Keep only the shot type, scene description, and main action. "
                "Remove camera movements and audio descriptions.\n\n"
                f"Original: {original_prompt}"
            ),
            max_tokens=100,
            temperature=0.3,
        )
        return simplified.strip()

    def generate_batch(
        self,
        scenes: list,
        mood: str,
        title: str = "",
        body_summary: str = "",
    ) -> list:
        """전체 씬 리스트에 대해 video_prompt를 배치 생성한다.

        video_mode가 "t2v" 또는 "i2v"인 씬에만 프롬프트를 생성한다.
        프롬프트 생성 실패 시 해당 씬의 video_prompt를 None으로 유지하고 경고 로그.
        """
        for i, scene in enumerate(scenes):
            if getattr(scene, "video_mode", None) not in ("t2v", "i2v"):
                continue
            try:
                text_lines: list[str] = []
                for line in scene.text_lines:
                    if isinstance(line, dict):
                        text_lines.append(line.get("text", ""))
                    else:
                        text_lines.append(str(line))

                scene.video_prompt = self.generate_prompt(
                    text_lines=text_lines,
                    mood=mood,
                    title=title,
                    body_summary=body_summary,
                    has_init_image=(scene.video_mode == "i2v"),
                )
                logger.info("[prompt] 씬 %d 프롬프트 생성 완료 (%d자)", i, len(scene.video_prompt))
            except Exception as e:
                logger.error("[prompt] 씬 %d 프롬프트 생성 실패: %s", i, e)
                scene.video_prompt = None
        return scenes
