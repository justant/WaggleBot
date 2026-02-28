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


_VIDEO_PROMPT_SYSTEM = """You are a cinematic video prompt engineer specializing in short-form content.
Given a Korean text snippet, its surrounding story context, and a mood, write an English paragraph (4-8 sentences) that describes a 3-5 second video clip.

Follow this 5-step formula strictly:
1. SHOT: Shot size and lighting (e.g., "Extreme close-up, dramatic chiaroscuro lighting")
2. SCENE: Location and atmosphere (e.g., "A cramped studio apartment at 3 AM, clothes scattered on the floor")
3. ACTION: Subject's specific motion (e.g., "A young woman slowly types on a keyboard, then slams the laptop shut")
4. CAMERA: Lens movement (e.g., "Slow dolly out with slight handheld shake, transitioning to a static wide shot")
5. AUDIO-VISUAL: Synesthetic mood cue (e.g., "Visuals evoke the heavy sound of silence broken only by distant traffic")

CRITICAL RULES:
- Output ONLY the English paragraph. No numbering, no step labels, no markdown.
- The paragraph must flow naturally as one cohesive description.
- Be SPECIFIC: avoid generic descriptions. Reference concrete objects, colors, textures.
- Match the visual style hint provided.
- The video is only 3-5 seconds, so describe a single continuous moment, not a sequence of events.

Style hint: {style_hint}
Story context: {story_context}"""


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

        Args:
            text_lines: 해당 씬의 텍스트 라인 목록
            mood: 감정 분류 (9종 중 하나)
            title: 게시글 제목 (컨텍스트)
            body_summary: 본문 요약 (컨텍스트, 500자 이내)
            has_init_image: I2V 모드 여부

        Returns:
            영어 비디오 프롬프트 문자열
        """
        styles = _load_video_styles()
        style_data = styles.get(mood, styles.get("daily", {}))
        style_hint = style_data.get("style_hint", "natural daylight, casual aesthetic")

        context = " ".join(text_lines)
        story_context = f"Title: {title}. Scene text: {context}"
        if body_summary:
            story_context += f". Overall story: {body_summary[:300]}"

        system_prompt = _VIDEO_PROMPT_SYSTEM.format(
            style_hint=style_hint,
            story_context=story_context,
        )

        prompt = call_ollama_raw(
            prompt=system_prompt + f"\n\nKorean text for this scene: {context}",
            max_tokens=256,
            temperature=0.7,
        )

        if has_init_image:
            prompt += " Maintain the original image composition and add subtle natural motion."

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
