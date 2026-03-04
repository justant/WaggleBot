"""레이아웃 렌더러 v2 — 베이스 프레임 베이킹 + 고정 Y좌표 슬롯.

씬 타입:
  intro     - 제목만 (title_only.svg)   → 베이스 프레임 그대로 사용
  image_text  - 이미지 + 텍스트 (image_text.svg)
  text_only - 텍스트만, 3슬롯 고정 Y (text_only.svg)
  outro     - 이미지만 (image_only.svg)   → 이미지가 남을 때 마지막 1프레임

핵심 설계:
  1. _create_base_frame() — base_layout.png + 제목을 헤더에 1회 합성
  2. 모든 씬 렌더러가 base_frame.copy()에서 시작 → 제목 위치 완전 고정
  3. text_only는 y_coords[] 배열로 슬롯별 Y좌표 명시 (동적 계산 없음)

배분 알고리즘:
  ratio = 이미지수 / 본문문장수
  ratio >= 0.8 → image_heavy : 거의 모든 문장에 이미지 사용
  ratio >= 0.3 → balanced  : 이미지 균등 분배
  ratio <  0.3 → text_heavy: text_only 위주, 앞에서 일부만 image_text
"""
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from PIL import ImageFont

from config.settings import ASSETS_DIR, MEDIA_DIR

# ── 내부 모듈 re-import (기존 import 경로 호환) ──
from ai_worker.renderer._frames import (
    CANVAS_W, CANVAS_H, HEADER_H, HEADER_COLOR,
    _create_base_frame, _render_intro_frame, _render_image_text_frame,
    _render_text_only_frame, _render_image_only_frame, _render_outro_frame,
    _render_video_text_overlay, _wrap_korean, _draw_centered_text,
    _truncate, _fit_cover, _paste_rounded, _load_image,
)
from ai_worker.renderer._tts import (
    _tts_chunk_async, _generate_tts_chunks, _merge_chunks,
    _get_audio_duration, _unpack_line, _INTRO_PAUSE_SEC,
)
from ai_worker.renderer._encode import (
    _render_video_segment, _render_static_segment,
    _resolve_codec, _get_encoder_args, _escape_ffmpeg_text,
    _build_layout_sfx_filter,
)

logger = logging.getLogger(__name__)

_LAYOUT_CONFIG: dict | None = None


# ---------------------------------------------------------------------------
# 설정 로더
# ---------------------------------------------------------------------------

def _load_layout() -> dict:
    global _LAYOUT_CONFIG
    if _LAYOUT_CONFIG is None:
        cfg_path = Path(__file__).resolve().parent.parent.parent / "config" / "layout.json"
        with open(cfg_path, encoding="utf-8") as f:
            _LAYOUT_CONFIG = json.load(f)
    return _LAYOUT_CONFIG


# ---------------------------------------------------------------------------
# 공통 유틸리티
# ---------------------------------------------------------------------------

def _apply_vf_weight(font: ImageFont.FreeTypeFont, filename: str) -> None:
    """가변 폰트(Variable Font)의 굵기 축을 파일명에서 추론해 설정한다."""
    name_upper = Path(filename).stem.upper()
    if "BOLD" in name_upper:
        weight_name = "Bold"
    elif "MEDIUM" in name_upper:
        weight_name = "Medium"
    elif "LIGHT" in name_upper:
        weight_name = "Light"
    else:
        return
    try:
        font.set_variation_by_name(weight_name)
    except Exception:
        pass


def _load_font(font_dir: Path, filename: str, size: int) -> ImageFont.FreeTypeFont:
    """폰트 로드 (assets/fonts → 시스템 한글 → PIL 기본 폰트)."""
    font_path = font_dir / filename
    if font_path.exists():
        try:
            font = ImageFont.truetype(str(font_path), size)
            _apply_vf_weight(font, filename)
            return font
        except Exception:
            pass
    try:
        result = subprocess.run(
            ["fc-list", ":lang=ko", "--format=%{file}\n"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            p = line.strip()
            if p and Path(p).exists():
                try:
                    font = ImageFont.truetype(p, size)
                    _apply_vf_weight(font, filename)
                    return font
                except Exception:
                    continue
    except Exception:
        pass
    logger.warning("폰트 없음: %s — PIL 기본 폰트 사용", filename)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _run_async(coro) -> object:
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 배분 알고리즘
# ---------------------------------------------------------------------------

def _plan_sequence(
    sentences: list[dict],
    images: list[str],
    layout: dict,
) -> list[dict]:
    """이미지:텍스트 비율에 따라 씬 유형을 결정한다."""
    alg = layout.get("layout_algorithm", {})
    heavy_thr = alg.get("image_heavy_threshold", 0.8)
    mixed_thr = alg.get("image_mixed_threshold", 0.3)

    n_imgs = len(images)
    plan: list[dict] = []

    if not sentences:
        return plan

    plan.append({"type": "intro", "sent_idx": 0, "img_idx": None})

    body_sents = sentences[1:]
    n_body = len(body_sents)
    img_idx = 0

    if n_body == 0:
        if img_idx < n_imgs:
            plan.append({"type": "outro", "sent_idx": None, "img_idx": img_idx})
        return plan

    ratio = n_imgs / n_body

    if ratio >= heavy_thr:
        img_slots = set(range(n_body))
    elif ratio >= mixed_thr:
        if n_imgs > 0:
            step = n_body / n_imgs
            img_slots = {min(int(k * step), n_body - 1) for k in range(n_imgs)}
        else:
            img_slots = set()
    else:
        n_use = min(n_imgs, max(1, n_body // 4)) if n_imgs > 0 else 0
        img_slots = set(range(n_use))

    for local_i in range(n_body):
        sent_idx = local_i + 1
        if local_i in img_slots and img_idx < n_imgs:
            plan.append({"type": "image_text", "sent_idx": sent_idx, "img_idx": img_idx})
            img_idx += 1
        else:
            plan.append({"type": "text_only", "sent_idx": sent_idx, "img_idx": None})

    if img_idx < n_imgs:
        plan.append({"type": "outro", "sent_idx": None, "img_idx": img_idx})

    return plan


# ---------------------------------------------------------------------------
# SceneDecision 변환 유틸리티
# ---------------------------------------------------------------------------

def _scenes_to_plan_and_sentences(
    scenes: list,
) -> tuple[list[dict], list[dict], list[str]]:
    """SceneDecision 목록을 내부 렌더러 형식 (sentences, plan, images)으로 변환한다."""
    sentences: list[dict] = []
    plan: list[dict] = []
    images: list[str] = []

    for scene_i, scene in enumerate(scenes):
        img_idx: Optional[int] = None
        if scene.image_url:
            img_idx = len(images)
            images.append(scene.image_url)

        if scene.type == "intro":
            text, audio = _unpack_line(scene.text_lines[0]) if scene.text_lines else ("", None)
            sent_idx = len(sentences)
            sentences.append({"text": text, "section": "hook", "audio": audio, "voice_override": None})
            plan.append({"type": "intro", "sent_idx": sent_idx, "img_idx": img_idx, "scene_idx": scene_i})

        elif scene.type == "image_text":
            text, audio = _unpack_line(scene.text_lines[0]) if scene.text_lines else ("", None)
            sent_idx = len(sentences)
            sent_dict: dict = {
                "text": text, "section": "body", "audio": audio,
                "voice_override": scene.voice_override,
                "block_type": getattr(scene, "block_type", "body"),
                "author": getattr(scene, "author", None),
            }
            psl = getattr(scene, "pre_split_lines", None)
            if psl:
                sent_dict["lines"] = psl
            sentences.append(sent_dict)
            plan.append({"type": "image_text", "sent_idx": sent_idx, "img_idx": img_idx, "scene_idx": scene_i})

        elif scene.type == "text_only":
            psl = getattr(scene, "pre_split_lines", None)
            for line in scene.text_lines:
                text, audio = _unpack_line(line)
                sent_idx = len(sentences)
                sent_dict = {
                    "text": text, "section": "body", "audio": audio,
                    "voice_override": scene.voice_override,
                    "block_type": getattr(scene, "block_type", "body"),
                    "author": getattr(scene, "author", None),
                }
                if psl:
                    sent_dict["lines"] = psl
                sentences.append(sent_dict)
                plan.append({"type": "text_only", "sent_idx": sent_idx, "img_idx": None, "scene_idx": scene_i})

        elif scene.type == "image_only":
            text, audio = _unpack_line(scene.text_lines[0]) if scene.text_lines else ("", None)
            sent_idx_val: Optional[int] = None
            if text:
                sent_idx_val = len(sentences)
                sentences.append({"text": text, "section": "body", "audio": audio, "voice_override": scene.voice_override})
            plan.append({"type": "image_only", "sent_idx": sent_idx_val, "img_idx": img_idx, "scene_idx": scene_i})

        elif scene.type == "outro":
            text, audio = _unpack_line(scene.text_lines[0]) if scene.text_lines else ("", None)
            sent_idx_val = None
            if text:
                sent_idx_val = len(sentences)
                sentences.append({"text": text, "section": "closer", "audio": audio, "voice_override": None})
            plan.append({"type": "outro", "sent_idx": sent_idx_val, "img_idx": img_idx, "scene_idx": scene_i})

    return sentences, plan, images


def _get_scene_for_entry(
    entry: dict,
    sentences: list[dict],
    scenes_list: list | None,
) -> object | None:
    """plan entry에 대응하는 SceneDecision을 찾는다."""
    if scenes_list is None:
        return None

    scene_idx = entry.get("scene_idx")
    if scene_idx is not None and 0 <= scene_idx < len(scenes_list):
        return scenes_list[scene_idx]

    sent_idx = entry.get("sent_idx")
    if sent_idx is None:
        return None

    target_text = sentences[sent_idx].get("text", "")
    if not target_text:
        return None

    for scene in scenes_list:
        for line in scene.text_lines:
            line_text = line.get("text", "") if isinstance(line, dict) else str(line)
            if line_text and line_text in target_text:
                return scene

    return None


# ---------------------------------------------------------------------------
# 공통 렌더링 파이프라인 (Steps 2 / 4 – 11)
# ---------------------------------------------------------------------------

def _render_pipeline(
    post_id: int,
    title: str,
    sentences: list[dict],
    plan: list[dict],
    images: list[str],
    output_path: Path,
    layout: dict,
    voice: str,
    rate: str,
    sfx_offset: float,
    max_slots: int,
    font_dir: Path,
    audio_dir: Path,
    save_tts_cache: Path | None = None,
    tts_audio_cache: Path | None = None,
    bgm_path: Path | None = None,
    scenes_list: list | None = None,
) -> Path:
    """sentences / plan / images 를 받아 mp4를 생성한다."""
    tmp_dir = MEDIA_DIR / "tmp" / f"layout_{post_id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 2: 베이스 프레임 베이킹 ──────────────────────
        base_frame = _create_base_frame(layout, title, font_dir, ASSETS_DIR)
        logger.info("[layout] 베이스 프레임 생성 완료 (제목 헤더 고정)")

        # ── Step 4: 이미지 사전 다운로드 ──────────────────────
        image_cache: dict[int, Optional["Image.Image"]] = {}
        for entry in plan:
            img_idx = entry.get("img_idx")
            if img_idx is not None and img_idx not in image_cache:
                url = images[img_idx] if img_idx < len(images) else None
                image_cache[img_idx] = _load_image(url, tmp_dir) if url else None

        # ── Steps 5~6: TTS 생성 또는 캐시 로드 ───────────────────
        merged_tts = tmp_dir / "merged_tts.wav"
        if tts_audio_cache and (tts_audio_cache / "durations.json").exists():
            durations: list[float] = json.loads(
                (tts_audio_cache / "durations.json").read_text(encoding="utf-8")
            )
            shutil.copy2(tts_audio_cache / "merged_tts.wav", merged_tts)
            total_dur = sum(durations)
            logger.info("[layout] TTS 캐시 사용: post_id=%d (%d프레임, 총 %.1fs)",
                        post_id, len(durations), total_dur)
        else:
            logger.info("[layout] TTS 생성 시작")
            from ai_worker.tts.fish_client import _warmup_model
            _run_async(_warmup_model())
            t0 = time.time()
            durations = _run_async(
                _generate_tts_chunks(plan, sentences, tmp_dir, voice, rate)
            )
            total_dur = sum(durations)
            logger.info("[layout] TTS 완료: %d프레임, 총 %.1fs (%.2fs)",
                        len(durations), total_dur, time.time() - t0)

            chunk_paths = [tmp_dir / f"chunk_{i:03d}.wav" for i in range(len(plan))]
            _merge_chunks(chunk_paths, merged_tts)

            if save_tts_cache:
                save_tts_cache.mkdir(parents=True, exist_ok=True)
                shutil.copy2(merged_tts, save_tts_cache / "merged_tts.wav")
                (save_tts_cache / "durations.json").write_text(
                    json.dumps(durations), encoding="utf-8"
                )
                logger.info("[layout] TTS 캐시 저장: %s", save_tts_cache)

        # ── Step 7: text_only용 줄바꿈 사전 계산 ──────────────
        sc_to = layout["scenes"]["text_only"]
        to_ta = sc_to["elements"]["text_area"]
        to_font = _load_font(font_dir, "NotoSansKR-Medium.ttf", to_ta["font_size"])
        to_max_w = to_ta["max_width"]
        to_max_chars = sc_to.get("text_max_chars", 0)

        for sent in sentences:
            if "lines" in sent:
                expanded: list[str] = []
                for line in sent["lines"]:
                    expanded.extend(_wrap_korean(line, to_font, to_max_w))
                sent["lines"] = expanded
                continue
            sent["lines"] = _wrap_korean(sent["text"], to_font, to_max_w)

        # ── Step 8: PIL 프레임 생성 ────────────────────────────
        logger.info("[layout] 프레임 생성 시작")
        t1 = time.time()
        frame_paths: list[Path] = []
        text_only_history: list[dict] = []

        for frame_idx, entry in enumerate(plan):
            scene_type = entry["type"]
            sent_idx = entry.get("sent_idx")
            img_idx = entry.get("img_idx")
            frame_path = tmp_dir / f"frame_{frame_idx:03d}.png"

            if scene_type != "text_only":
                text_only_history = []

            if scene_type == "intro":
                _render_intro_frame(base_frame, frame_path)

            elif scene_type == "image_text":
                img_pil = image_cache.get(img_idx) if img_idx is not None else None
                text = sentences[sent_idx]["text"] if sent_idx is not None else ""
                if img_pil is None:
                    logger.warning("[layout] 프레임 %d: image_text→text_only 폴백 (이미지 없음)", frame_idx)
                    lines = sentences[sent_idx].get("lines", [text]) if sent_idx is not None else [text]
                    fallback_entry = {"lines": lines, "is_new": True,
                                      "block_type": entry.get("block_type", "body"),
                                      "author": entry.get("author")}
                    _render_text_only_frame(base_frame, [fallback_entry], layout, font_dir, frame_path)
                else:
                    _render_image_text_frame(base_frame, img_pil, text, layout, font_dir, frame_path)

            elif scene_type == "text_only":
                for prev in text_only_history:
                    prev["is_new"] = False

                new_lines = sentences[sent_idx]["lines"] if sent_idx is not None else []

                if len(text_only_history) >= max_slots:
                    if len(new_lines) > max_slots:
                        logger.warning("[layout] 프레임 %d: %d줄 초과 — 단독 표시",
                                       frame_idx, len(new_lines))
                    text_only_history = []

                sent_data = sentences[sent_idx] if sent_idx is not None else {}
                text_only_history.append({
                    "lines": new_lines,
                    "is_new": True,
                    "block_type": sent_data.get("block_type", "body"),
                    "author": sent_data.get("author"),
                })
                _render_text_only_frame(base_frame, text_only_history, layout, font_dir, frame_path)

            elif scene_type == "image_only":
                img_pil = image_cache.get(img_idx) if img_idx is not None else None
                _render_image_only_frame(base_frame, img_pil, layout, frame_path)

            elif scene_type == "outro":
                img_pil = image_cache.get(img_idx) if img_idx is not None else None
                _render_outro_frame(base_frame, img_pil, "", layout, font_dir, frame_path)

            frame_paths.append(frame_path)

        logger.info("[layout] 프레임 %d장 완료 (%.2fs)", len(frame_paths), time.time() - t1)

        # 비디오 씬 존재 여부 확인
        has_video_scenes = False
        if scenes_list:
            has_video_scenes = any(
                getattr(s, "video_clip_path", None)
                and not getattr(s, "video_generation_failed", False)
                for s in scenes_list
            )

        if has_video_scenes:
            # ── Step 8.5: 하이브리드 세그먼트 생성 ─────────────────
            logger.info("[layout] 하이브리드 렌더링: 비디오 씬 포함")
            segment_paths: list[Path] = []
            for frame_idx, (entry, dur) in enumerate(zip(plan, durations)):
                segment_path = tmp_dir / f"seg_{frame_idx:03d}.mp4"
                scene = _get_scene_for_entry(entry, sentences, scenes_list)

                if (
                    scene is not None
                    and getattr(scene, "video_clip_path", None)
                    and not getattr(scene, "video_generation_failed", False)
                ):
                    text = sentences[entry["sent_idx"]]["text"] if entry.get("sent_idx") is not None else ""
                    try:
                        _render_video_segment(
                            base_frame=base_frame,
                            scene=scene,
                            text=text,
                            duration=dur,
                            layout=layout,
                            font_dir=font_dir,
                            output_path=segment_path,
                        )
                    except Exception as e:
                        logger.warning(
                            "[layout] 비디오 세그먼트 %d 생성 실패, 정적 폴백: %s",
                            frame_idx, e,
                        )
                        _render_static_segment(frame_paths[frame_idx], dur, segment_path)
                else:
                    _render_static_segment(frame_paths[frame_idx], dur, segment_path)

                segment_paths.append(segment_path)

            # ── Step 9: segment concat ─────────────────────────────
            concat_file = tmp_dir / "concat_list.txt"
            concat_lines_list: list[str] = []
            for sp in segment_paths:
                concat_lines_list.append(f"file '{sp.resolve()}'\n")
            concat_file.write_text("".join(concat_lines_list), encoding="utf-8")

            video_only = tmp_dir / "video_only.mp4"
            concat_cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(concat_file),
                "-c", "copy",
                str(video_only),
            ]
            concat_result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=300)
            if concat_result.returncode != 0:
                logger.error("[layout] concat 실패:\n%s", concat_result.stderr[-2000:])
                raise subprocess.CalledProcessError(concat_result.returncode, concat_cmd)

            # ── Step 10–11: 오디오 합성 ────────────────────────────
            timings: list[float] = []
            acc = 0.0
            for dur in durations:
                timings.append(acc)
                acc += dur

            extra_inputs, sfx_filter = _build_layout_sfx_filter(
                plan, timings, audio_dir, layout,
                tts_input_idx=1, sfx_offset=sfx_offset,
            )

            effective_bgm: Path | None = None
            if bgm_path is not None and Path(bgm_path).exists():
                effective_bgm = Path(bgm_path)
                logger.info("[layout] BGM 사용 (bgm_path): %s", effective_bgm.name)
            elif bgm_path is not None:
                logger.warning("[layout] bgm_path 파일 없음: %s — BGM 없이 인코딩", bgm_path)

            if effective_bgm is not None:
                bgm_audio_filter = (
                    f"[1:a]apad[tts_pad];"
                    f"[2:a]volume=0.15,aloop=loop=-1:size=2e+09[bgm_loop];"
                    f"[tts_pad][bgm_loop]amix=inputs=2:duration=first:normalize=0[aout]"
                )
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(video_only),
                    "-i", str(merged_tts),
                    "-stream_loop", "-1", "-i", str(effective_bgm),
                    "-filter_complex", bgm_audio_filter,
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    str(output_path),
                ]
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-i", str(video_only),
                    "-i", str(merged_tts),
                    *extra_inputs,
                    "-filter_complex", sfx_filter,
                    "-map", "0:v", "-map", "[aout]",
                    "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k",
                    str(output_path),
                ]
        else:
            # ── Step 9 (기존): 정적 PNG concat ─────────────────────
            concat_file = tmp_dir / "concat_list.txt"
            concat_lines: list[str] = []
            for fp, dur in zip(frame_paths, durations):
                concat_lines.append(f"file '{fp.resolve()}'\n")
                concat_lines.append(f"duration {dur:.4f}\n")
            if frame_paths:
                concat_lines.append(f"file '{frame_paths[-1].resolve()}'\n")
            concat_file.write_text("".join(concat_lines), encoding="utf-8")

            # ── Step 10: 타임스탬프 + SFX ──────────────────────────
            timings = []
            acc = 0.0
            for dur in durations:
                timings.append(acc)
                acc += dur

            extra_inputs, sfx_filter = _build_layout_sfx_filter(
                plan, timings, audio_dir, layout,
                tts_input_idx=1, sfx_offset=sfx_offset,
            )

            # ── Step 11: FFmpeg 인코딩 ─────────────────────────────
            codec = _resolve_codec()
            enc_args = _get_encoder_args(codec)
            video_filter = (
                "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2[vout]"
            )

            effective_bgm = None
            if bgm_path is not None and Path(bgm_path).exists():
                effective_bgm = Path(bgm_path)
                logger.info("[layout] BGM 사용 (bgm_path): %s", effective_bgm.name)
            elif bgm_path is not None:
                logger.warning("[layout] bgm_path 파일 없음: %s — BGM 없이 인코딩", bgm_path)

            if effective_bgm is not None:
                bgm_sfx_extra, bgm_sfx_filter = _build_layout_sfx_filter(
                    plan, timings, audio_dir, layout,
                    tts_input_idx=1, sfx_offset=sfx_offset,
                )
                bgm_audio_filter = (
                    f"[1:a]apad[tts_pad];"
                    f"[2:a]volume=0.15,aloop=loop=-1:size=2e+09[bgm_loop];"
                    f"[tts_pad][bgm_loop]amix=inputs=2:duration=first:normalize=0[aout_premix]"
                )
                if bgm_sfx_extra:
                    bgm_extra_sfx, bgm_sfx_str = _build_layout_sfx_filter(
                        plan, timings, audio_dir, layout,
                        tts_input_idx=1, sfx_offset=sfx_offset,
                    )
                    bgm_sfx_str_patched = bgm_sfx_str.replace(
                        f"[1:a]acopy[aout]", "[aout_premix]acopy[aout]"
                    ).replace(
                        f"[1:a]", "[aout_premix]"
                    )
                    filter_complex = f"{video_filter};{bgm_audio_filter};{bgm_sfx_str_patched}"
                    cmd = [
                        "ffmpeg", "-y",
                        "-f", "concat", "-safe", "0", "-i", str(concat_file),
                        "-i", str(merged_tts),
                        "-stream_loop", "-1", "-i", str(effective_bgm),
                        *bgm_extra_sfx,
                        "-filter_complex", filter_complex,
                        "-map", "[vout]", "-map", "[aout]",
                        *enc_args,
                        "-c:a", "aac", "-b:a", "192k", "-r", "30",
                        str(output_path),
                    ]
                else:
                    bgm_audio_filter_final = bgm_audio_filter.replace(
                        "[aout_premix]", "[aout]"
                    )
                    filter_complex = f"{video_filter};{bgm_audio_filter_final}"
                    cmd = [
                        "ffmpeg", "-y",
                        "-f", "concat", "-safe", "0", "-i", str(concat_file),
                        "-i", str(merged_tts),
                        "-stream_loop", "-1", "-i", str(effective_bgm),
                        "-filter_complex", filter_complex,
                        "-map", "[vout]", "-map", "[aout]",
                        *enc_args,
                        "-c:a", "aac", "-b:a", "192k", "-r", "30",
                        str(output_path),
                    ]
            else:
                filter_complex = f"{video_filter};{sfx_filter}"
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(concat_file),
                    "-i", str(merged_tts),
                    *extra_inputs,
                    "-filter_complex", filter_complex,
                    "-map", "[vout]", "-map", "[aout]",
                    *enc_args,
                    "-c:a", "aac", "-b:a", "192k", "-r", "30",
                    str(output_path),
                ]

        logger.info("[layout] FFmpeg 인코딩 시작: %s", output_path.name)
        ffmpeg_result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if ffmpeg_result.returncode != 0:
            logger.error("[layout] FFmpeg 실패 (returncode=%d):\n%s",
                         ffmpeg_result.returncode, ffmpeg_result.stderr[-3000:])
            raise subprocess.CalledProcessError(
                ffmpeg_result.returncode, cmd, ffmpeg_result.stdout, ffmpeg_result.stderr
            )

        logger.info("[layout] 완료: %s (총 %.1fs)", output_path.name, total_dur)
        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_layout_video(post, script, output_path: Path | None = None) -> Path:
    """레이아웃 기반 쇼츠 영상 렌더링."""
    from config import settings as s
    from config.settings import load_pipeline_config, VOICE_DEFAULT

    layout = _load_layout()
    _pipeline_cfg = load_pipeline_config()
    voice: str = _pipeline_cfg.get("tts_voice", VOICE_DEFAULT)
    rate: str = getattr(s, "TTS_RATE", "+25%")
    sfx_offset: float = getattr(s, "SFX_OFFSET", -0.15)
    max_slots: int = layout["scenes"]["text_only"]["elements"]["text_area"].get("max_slots", 3)
    font_dir: Path = ASSETS_DIR / "fonts"
    audio_dir: Path = getattr(s, "AUDIO_DIR", ASSETS_DIR / "audio")

    video_dir = MEDIA_DIR / "video" / post.site_code
    video_dir.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        output_path = video_dir / f"post_{post.origin_id}_SD.mp4"

    sentences: list[dict] = []
    sentences.append({"text": script.hook, "section": "hook"})
    for body_item in script.body:
        if isinstance(body_item, dict):
            pre_split_lines: list[str] | None = body_item.get("lines")
            body_text = " ".join(pre_split_lines) if pre_split_lines else ""
            block_type = body_item.get("type", "body")
            author = body_item.get("author")
        else:
            body_text = str(body_item)
            pre_split_lines = None
            block_type = "body"
            author = None

        is_quote = block_type == "comment" or any(
            q in body_text for q in ('"', "'", "\u2018", "\u2019", "\u201c", "\u201d")
        )
        sent: dict = {
            "text": body_text,
            "section": "comment" if is_quote else "body",
            "block_type": block_type,
        }
        if author:
            sent["author"] = author
        if pre_split_lines:
            sent["lines"] = pre_split_lines
        sentences.append(sent)
    sentences.append({"text": script.closer, "section": "closer"})

    images: list[str] = post.images if isinstance(post.images, list) else []
    logger.info("[layout] post_id=%d 문장=%d 이미지=%d", post.id, len(sentences), len(images))

    plan = _plan_sequence(sentences, images, layout)
    logger.info("[layout] 씬 계획: %s", [p["type"] for p in plan])

    return _render_pipeline(
        post.id, post.title or "", sentences, plan, images,
        output_path, layout, voice, rate, sfx_offset, max_slots, font_dir, audio_dir,
    )


def render_layout_video_from_scenes(
    post,
    scenes: list,
    output_path: Path | None = None,
    save_tts_cache: Path | None = None,
    tts_audio_cache: Path | None = None,
) -> Path:
    """SceneDirector 출력(SceneDecision 목록)으로 직접 렌더링."""
    from config import settings as s
    from config.settings import load_pipeline_config, VOICE_DEFAULT

    layout = _load_layout()
    _pipeline_cfg = load_pipeline_config()
    voice: str = _pipeline_cfg.get("tts_voice", VOICE_DEFAULT)
    rate: str = getattr(s, "TTS_RATE", "+25%")
    sfx_offset: float = getattr(s, "SFX_OFFSET", -0.15)
    max_slots: int = layout["scenes"]["text_only"]["elements"]["text_area"].get("max_slots", 3)
    font_dir: Path = ASSETS_DIR / "fonts"
    audio_dir: Path = getattr(s, "AUDIO_DIR", ASSETS_DIR / "audio")

    video_dir = MEDIA_DIR / "video" / post.site_code
    video_dir.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        output_path = video_dir / f"post_{post.origin_id}_SD.mp4"

    sentences, plan, images = _scenes_to_plan_and_sentences(scenes)
    logger.info(
        "[layout:scenes] post_id=%d 씬=%d 문장=%d 이미지=%d",
        post.id, len(scenes), len(sentences), len(images),
    )

    bgm_path: Path | None = None
    for scene in scenes:
        if scene.type == "intro" and getattr(scene, "bgm_path", None):
            candidate = Path(scene.bgm_path)
            if candidate.exists():
                bgm_path = candidate
                logger.info("[layout:scenes] intro bgm_path 적용: %s", bgm_path.name)
            else:
                logger.warning(
                    "[layout:scenes] intro bgm_path 파일 없음: %s — 기존 BGM 방식 fallback",
                    scene.bgm_path,
                )
            break

    return _render_pipeline(
        post.id, post.title or "", sentences, plan, images,
        output_path, layout, voice, rate, sfx_offset, max_slots, font_dir, audio_dir,
        save_tts_cache=save_tts_cache,
        tts_audio_cache=tts_audio_cache,
        bgm_path=bgm_path,
        scenes_list=scenes,
    )
