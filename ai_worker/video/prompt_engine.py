"""LTX-2용 영어 비디오 프롬프트 생성 엔진.

LTX-2 Official Prompting Guide 기반:
- 단일 흐르는 단락 (single flowing paragraph)
- 현재 시제 (present tense)
- 3~6문장, 200단어 이내
- 6요소: Shot, Scene, Action, Character, Camera, Audio
- 한국 중심: 한국 인물, 한국 배경, 사운드 묘사 포함
"""

import json
import logging
from pathlib import Path

from ai_worker.llm.client import call_ollama_raw

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# System Prompts — LTX-2 Korea-centric V2
# ──────────────────────────────────────────────

_T2V_PROMPT_SYSTEM_V2 = """\
You are a video prompt writer for LTX-2 AI video generation.
Write a single flowing paragraph in English describing a 4-second realistic video clip.

SETTING: Modern South Korea. Korean people, Korean streets, Korean offices, Korean cafes.
STYLE: Photorealistic documentary/news footage. Natural lighting. Real-world settings.

RULES:
- Describe events chronologically as they happen over 4 seconds
- Include specific movements, gestures, and facial expressions
- Characters should be attractive Korean men/women in everyday situations
- Specify camera angle (eye-level, low angle, close-up, medium shot)
- Keep camera mostly STATIC or with minimal natural movement
- Describe lighting and colors precisely
- Include SOUND descriptions for audio generation (ambient noise, speech tone, background sounds)
- NEVER include: cyberpunk, sci-fi, fantasy, anime, abstract art, neon, western settings
- Keep under 200 words in a single paragraph

PERSON DESCRIPTION TIERS:
- Prefer medium shots (waist-up or full body) over extreme face close-ups
- If the scene involves emotional dialogue, use side profiles or over-shoulder angles
- If person generation fails, fall back to object/environment focus (empty room, laptop screen, coffee cup)
- Last resort: replace with cute golden retriever puppy or kitten in animated style

Mood: {mood}
Visual style: {style_hint}

Korean source text: {korean_text}"""

_I2V_SYSTEM = """\
You write motion prompts for LTX-2 Image-to-Video mode.
An input image already shows the scene, characters, and composition.
Describe ONLY the motion, camera movement, and sound to animate the image.

RULES:
- ONE paragraph, 2-4 sentences, present tense.
- Do NOT re-describe the scene or characters (they are in the image).
- Focus on micro-motions: breathing, hair blowing, blinking, fabric rustling.
- Specify camera: static frame, subtle dolly in, slow pan right, gentle tilt.
- Include SOUND descriptions: ambient sounds, speech tone, background noise.
- Strength guide: 0.7-0.8 recommended. Higher = more motion but may distort.

EXAMPLE:
"The subject's hair sways gently in a soft breeze as she slowly turns her head toward the camera, her lips parting into a faint smile. The camera holds a static medium shot, then begins a subtle dolly in. Warm ambient café noise and a quiet acoustic melody play softly."

---
Mood: {mood}
Korean context: {korean_text}

Write the motion prompt:"""

# ──────────────────────────────────────────────
# Negative Prompt V2
# ──────────────────────────────────────────────

NEGATIVE_PROMPT = (
    "worst quality, inconsistent motion, blurry, jittery, distorted, "
    "watermarks, anime, cartoon, 3d render, CGI, "
    "cyberpunk, sci-fi, futuristic, neon lights, abstract, "
    "deformed hands, extra fingers, bad anatomy, "
    "ugly, duplicate, morbid, mutilated, "
    "western faces, caucasian, european setting"
)

# ──────────────────────────────────────────────
# Video Styles (mood → style hint)
# ──────────────────────────────────────────────

_VIDEO_STYLES: dict | None = None


def _load_video_styles() -> dict:
    """config/video_styles.json 로드 (캐시)."""
    global _VIDEO_STYLES
    if _VIDEO_STYLES is None:
        path = Path(__file__).resolve().parent.parent.parent / "config" / "video_styles.json"
        try:
            with open(path, encoding="utf-8") as f:
                _VIDEO_STYLES = json.load(f)
        except Exception:
            _VIDEO_STYLES = {}
    return _VIDEO_STYLES


def _get_style_hint(mood: str) -> str:
    """mood에서 간결한 스타일 힌트 추출 (atmosphere 필드 사용)."""
    styles = _load_video_styles()
    style_data = styles.get(mood, styles.get("daily", {}))
    return style_data.get("atmosphere", "cinematic, natural lighting")


# ──────────────────────────────────────────────
# Prompt Engine
# ──────────────────────────────────────────────

class VideoPromptEngine:
    """LTX-2 프롬프트 생성기 (공식 가이드 + 한국 중심)."""

    def generate_prompt(
        self,
        text_lines: list[str],
        mood: str,
        title: str = "",
        body_summary: str = "",
        has_init_image: bool = False,
    ) -> str:
        """한국어 텍스트 → LTX-2용 영어 프롬프트 변환."""
        korean_text = " ".join(text_lines)
        if title:
            korean_text = f"[제목: {title}] {korean_text}"

        if has_init_image:
            prompt_input = _I2V_SYSTEM.format(
                korean_text=korean_text,
                mood=mood,
            )
            raw = call_ollama_raw(prompt=prompt_input, max_tokens=120, temperature=0.3)
        else:
            style_hint = _get_style_hint(mood)
            prompt_input = _T2V_PROMPT_SYSTEM_V2.format(
                style_hint=style_hint,
                korean_text=korean_text,
                mood=mood,
            )
            raw = call_ollama_raw(prompt=prompt_input, max_tokens=180, temperature=0.4)

        return _clean_prompt(raw)

    def simplify_prompt(self, original_prompt: str) -> str:
        """재시도용 프롬프트 단순화 (2-3문장).

        카메라 + 주요 동작만 남기고 오디오, 세부 조명, 보조 캐릭터 제거.
        """
        system = (
            "Simplify this video prompt to 2-3 short sentences. "
            "Keep ONLY: the main subject, one key action, and camera angle. "
            "Remove audio, detailed lighting, and secondary characters. "
            "Present tense, one paragraph.\n\n"
            f"Original: {original_prompt}\n\nSimplified:"
        )
        raw = call_ollama_raw(prompt=system, max_tokens=80, temperature=0.2)
        return _clean_prompt(raw)

    def generate_batch(
        self,
        scenes: list,
        mood: str,
        title: str = "",
        body_summary: str = "",
    ) -> list:
        """여러 씬의 비디오 프롬프트를 일괄 생성."""
        for i, scene in enumerate(scenes):
            if getattr(scene, "video_mode", None) not in ("t2v", "i2v"):
                continue
            try:
                text_lines = [
                    line.get("text", "") if isinstance(line, dict) else str(line)
                    for line in scene.text_lines
                ]
                scene.video_prompt = self.generate_prompt(
                    text_lines=text_lines,
                    mood=mood,
                    title=title,
                    body_summary=body_summary,
                    has_init_image=(scene.video_mode == "i2v"),
                )
                logger.info(
                    "[prompt] 씬 %d 프롬프트 생성 완료 (%d자)", i, len(scene.video_prompt)
                )
            except Exception as e:
                logger.error("[prompt] 씬 %d 프롬프트 생성 실패: %s", i, e)
                scene.video_prompt = None
        return scenes


def _clean_prompt(raw: str) -> str:
    """LLM 출력을 단일 단락으로 정리."""
    prompt = " ".join(raw.strip().splitlines()).strip()
    # 따옴표 래핑 제거
    if prompt.startswith('"') and prompt.endswith('"'):
        prompt = prompt[1:-1].strip()
    # 마크다운 헤더 제거
    if prompt.startswith("#"):
        lines = prompt.split(". ", 1)
        prompt = lines[1] if len(lines) > 1 else prompt
    return prompt
