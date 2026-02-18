"""레이아웃 렌더러 — Figma SVG 기반 4가지 씬 + 이미지:텍스트 배분 알고리즘.

씬 타입:
  intro     - 제목만 (title_only.svg)   → 첫 프레임
  img_text  - 이미지 + 텍스트 (img_text.svg)
  text_only - 텍스트만, 3줄 Clear (text_only.svg)
  outro     - 이미지만 (img_only.svg)   → 이미지가 남을 때 마지막 1프레임

배분 알고리즘:
  ratio = 이미지수 / 본문문장수
  ratio >= 0.8 → img_heavy  : 거의 모든 문장에 이미지 사용
  ratio >= 0.3 → balanced   : 이미지 균등 분배
  ratio <  0.3 → text_heavy : text_only 위주, 앞에서 일부만 img_text
"""
import hashlib
import json
import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont

from config.settings import ASSETS_DIR, MEDIA_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 캔버스 기본 상수 (layout.json에서 오버라이드 가능)
# ---------------------------------------------------------------------------
CANVAS_W = 1080
CANVAS_H = 1920
HEADER_H = 160
HEADER_COLOR = "#4A44FF"

_LAYOUT_CONFIG: dict | None = None


# ---------------------------------------------------------------------------
# 설정 로더
# ---------------------------------------------------------------------------

def _load_layout() -> dict:
    """config/layout.json을 로드한다 (최초 1회 캐시)."""
    global _LAYOUT_CONFIG
    if _LAYOUT_CONFIG is None:
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "layout.json"
        with open(cfg_path, encoding="utf-8") as f:
            _LAYOUT_CONFIG = json.load(f)
    return _LAYOUT_CONFIG


# ---------------------------------------------------------------------------
# 공통 유틸리티
# ---------------------------------------------------------------------------

def _load_font(font_dir: Path, filename: str, size: int) -> ImageFont.FreeTypeFont:
    """폰트 로드 (assets/fonts → 시스템 한글 → PIL 기본 폰트)."""
    font_path = font_dir / filename
    if font_path.exists():
        try:
            return ImageFont.truetype(str(font_path), size)
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
                    return ImageFont.truetype(p, size)
                except Exception:
                    continue
    except Exception:
        pass
    logger.warning("폰트 없음: %s", filename)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _truncate(text: str, max_chars: int) -> str:
    """max_chars 초과 시 (max_chars-2)자 + '..'."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 2] + ".."


def _wrap_korean(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """한글 음절 단위 줄바꿈 (공백 처리 포함).

    공백이 있으면 단어 단위로 우선 분리하고, 단어가 max_width를 초과하면
    음절 단위로 강제 분리한다.
    """
    def _w(t: str) -> float:
        try:
            return font.getlength(t)
        except AttributeError:
            return float(font.getbbox(t)[2])

    lines: list[str] = []
    current = ""
    cur_w = 0.0
    space_w = _w(" ")

    for ch in text:
        ch_w = _w(ch)
        if ch == " ":
            # 공백: 현재 줄 끝에 붙이거나 새 줄 시작
            if cur_w + ch_w <= max_width:
                current += ch
                cur_w += ch_w
            else:
                if current.strip():
                    lines.append(current.rstrip())
                current = ""
                cur_w = 0.0
        else:
            if cur_w + ch_w > max_width:
                if current:
                    lines.append(current.rstrip())
                current = ch
                cur_w = ch_w
            else:
                current += ch
                cur_w += ch_w

    if current.strip():
        lines.append(current.rstrip())

    return lines or [text]


def _get_audio_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


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


def _resolve_codec() -> str:
    from ai_worker.video import _check_nvenc
    return "h264_nvenc" if _check_nvenc() else "libx264"


def _get_encoder_args(codec: str) -> list[str]:
    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "medium", "-cq", "23", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p"]


# ---------------------------------------------------------------------------
# 이미지 유틸리티
# ---------------------------------------------------------------------------

def _load_image(src: str, cache_dir: Path) -> Optional[Image.Image]:
    """URL 또는 로컬 경로에서 이미지 로드. 실패 시 None."""
    if src.startswith("http://") or src.startswith("https://"):
        url_hash = hashlib.md5(src.encode()).hexdigest()[:16]
        cache_path = cache_dir / f"img_{url_hash}.jpg"
        if not cache_path.exists():
            try:
                resp = requests.get(src, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                cache_path.write_bytes(resp.content)
            except Exception as e:
                logger.warning("이미지 다운로드 실패: %s — %s", src, e)
                return None
        try:
            return Image.open(cache_path).convert("RGB")
        except Exception as e:
            logger.warning("이미지 열기 실패: %s — %s", cache_path, e)
            return None
    else:
        try:
            return Image.open(src).convert("RGB")
        except Exception as e:
            logger.warning("로컬 이미지 로드 실패: %s — %s", src, e)
            return None


def _fit_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Cover 모드: 비율 유지 + 중앙 크롭."""
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    top = (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _paste_rounded(
    base: Image.Image, overlay: Image.Image,
    x: int, y: int, radius: int,
) -> Image.Image:
    """둥근 모서리 마스크로 overlay를 base에 붙여넣기."""
    w, h = overlay.size
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    try:
        md.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)
    except AttributeError:
        md.rectangle([(0, 0), (w - 1, h - 1)], fill=255)
    base = base.convert("RGBA")
    ov = overlay.convert("RGBA")
    ov.putalpha(mask)
    base.paste(ov, (x, y), ov)
    return base.convert("RGB")


# ---------------------------------------------------------------------------
# 배경 캔버스 생성
# ---------------------------------------------------------------------------

def _make_canvas(layout: dict) -> Image.Image:
    """흰 배경 + 보라색 헤더 캔버스."""
    g = layout["global"]
    w = layout["canvas"]["width"]
    h = layout["canvas"]["height"]
    hh = g.get("header_height", HEADER_H)
    hc = g.get("header_color", HEADER_COLOR)

    img = Image.new("RGB", (w, h), g.get("background_color", "#FFFFFF"))
    draw = ImageDraw.Draw(img)
    draw.rectangle([(0, 0), (w, hh)], fill=hc)
    return img


# ---------------------------------------------------------------------------
# 텍스트 중앙 정렬 헬퍼
# ---------------------------------------------------------------------------

def _draw_centered_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    y_start: int,
    line_height: int,
    color: str,
    canvas_w: int,
    stroke_color: str = "",
    stroke_width: int = 0,
) -> int:
    """줄 목록을 중앙 정렬로 렌더링하고 마지막 y 좌표를 반환한다."""
    y = y_start
    for line in lines:
        try:
            lw = font.getlength(line)
        except AttributeError:
            lw = font.getbbox(line)[2]
        cx = int((canvas_w - lw) / 2)
        if stroke_color and stroke_width > 0:
            draw.text((cx, y), line, font=font, fill=color,
                      stroke_width=stroke_width, stroke_fill=stroke_color)
        else:
            draw.text((cx, y), line, font=font, fill=color)
        y += line_height
    return y


# ---------------------------------------------------------------------------
# 씬 렌더러
# ---------------------------------------------------------------------------

def _render_intro_frame(
    title: str,
    layout: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """씬 1: intro — 제목만 표시 (title_only.svg)."""
    sc = layout["scenes"]["intro"]
    el = sc["elements"]["title_text"]
    max_chars = sc.get("title_max_chars", 18)

    font = _load_font(font_dir, "NotoSansKR-Bold.ttf", el["font_size"])
    lh = el.get("line_height", int(el["font_size"] * 1.4))

    img = _make_canvas(layout)
    draw = ImageDraw.Draw(img)

    display_title = _truncate(title, max_chars)
    lines = _wrap_korean(display_title, font, el["max_width"])

    # 수직 중앙: y 기준점에서 전체 블록 높이의 절반을 위로
    total_h = len(lines) * lh
    y_start = el["y"] - total_h // 2
    _draw_centered_lines(draw, lines, font, y_start, lh, el["color"], layout["canvas"]["width"])

    img.save(str(out_path), "PNG")
    return out_path


def _render_img_text_frame(
    img_pil: Optional[Image.Image],
    text: str,
    layout: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """씬 2: img_text — 이미지 + 텍스트 (img_text.svg).

    img_pil이 None이면 이미지 영역을 회색 플레이스홀더로 대체한다.
    """
    sc = layout["scenes"]["img_text"]
    ia = sc["elements"]["image_area"]
    ta = sc["elements"]["text_area"]
    max_chars = sc.get("text_max_chars", 12)

    font = _load_font(font_dir, "NotoSansKR-Medium.ttf", ta["font_size"])
    lh = ta.get("line_height", int(ta["font_size"] * 1.4))
    max_lines: int = ta.get("max_lines", 2)

    img = _make_canvas(layout)
    draw = ImageDraw.Draw(img)

    # ── 이미지 영역 ────────────────────────────────────────────
    iw, ih = ia["width"], ia["height"]
    if img_pil is not None:
        fitted = _fit_cover(img_pil, iw, ih)
        img = _paste_rounded(img, fitted, ia["x"], ia["y"], ia.get("border_radius", 0))
        draw = ImageDraw.Draw(img)
    else:
        # 플레이스홀더 (회색 박스)
        try:
            draw.rounded_rectangle(
                [(ia["x"], ia["y"]), (ia["x"] + iw, ia["y"] + ih)],
                radius=ia.get("border_radius", 0), fill="#CCCCCC",
            )
        except AttributeError:
            draw.rectangle([(ia["x"], ia["y"]), (ia["x"] + iw, ia["y"] + ih)], fill="#CCCCCC")

    # ── 텍스트 영역 ────────────────────────────────────────────
    display_text = _truncate(text, max_chars * max_lines)
    lines = _wrap_korean(display_text, font, ta["max_width"])[:max_lines]

    total_h = len(lines) * lh
    y_start = ta["y"] - total_h // 2
    _draw_centered_lines(draw, lines, font, y_start, lh, ta["color"], layout["canvas"]["width"])

    img.save(str(out_path), "PNG")
    return out_path


def _render_text_only_frame(
    text_history: list[dict],   # [{"lines": list[str], "is_new": bool}]
    layout: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """씬 3: text_only — 텍스트 3줄 누적 (text_only.svg).

    text_history의 각 항목은 {"lines": [...], "is_new": bool}.
    """
    sc = layout["scenes"]["text_only"]
    ta = sc["elements"]["text_area"]

    font = _load_font(font_dir, "NotoSansKR-Medium.ttf", ta["font_size"])
    lh = ta.get("line_height", 140)
    new_color = ta.get("color", "#000000")
    prev_color = ta.get("prev_text_color", "#888888")

    img = _make_canvas(layout)
    draw = ImageDraw.Draw(img)

    # 전체 블록 높이 계산 → 수직 중앙 정렬
    all_lines: list[tuple[str, str]] = []   # (line_text, color)
    for entry in text_history:
        color = new_color if entry.get("is_new") else prev_color
        for line in entry["lines"]:
            all_lines.append((line, color))

    total_h = len(all_lines) * lh
    canvas_h = layout["canvas"]["height"]
    y = max(ta["y"], (canvas_h - total_h) // 2)

    cw = layout["canvas"]["width"]
    for line_text, color in all_lines:
        try:
            lw = font.getlength(line_text)
        except AttributeError:
            lw = font.getbbox(line_text)[2]
        cx = int((cw - lw) / 2)
        draw.text((cx, y), line_text, font=font, fill=color)
        y += lh

    img.save(str(out_path), "PNG")
    return out_path


def _render_outro_frame(
    img_pil: Optional[Image.Image],
    overlay_text: str,
    layout: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """씬 4: outro — 이미지만 (img_only.svg), 텍스트 오버레이 선택."""
    sc = layout["scenes"]["outro"]
    ia = sc["elements"]["image_area"]
    ot = sc["elements"]["overlay_text"]

    font = _load_font(font_dir, "NotoSansKR-Regular.ttf", ot["font_size"])
    lh = int(ot["font_size"] * 1.3)

    img = _make_canvas(layout)
    draw = ImageDraw.Draw(img)

    # ── 이미지 ─────────────────────────────────────────────────
    if img_pil is not None:
        fitted = _fit_cover(img_pil, ia["width"], ia["height"])
        img = _paste_rounded(img, fitted, ia["x"], ia["y"], ia.get("border_radius", 0))
        draw = ImageDraw.Draw(img)
    else:
        try:
            draw.rounded_rectangle(
                [(ia["x"], ia["y"]), (ia["x"] + ia["width"], ia["y"] + ia["height"])],
                radius=ia.get("border_radius", 0), fill="#CCCCCC",
            )
        except AttributeError:
            draw.rectangle(
                [(ia["x"], ia["y"]), (ia["x"] + ia["width"], ia["y"] + ia["height"])],
                fill="#CCCCCC",
            )

    # ── 오버레이 텍스트 (선택) ─────────────────────────────────
    if overlay_text:
        lines = _wrap_korean(overlay_text, font, ot["max_width"])
        total_h = len(lines) * lh
        y = ot["y"] - total_h // 2
        _draw_centered_lines(
            draw, lines, font, y, lh,
            ot["color"], layout["canvas"]["width"],
            stroke_color=ot.get("stroke_color", ""),
            stroke_width=ot.get("stroke_width", 0),
        )

    img.save(str(out_path), "PNG")
    return out_path


# ---------------------------------------------------------------------------
# 배분 알고리즘
# ---------------------------------------------------------------------------

def _plan_sequence(
    sentences: list[dict],
    images: list[str],
    layout: dict,
) -> list[dict]:
    """이미지:텍스트 비율에 따라 씬 유형을 결정한다.

    Args:
        sentences: [{"text": str, "section": str}, ...] hook + body + closer
        images:    이미지 URL / 경로 목록

    Returns:
        plan: [
            {"type": "intro",     "sent_idx": 0,    "img_idx": None},
            {"type": "img_text",  "sent_idx": 1,    "img_idx": 0},
            {"type": "text_only", "sent_idx": 2,    "img_idx": None},
            {"type": "outro",     "sent_idx": None, "img_idx": 3},
            ...
        ]
    """
    alg = layout.get("layout_algorithm", {})
    heavy_thr = alg.get("img_heavy_threshold", 0.8)
    mixed_thr = alg.get("img_mixed_threshold", 0.3)

    n_imgs = len(images)
    plan: list[dict] = []

    # ── intro: 첫 문장 (hook) ─────────────────────────────────
    if not sentences:
        return plan
    plan.append({"type": "intro", "sent_idx": 0, "img_idx": None})

    body_sents = sentences[1:]   # body + closer
    n_body = len(body_sents)
    img_idx = 0

    if n_body == 0:
        if img_idx < n_imgs:
            plan.append({"type": "outro", "sent_idx": None, "img_idx": img_idx})
        return plan

    ratio = n_imgs / n_body

    # ── 이미지 슬롯 결정 ──────────────────────────────────────
    if ratio >= heavy_thr:
        # 이미지 풍부: 순서대로 모든 문장에 이미지 배정
        img_slots = set(range(n_body))
    elif ratio >= mixed_thr:
        # 중간: 균등 분배
        if n_imgs > 0:
            step = n_body / n_imgs
            img_slots = {min(int(k * step), n_body - 1) for k in range(n_imgs)}
        else:
            img_slots = set()
    else:
        # 이미지 부족: 앞 25%만 img_text
        n_use = min(n_imgs, max(1, n_body // 4)) if n_imgs > 0 else 0
        img_slots = set(range(n_use))

    # ── body / closer 씬 결정 ─────────────────────────────────
    for local_i, _ in enumerate(body_sents):
        sent_idx = local_i + 1   # sentences 전체에서의 인덱스
        if local_i in img_slots and img_idx < n_imgs:
            plan.append({"type": "img_text", "sent_idx": sent_idx, "img_idx": img_idx})
            img_idx += 1
        else:
            plan.append({"type": "text_only", "sent_idx": sent_idx, "img_idx": None})

    # ── outro: 남은 이미지가 있으면 1프레임 ──────────────────
    if img_idx < n_imgs:
        plan.append({"type": "outro", "sent_idx": None, "img_idx": img_idx})

    return plan


# ---------------------------------------------------------------------------
# TTS 유틸리티 (ssul_renderer와 동일 로직, 독립 구현)
# ---------------------------------------------------------------------------

async def _tts_chunk_async(
    text: str,
    idx: int,
    output_dir: Path,
    voice: str,
    rate: str,
) -> float:
    """문장 TTS mp3 생성 + 앞부분 묵음 제거."""
    import asyncio, edge_tts

    out_path = output_dir / f"chunk_{idx:03d}.mp3"
    if not text or not text.strip():
        return 0.0

    for attempt in range(2):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(out_path))
            break
        except Exception:
            if attempt == 0:
                logger.warning("TTS 청크 %d 실패 — 재시도", idx, exc_info=True)
                await asyncio.sleep(0.5)
            else:
                logger.error("TTS 청크 %d 최종 실패", idx)
                return 0.0

    trimmed = out_path.with_name(f"{out_path.stem}_trim.mp3")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", str(out_path),
             "-af", "silenceremove=start_periods=1:start_threshold=-50dB:start_duration=0.1",
             "-c:a", "libmp3lame", "-q:a", "2", str(trimmed)],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0 and trimmed.exists() and trimmed.stat().st_size > 0:
            trimmed.replace(out_path)
        else:
            trimmed.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("묵음 제거 실패 (원본 사용): %s", e)
        trimmed.unlink(missing_ok=True)

    return _get_audio_duration(out_path)


async def _generate_tts_chunks(
    plan: list[dict],
    sentences: list[dict],
    output_dir: Path,
    voice: str,
    rate: str,
    outro_duration: float = 1.5,
) -> list[float]:
    """plan 순서대로 TTS를 생성하고 각 프레임의 지속 시간을 반환한다.

    outro(sent_idx=None)는 silence로 처리한다.
    """
    import asyncio
    durations: list[float] = []
    for frame_idx, entry in enumerate(plan):
        sent_idx = entry.get("sent_idx")
        if sent_idx is not None and sent_idx < len(sentences):
            text = sentences[sent_idx]["text"]
            dur = await _tts_chunk_async(text, frame_idx, output_dir, voice, rate)
        else:
            # outro → 짧은 묵음 생성
            out_path = output_dir / f"chunk_{frame_idx:03d}.mp3"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
                 "-t", str(outro_duration), "-c:a", "libmp3lame", "-b:a", "64k", str(out_path)],
                capture_output=True, check=True,
            )
            dur = outro_duration
        durations.append(dur)
        logger.debug("[layout] TTS 프레임 %d: %.2fs", frame_idx, dur)
    return durations


def _merge_chunks(chunk_paths: list[Path], output_path: Path) -> None:
    valid = [c for c in chunk_paths if c.exists() and c.stat().st_size > 0]
    if not valid:
        raise RuntimeError("유효한 TTS 청크 없음")
    concat_file = output_path.parent / "tts_concat.txt"
    concat_file.write_text("".join(f"file '{c.resolve()}'\n" for c in valid), encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", str(concat_file), "-c", "copy", str(output_path)],
        capture_output=True, check=True,
    )
    concat_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# SFX 필터
# ---------------------------------------------------------------------------

def _build_layout_sfx_filter(
    plan: list[dict],
    timings: list[float],
    audio_dir: Path,
    layout: dict,
    tts_input_idx: int = 1,
    sfx_offset: float = -0.15,
) -> tuple[list[str], str]:
    """plan 씬 타입에 따른 효과음 amix 필터 구성."""
    sfx_map: dict[str, str] = layout.get("layout_algorithm", {}).get("sfx", {
        "intro": "click.mp3",
        "img_text": "shutter.mp3",
        "text_only": "pop.mp3",
        "outro": "ding.mp3",
    })
    vol_map = {"click.mp3": 0.6, "shutter.mp3": 0.5, "pop.mp3": 0.45, "ding.mp3": 0.4}

    extra_inputs: list[str] = []
    filter_parts: list[str] = []
    sfx_labels: list[str] = []
    current_idx = tts_input_idx + 1

    for i, (entry, t_start) in enumerate(zip(plan, timings)):
        sfx_file = sfx_map.get(entry["type"], "pop.mp3")
        sfx_path = audio_dir / sfx_file
        if not sfx_path.exists():
            continue
        vol = vol_map.get(sfx_file, 0.4)
        delay_ms = max(0, int((t_start + sfx_offset) * 1000))
        label = f"sfx{i}"
        extra_inputs += ["-i", str(sfx_path)]
        filter_parts.append(f"[{current_idx}:a]adelay={delay_ms}|{delay_ms},volume={vol}[{label}]")
        sfx_labels.append(f"[{label}]")
        current_idx += 1

    tts_ref = f"[{tts_input_idx}:a]"
    if sfx_labels:
        all_refs = tts_ref + "".join(sfx_labels)
        n = 1 + len(sfx_labels)
        filter_str = ";".join(filter_parts) + f";{all_refs}amix=inputs={n}:normalize=0[aout]"
    else:
        filter_str = f"{tts_ref}acopy[aout]"

    return extra_inputs, filter_str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_layout_video(post, script, output_path: Path | None = None) -> Path:
    """레이아웃 기반 쇼츠 영상 렌더링.

    Args:
        post:        Post 객체 (post.id, post.title, post.images 사용)
        script:      ScriptData 객체 (hook / body / closer)
        output_path: 최종 mp4 저장 경로

    Returns:
        생성된 mp4 파일 경로
    """
    from config import settings as s

    layout = _load_layout()
    voice: str = getattr(s, "SSUL_TTS_VOICE", "ko-KR-SunHiNeural")
    rate: str = getattr(s, "SSUL_TTS_RATE", "+25%")
    sfx_offset: float = getattr(s, "SSUL_SFX_OFFSET", -0.15)
    font_dir: Path = ASSETS_DIR / "fonts"
    audio_dir: Path = getattr(s, "SSUL_AUDIO_DIR", ASSETS_DIR / "audio")

    video_dir = MEDIA_DIR / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        output_path = video_dir / f"post_{post.id}.mp4"

    tmp_dir = MEDIA_DIR / "tmp" / f"layout_{post.id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 1: 문장 구조화 ────────────────────────────────
        sentences: list[dict] = []
        sentences.append({"text": script.hook, "section": "hook"})
        for body_text in script.body:
            is_quote = any(q in body_text for q in ('"', "'", "\u2018", "\u2019", "\u201c", "\u201d"))
            sentences.append({"text": body_text, "section": "comment" if is_quote else "body"})
        sentences.append({"text": script.closer, "section": "closer"})

        images: list[str] = post.images if isinstance(post.images, list) else []
        logger.info("[layout] post_id=%d 문장=%d 이미지=%d", post.id, len(sentences), len(images))

        # ── Step 2: 씬 배분 계획 ───────────────────────────────
        plan = _plan_sequence(sentences, images, layout)
        logger.info("[layout] 씬 계획: %s", [p["type"] for p in plan])

        # ── Step 3: 이미지 사전 다운로드 ──────────────────────
        img_cache: dict[int, Optional[Image.Image]] = {}
        for entry in plan:
            img_idx = entry.get("img_idx")
            if img_idx is not None and img_idx not in img_cache:
                url = images[img_idx] if img_idx < len(images) else None
                img_cache[img_idx] = _load_image(url, tmp_dir) if url else None

        # ── Step 4: TTS 생성 ───────────────────────────────────
        logger.info("[layout] TTS 생성 시작")
        t0 = time.time()
        durations: list[float] = _run_async(  # type: ignore[assignment]
            _generate_tts_chunks(plan, sentences, tmp_dir, voice, rate)
        )
        total_dur = sum(durations)
        logger.info("[layout] TTS 완료: %d프레임, 총 %.1fs (%.2fs)", len(durations), total_dur, time.time() - t0)

        # ── Step 5: TTS concat ─────────────────────────────────
        chunk_paths = [tmp_dir / f"chunk_{i:03d}.mp3" for i in range(len(plan))]
        merged_tts = tmp_dir / "merged_tts.mp3"
        _merge_chunks(chunk_paths, merged_tts)

        # ── Step 6: 줄바꿈 사전 계산 (text_only용) ─────────────
        sc_to = layout["scenes"]["text_only"]
        to_ta = sc_to["elements"]["text_area"]
        to_font = _load_font(font_dir, "NotoSansKR-Medium.ttf", to_ta["font_size"])
        to_max_w = to_ta["max_width"]
        to_max_lines = to_ta.get("max_total_lines", 3)

        for sent in sentences:
            sent["lines"] = _wrap_korean(sent["text"], to_font, to_max_w)

        # ── Step 7: PIL 프레임 생성 ────────────────────────────
        logger.info("[layout] 프레임 생성 시작")
        t1 = time.time()
        frame_paths: list[Path] = []
        title = (post.title or "")

        text_only_history: list[dict] = []  # text_only 누적 히스토리

        for frame_idx, entry in enumerate(plan):
            scene_type = entry["type"]
            sent_idx = entry.get("sent_idx")
            img_idx = entry.get("img_idx")

            frame_path = tmp_dir / f"frame_{frame_idx:03d}.png"

            # text_only가 아닌 씬이 등장하면 히스토리 리셋
            if scene_type != "text_only":
                text_only_history = []

            if scene_type == "intro":
                _render_intro_frame(title, layout, font_dir, frame_path)

            elif scene_type == "img_text":
                img_pil = img_cache.get(img_idx) if img_idx is not None else None
                text = sentences[sent_idx]["text"] if sent_idx is not None else ""
                _render_img_text_frame(img_pil, text, layout, font_dir, frame_path)

            elif scene_type == "text_only":
                # 이전 항목 흐리게 처리
                for prev in text_only_history:
                    prev["is_new"] = False

                new_lines = sentences[sent_idx]["lines"] if sent_idx is not None else []

                # 3줄 초과 시 Clear
                cur_total = sum(len(e["lines"]) for e in text_only_history)
                if text_only_history and cur_total + len(new_lines) > to_max_lines:
                    if len(new_lines) > to_max_lines:
                        logger.warning("[layout] 문장 %d: %d줄 초과 — 단독 표시", frame_idx, len(new_lines))
                    text_only_history = []

                text_only_history.append({"lines": new_lines, "is_new": True})
                _render_text_only_frame(text_only_history, layout, font_dir, frame_path)

            elif scene_type == "outro":
                img_pil = img_cache.get(img_idx) if img_idx is not None else None
                _render_outro_frame(img_pil, "", layout, font_dir, frame_path)

            frame_paths.append(frame_path)

        logger.info("[layout] 프레임 %d장 완료 (%.2fs)", len(frame_paths), time.time() - t1)

        # ── Step 8: concat_list.txt ────────────────────────────
        concat_file = tmp_dir / "concat_list.txt"
        lines_txt: list[str] = []
        for fp, dur in zip(frame_paths, durations):
            lines_txt.append(f"file '{fp.resolve()}'\n")
            lines_txt.append(f"duration {dur:.4f}\n")
        if frame_paths:
            lines_txt.append(f"file '{frame_paths[-1].resolve()}'\n")
        concat_file.write_text("".join(lines_txt), encoding="utf-8")

        # ── Step 9: 타임스탬프 ─────────────────────────────────
        timings: list[float] = []
        acc = 0.0
        for dur in durations:
            timings.append(acc)
            acc += dur

        # ── Step 10: SFX 필터 ──────────────────────────────────
        extra_inputs, sfx_filter = _build_layout_sfx_filter(
            plan, timings, audio_dir, layout,
            tts_input_idx=1, sfx_offset=sfx_offset,
        )

        # ── Step 11: FFmpeg 인코딩 ─────────────────────────────
        codec = _resolve_codec()
        enc_args = _get_encoder_args(codec)

        # 1080×1920 직접 렌더링이므로 리사이즈 불필요 (원본 비율 유지)
        video_filter = "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2[vout]"
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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error("[layout] FFmpeg 실패 (returncode=%d):\n%s",
                         result.returncode, result.stderr[-3000:])
            raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

        logger.info("[layout] 완료: %s (총 %.1fs)", output_path.name, total_dur)
        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
