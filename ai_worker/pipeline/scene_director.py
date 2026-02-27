"""Phase 4: 씬 배분 알고리즘 (Scene Director)

scene_policy.json 정책과 mood 프리셋을 기반으로
intro / body(img_text|text_only|img_only) / outro 씬을 순서대로 배분한다.

씬 흐름:
    intro(hook) → [img_text | text_only | img_only] × N → outro(closer + mood 아웃트로 이미지)

- 이미지 있으면 → img_text 우선 (균등 분배)
- 이미지 소진 후 → text_only
- 이미지가 텍스트보다 많으면 → img_only
- intro/outro 이미지: policy의 mood 폴더에서 랜덤 선택
"""
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ai_worker.pipeline.resource_analyzer import ResourceProfile
from config.settings import EMOTION_TAGS

logger = logging.getLogger(__name__)

SceneType = Literal["intro", "img_text", "text_only", "img_only", "outro"]

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
    text_lines: list          # str 또는 {"text": str, "audio": str|None} — TTS 사전 생성 후 dict로 교체
    image_url: str | None     # img_text / outro 에서 사용
    text_only_stack: int = 1  # text_only 씬의 실제 스택 줄 수
    emotion_tag: str = ""     # Fish Speech 감정 태그 (EMOTION_TAGS에서 자동 할당)
    voice_override: str | None = None  # 댓글 씬: comment_voices에서 random 선택
    mood: str = "daily"           # 콘텐츠 mood 키
    tts_emotion: str = ""         # TTS 감정 톤 키 (예: "gentle", "cheerful")
    bgm_path: str | None = None   # BGM 파일 경로 (intro 씬에만 설정)
    block_type: str = "body"      # "body" 또는 "comment" (렌더링 UI 분기)
    author: str | None = None     # comment 타입의 작성자 닉네임


def pick_random_file(dir_path: str, extensions: list[str]) -> Path | None:
    """지정 폴더에서 지원 확장자의 파일 하나를 랜덤 선택. 비어있거나 폴더 없으면 None."""
    folder = Path(dir_path)
    if not folder.is_dir():
        return None
    files = [f for f in folder.iterdir() if f.suffix.lower() in extensions]
    return random.choice(files) if files else None


def distribute_images(
    body_items: list[tuple[str, str | None, str, str | None]],
    images: list[str],
    max_images: int,
    tts_emotion: str = "",
    mood: str = "daily",
) -> list[SceneDecision]:
    """본문 아이템에 이미지를 균등 분배하여 SceneDecision 리스트 생성.

    Args:
        body_items: (text, voice_override, block_type, author) 튜플 리스트
        images: 사용 가능한 이미지 경로 리스트
        max_images: 최대 이미지 사용 수
        tts_emotion: TTS 감정 키
        mood: 콘텐츠 mood 키
    """
    remaining_imgs = images[:max_images]

    def _make(
        type_: str, text: str, image: str | None = None,
        voice: str | None = None, block_type: str = "body", author: str | None = None,
    ) -> SceneDecision:
        return SceneDecision(
            type=type_,
            text_lines=[text],
            image_url=image,
            mood=mood,
            tts_emotion=tts_emotion,
            voice_override=voice,
            block_type=block_type,
            author=author,
        )

    # Case 5: 텍스트 없이 이미지만
    if not body_items and remaining_imgs:
        return [_make("img_only", "", img) for img in remaining_imgs]

    # Case 4: 이미지 없음
    if not remaining_imgs:
        return [_make("text_only", text, voice=voice, block_type=bt, author=au) for text, voice, bt, au in body_items]

    total = len(body_items)
    n_imgs = len(remaining_imgs)

    # Case 3: 이미지 ≥ 텍스트 — 매 줄 img_text
    if n_imgs >= total:
        return [
            _make("img_text", text, remaining_imgs[i] if i < n_imgs else None, voice, bt, au)
            for i, (text, voice, bt, au) in enumerate(body_items)
        ]

    # Case 1, 2: 균등 분배
    interval = total / (n_imgs + 1)
    img_positions = {round(interval * (i + 1)) - 1 for i in range(n_imgs)}
    img_idx = 0
    scenes: list[SceneDecision] = []

    for line_idx, (text, voice, bt, au) in enumerate(body_items):
        if line_idx in img_positions and img_idx < n_imgs:
            scenes.append(_make("img_text", text, remaining_imgs[img_idx], voice, bt, au))
            img_idx += 1
        else:
            scenes.append(_make("text_only", text, voice=voice, block_type=bt, author=au))
    return scenes


class SceneDirector:
    """scene_policy.json 정책과 mood를 기반으로 씬 목록을 결정한다."""

    def __init__(
        self,
        profile: ResourceProfile,
        images: list[str],
        script: dict,
        comment_voices: list[str] | None = None,
        mood: str = "daily",
    ) -> None:
        self.profile = profile
        self._images: list[str] = list(images)   # 소모 추적용 복사본
        self.script = script
        self.mood = mood

        if comment_voices is None:
            # pipeline.json에서 자동 로드 (processor.py 레거시 경로용)
            try:
                import json as _json
                from config.settings import load_pipeline_config
                _cfg = load_pipeline_config()
                self.comment_voices = _json.loads(_cfg.get("comment_voices", "[]"))
            except Exception:
                self.comment_voices = []
        else:
            self.comment_voices = comment_voices

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def direct(self) -> list[SceneDecision]:
        """씬 배분 목록을 생성해 반환한다 (scene_policy.json 기반)."""
        import json as _json
        from pathlib import Path as _Path

        # scene_policy.json 로드
        policy_path = _Path("config/scene_policy.json")
        try:
            policy = _json.loads(policy_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("scene_policy.json 로드 실패, fallback 모드: %s", e)
            policy = None

        mood = self.mood
        fallback_mood = "daily"

        if policy:
            moods_dict = policy.get("moods", {})
            supported_image_ext = policy.get("defaults", {}).get("supported_image_ext", [".png", ".jpg", ".jpeg", ".webp"])
            supported_bgm_ext = policy.get("defaults", {}).get("supported_bgm_ext", [".mp3", ".wav", ".ogg"])
            max_body_images = policy.get("defaults", {}).get("max_body_images", 8)
            fallback_mood = policy.get("defaults", {}).get("fallback_mood", "daily")
            fixed_texts = policy.get("scene_rules", {}).get("outro", {}).get("fixed_texts", ["여러분들의 생각은 어떤가요?"])

            # mood 프리셋 조회 (없으면 fallback)
            if mood not in moods_dict:
                logger.warning("mood '%s' 미인식, fallback '%s' 사용", mood, fallback_mood)
                mood = fallback_mood
            preset = moods_dict.get(mood, moods_dict.get(fallback_mood, {}))
            tts_emotion = preset.get("tts_emotion", "")

            def _pick_asset(dir_key: str) -> _Path | None:
                dir_path = preset.get(dir_key, "")
                result = pick_random_file(dir_path, supported_image_ext)
                if result is None and mood != fallback_mood:
                    # fallback mood 폴더 시도
                    fb_preset = moods_dict.get(fallback_mood, {})
                    result = pick_random_file(fb_preset.get(dir_key, ""), supported_image_ext)
                return result

            def _pick_bgm() -> _Path | None:
                # BGM 폴더가 비어있으면 fallback 없이 None 반환 → BGM 미사용
                return pick_random_file(preset.get("bgm_dir", ""), supported_bgm_ext)
        else:
            # policy 없을 때 fallback (기존 동작 유지)
            tts_emotion = ""
            max_body_images = 20
            fixed_texts = ["여러분들의 생각은 어떤가요?"]

            def _pick_asset(_: str) -> None:
                return None

            def _pick_bgm() -> None:
                return None

        scenes: list[SceneDecision] = []

        # BGM 선택 (intro 씬에만 설정)
        bgm_file = _pick_bgm()
        bgm_path = str(bgm_file) if bgm_file else None

        # ── Intro ──────────────────────────────────────────────────────
        hook = self.script.get("hook", "")
        if self._images:
            # 이미지 있으면 첫 이미지 사용
            intro_img = self._images.pop(0)
            intro_type = "img_text"
        else:
            # 이미지 없으면 mood 폴더에서 랜덤
            intro_asset = _pick_asset("intro_image_dir")
            intro_img = str(intro_asset) if intro_asset else None
            intro_type = "img_only" if intro_img else "intro"

        scenes.append(SceneDecision(
            type=intro_type,
            text_lines=[hook],
            image_url=intro_img,
            mood=mood,
            tts_emotion=tts_emotion,
            bgm_path=bgm_path,
        ))

        # ── Body ───────────────────────────────────────────────────────
        body_raw = list(self.script.get("body", []))
        body_items: list[tuple[str, str | None, str, str | None]] = []
        for item in body_raw:
            if isinstance(item, dict):
                text = " ".join(item.get("lines", []))
                block_type = item.get("type", "body")
                author = item.get("author")
                is_comment = block_type == "comment"
                voice = random.choice(self.comment_voices) if is_comment and self.comment_voices else None
                body_items.append((text, voice, block_type, author))
            else:
                body_items.append((str(item), None, "body", None))

        body_scenes = distribute_images(
            body_items,
            list(self._images),
            max_body_images,
            tts_emotion=tts_emotion,
            mood=mood,
        )
        # _images에서 사용된 이미지 소모 추적
        used_img_count = sum(1 for s in body_scenes if s.image_url is not None)
        self._images = self._images[used_img_count:]
        scenes.extend(body_scenes)

        # ── Outro ──────────────────────────────────────────────────────
        outro_asset = _pick_asset("outro_image_dir")
        outro_img = str(outro_asset) if outro_asset else None
        outro_text = random.choice(fixed_texts)
        scenes.append(SceneDecision(
            type="outro",
            text_lines=[outro_text],
            image_url=outro_img,
            mood=mood,
            tts_emotion=tts_emotion,
        ))

        logger.debug(
            "씬 배분: 총 %d개 (%s) [mood=%s, tts_emotion=%s, bgm=%s]",
            len(scenes),
            ", ".join(s.type for s in scenes),
            mood,
            tts_emotion,
            bgm_path,
        )
        return scenes

    # ------------------------------------------------------------------
    # Private helpers (레거시 — distribute_images()로 대체됨, 하위 호환 유지)
    # ------------------------------------------------------------------

    def _make_scene(
        self,
        type_: str,
        lines: list[str],
        image: str | None = None,
        stack: int = 1,
        voice_override: str | None = None,
    ) -> SceneDecision:
        """SceneDecision을 생성하며 emotion_tag를 자동 할당한다."""
        return SceneDecision(
            type=type_,
            text_lines=lines,
            image_url=image,
            text_only_stack=stack,
            emotion_tag=EMOTION_TAGS.get(type_, ""),
            voice_override=voice_override,
            mood=self.mood,
        )
