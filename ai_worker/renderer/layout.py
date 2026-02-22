"""레이아웃 렌더러 v2 — 베이스 프레임 베이킹 + 고정 Y좌표 슬롯.

씬 타입:
  intro     - 제목만 (title_only.svg)   → 베이스 프레임 그대로 사용
  img_text  - 이미지 + 텍스트 (img_text.svg)
  text_only - 텍스트만, 3슬롯 고정 Y (text_only.svg)
  outro     - 이미지만 (img_only.svg)   → 이미지가 남을 때 마지막 1프레임

핵심 설계:
  1. _create_base_frame() — base_layout.png + 제목을 헤더에 1회 합성
  2. 모든 씬 렌더러가 base_frame.copy()에서 시작 → 제목 위치 완전 고정
  3. text_only는 y_coords[] 배열로 슬롯별 Y좌표 명시 (동적 계산 없음)

배분 알고리즘:
  ratio = 이미지수 / 본문문장수
  ratio >= 0.8 → img_heavy : 거의 모든 문장에 이미지 사용
  ratio >= 0.3 → balanced  : 이미지 균등 분배
  ratio <  0.3 → text_heavy: text_only 위주, 앞에서 일부만 img_text
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

# 캔버스 기본 상수 (layout.json 미로드 시 fallback)
CANVAS_W = 1080
CANVAS_H = 1920
HEADER_H = 160
HEADER_COLOR = "#4A44FF"

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
        return  # Regular: 기본값 유지
    try:
        font.set_variation_by_name(weight_name)
    except Exception:
        pass


def _load_font(font_dir: Path, filename: str, size: int) -> ImageFont.FreeTypeFont:
    """폰트 로드 (assets/fonts → 시스템 한글 → PIL 기본 폰트).

    Variable Font(VF)인 경우 파일명에서 Bold/Medium/Light를 감지해 굵기 축을 설정한다.
    """
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


def _truncate(text: str, max_chars: int) -> str:
    """max_chars 초과 시 (max_chars-2)자 + '..'."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 2] + ".."


def _wrap_korean(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """한글 음절 단위 줄바꿈 (공백 우선, 초과 시 강제 분리)."""
    def _w(t: str) -> float:
        try:
            return font.getlength(t)
        except AttributeError:
            return float(font.getbbox(t)[2])

    lines: list[str] = []
    current = ""
    cur_w = 0.0

    for ch in text:
        ch_w = _w(ch)
        if ch == " ":
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


def _draw_centered_text(
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
    """줄 목록을 중앙 정렬로 그리고, 마지막 줄 아래 y를 반환한다."""
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
    from ai_worker.renderer.video import _check_nvenc
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
                _hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                if "dcinside.com" in src:
                    _hdrs["Referer"] = "https://gall.dcinside.com/"
                resp = requests.get(src, timeout=10, headers=_hdrs)
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
# 베이스 프레임 베이킹
# ---------------------------------------------------------------------------

def _create_base_frame(
    layout: dict,
    title: str,
    font_dir: Path,
    assets_dir: Path,
) -> Image.Image:
    """영상 전용 베이스 프레임을 생성한다.

    base_layout.png 위에 헤더 고정 위치의 제목을 1회 합성한다.
    모든 씬 렌더러가 이 프레임을 .copy()해서 사용하므로
    제목이 어떤 씬에서도 동일한 위치에 출력된다.
    """
    g = layout["global"]
    cw = layout["canvas"]["width"]
    ch = layout["canvas"]["height"]

    # 1. 배경 템플릿 로드
    _proj_root = Path(__file__).resolve().parent.parent.parent
    tpl_path = _proj_root / g.get("base_layout", "assets/backgrounds/base_layout.png")
    if tpl_path.exists():
        base = Image.open(tpl_path).convert("RGB")
        # 캔버스 크기와 다를 경우 리사이즈
        if base.size != (cw, ch):
            base = base.resize((cw, ch), Image.LANCZOS)
    else:
        logger.warning("base_template 없음: %s — 흰 배경 fallback", tpl_path)
        base = Image.new("RGB", (cw, ch), g.get("background_color", "#FFFFFF"))
        draw_bg = ImageDraw.Draw(base)
        draw_bg.rectangle(
            [(0, 0), (cw, g.get("header_height", HEADER_H))],
            fill=g.get("header_color", HEADER_COLOR),
        )

    # 2. 헤더 고정 위치에 제목 렌더링
    ht = g.get("header_title")
    if ht:
        font = _load_font(font_dir, "NotoSansKR-Bold.ttf", ht.get("font_size", 52))
        max_chars = ht.get("max_chars", 22)
        display = title[:max_chars] + "..." if len(title) > max_chars else title
        lines = _wrap_korean(display, font, ht.get("max_width", 860))
        lh = int(ht.get("font_size", 52) * 1.3)
        draw = ImageDraw.Draw(base)

        x_pos = ht.get("x", 50)  # layout.json의 "x": 50 값을 가져옴
        y_curr = ht.get("y", 200)
        color = ht.get("color", "#000000")

        for line in lines:
            draw.text((x_pos, y_curr), line, font=font, fill=color)
            y_curr += lh

    return base


# ---------------------------------------------------------------------------
# 씬 렌더러 (모두 base_frame.copy()에서 시작)
# ---------------------------------------------------------------------------

def _render_intro_frame(
    base_frame: Image.Image,
    out_path: Path,
) -> Path:
    """씬 1: intro — 베이스 프레임 그대로 저장 (제목은 헤더에 이미 있음)."""
    base_frame.copy().save(str(out_path), "PNG")
    return out_path


def _render_img_text_frame(
    base_frame: Image.Image,
    img_pil: Optional[Image.Image],
    text: str,
    layout: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """씬 2: img_text — 베이스 프레임 + 이미지(900×900) + 하단 텍스트."""
    sc = layout["scenes"]["img_text"]
    ia = sc["elements"]["image_area"]
    ta = sc["elements"]["text_area"]
    max_chars = sc.get("text_max_chars", 12)
    max_lines: int = ta.get("max_lines", 2)

    font = _load_font(font_dir, "NotoSansKR-Medium.ttf", ta["font_size"])
    lh = ta.get("line_height", int(ta["font_size"] * 1.4))

    img = base_frame.copy()
    draw = ImageDraw.Draw(img)

    # ── 이미지 영역 ───────────────────────────────────────────
    iw, ih = ia["width"], ia["height"]
    if img_pil is not None:
        fitted = _fit_cover(img_pil, iw, ih)
        img = _paste_rounded(img, fitted, ia["x"], ia["y"], ia.get("border_radius", 0))
        draw = ImageDraw.Draw(img)
    else:
        try:
            draw.rounded_rectangle(
                [(ia["x"], ia["y"]), (ia["x"] + iw, ia["y"] + ih)],
                radius=ia.get("border_radius", 0), fill="#CCCCCC",
            )
        except AttributeError:
            draw.rectangle([(ia["x"], ia["y"]), (ia["x"] + iw, ia["y"] + ih)], fill="#CCCCCC")

    # ── 텍스트 영역 ───────────────────────────────────────────
    lines = _wrap_korean(text, font, ta["max_width"])
    total_h = len(lines) * lh
    y_start = ta["y"] - total_h // 2
    _draw_centered_text(draw, lines, font, y_start, lh, ta["color"], layout["canvas"]["width"])

    img.save(str(out_path), "PNG")
    return out_path


def _render_text_only_frame(
    base_frame: Image.Image,
    text_history: list[dict],   # [{"lines": list[str], "is_new": bool, "block_type": str, "author": str|None}]
    layout: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """씬 3: text_only — 동적 Y좌표로 텍스트 배치.

    슬롯 간격은 len(lines) × (line_height + slot_gap)으로 동적 계산.
    1줄 슬롯: 150px 전진, 2줄 슬롯: 300px 전진.
    comment 타입은 닉네임 + 다른 색상으로 시각 구분.
    """
    sc = layout["scenes"]["text_only"]
    ta = sc["elements"]["text_area"]

    font = _load_font(font_dir, "NotoSansKR-Medium.ttf", ta["font_size"])
    author_font = _load_font(font_dir, "NotoSansKR-Regular.ttf", max(int(ta["font_size"] * 0.55), 20))
    lh: int = ta.get("line_height", 120)
    slot_gap: int = ta.get("slot_gap", 5)
    new_color: str = ta.get("color", "#000000")
    prev_color: str = ta.get("prev_text_color", "#888888")
    comment_color: str = "#00BCD4"       # 댓글 본문: 청록색
    comment_prev_color: str = "#5E9EA0"  # 댓글 이전: 흐린 청록
    author_color: str = "#9E9E9E"        # 닉네임: 회색
    y_start: int = ta.get("y_coords", [550])[0]
    cw = layout["canvas"]["width"]

    img = base_frame.copy()
    draw = ImageDraw.Draw(img)

    current_y = y_start
    for entry in text_history:
        is_new = entry.get("is_new", False)
        block_type = entry.get("block_type", "body")
        author = entry.get("author")
        slot_y = current_y

        # comment 타입: 닉네임을 먼저 표시
        if block_type == "comment" and author:
            author_text = f"@{author}"
            try:
                aw = author_font.getlength(author_text)
            except AttributeError:
                aw = author_font.getbbox(author_text)[2]
            ax = int((cw - aw) / 2)
            draw.text((ax, slot_y), author_text, font=author_font, fill=author_color)
            slot_y += int(lh * 0.55)

        # 본문/댓글 텍스트 색상 결정
        if block_type == "comment":
            color = comment_color if is_new else comment_prev_color
        else:
            color = new_color if is_new else prev_color

        for line_text in entry["lines"]:
            try:
                lw = font.getlength(line_text)
            except AttributeError:
                lw = font.getbbox(line_text)[2]
            cx = int((cw - lw) / 2)
            draw.text((cx, slot_y), line_text, font=font, fill=color)
            slot_y += lh

        # 다음 슬롯 시작 y: 줄 수 × (line_height + slot_gap) + 닉네임 여분
        author_extra = int(lh * 0.55) if (block_type == "comment" and author) else 0
        current_y += len(entry["lines"]) * (lh + slot_gap) + author_extra

    img.save(str(out_path), "PNG")
    return out_path


def _render_img_only_frame(
    base_frame: Image.Image,
    img_pil: Optional[Image.Image],
    layout: dict,
    out_path: Path,
) -> Path:
    """씬 img_only — 베이스 프레임 + 이미지 전체 화면 cover 렌더링. 텍스트 없음."""
    sc = layout["scenes"]["img_only"]
    ia = sc["elements"]["image_area"]

    img = base_frame.copy()
    draw = ImageDraw.Draw(img)

    if img_pil is not None:
        fitted = _fit_cover(img_pil, ia["width"], ia["height"])
        img = _paste_rounded(img, fitted, ia["x"], ia["y"], ia.get("border_radius", 0))
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

    img.save(str(out_path), "PNG")
    return out_path


def _render_outro_frame(
    base_frame: Image.Image,
    img_pil: Optional[Image.Image],
    overlay_text: str,
    layout: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """씬 4: outro — 베이스 프레임 + 대형 이미지 + 선택적 오버레이."""
    sc = layout["scenes"]["outro"]
    ia = sc["elements"]["image_area"]
    ot = sc["elements"]["overlay_text"]

    font = _load_font(font_dir, "NotoSansKR-Regular.ttf", ot["font_size"])
    lh = int(ot["font_size"] * 1.3)
    cw = layout["canvas"]["width"]

    img = base_frame.copy()
    draw = ImageDraw.Draw(img)

    # ── 이미지 ────────────────────────────────────────────────
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

    # ── 오버레이 텍스트 (선택) ────────────────────────────────
    if overlay_text:
        lines = _wrap_korean(overlay_text, font, ot["max_width"])
        total_h = len(lines) * lh
        y_start = ot["y"] - total_h // 2
        _draw_centered_text(
            draw, lines, font, y_start, lh,
            ot["color"], cw,
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

    Returns:
        [{"type": scene_type, "sent_idx": int|None, "img_idx": int|None}, ...]
    """
    alg = layout.get("layout_algorithm", {})
    heavy_thr = alg.get("img_heavy_threshold", 0.8)
    mixed_thr = alg.get("img_mixed_threshold", 0.3)

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
            plan.append({"type": "img_text", "sent_idx": sent_idx, "img_idx": img_idx})
            img_idx += 1
        else:
            plan.append({"type": "text_only", "sent_idx": sent_idx, "img_idx": None})

    if img_idx < n_imgs:
        plan.append({"type": "outro", "sent_idx": None, "img_idx": img_idx})

    return plan


# ---------------------------------------------------------------------------
# TTS 유틸리티
# ---------------------------------------------------------------------------

_INTRO_PAUSE_SEC: float = 0.5  # 제목 읽기 후 본문 시작 전 숨고르기 (초)


async def _tts_chunk_async(
    text: str,
    idx: int,
    output_dir: Path,
    scene_type: str = "img_text",
    pre_audio: str | None = None,
    voice_key: str = "default",
) -> float:
    """문장 TTS 생성. pre_audio가 유효하면 재사용, 없으면 Fish Speech 호출.

    Args:
        text:       읽을 텍스트
        idx:        프레임 인덱스 (출력 파일명 결정)
        output_dir: TTS 청크 저장 디렉터리
        scene_type: 씬 타입 (Fish Speech 감정 태그 결정)
        pre_audio:  content_processor에서 사전 생성된 오디오 경로 (있으면 복사)
        voice_key:  VOICE_PRESETS 키 (pipeline.json의 tts_voice)
    """
    import asyncio
    import shutil
    from ai_worker.tts.fish_client import synthesize as fish_synthesize

    out_path = output_dir / f"chunk_{idx:03d}.wav"
    if not text or not text.strip():
        return 0.0

    # 사전 생성된 오디오 재사용
    if pre_audio:
        pre_path = Path(pre_audio)
        if pre_path.exists() and pre_path.stat().st_size > 0:
            shutil.copy2(pre_path, out_path)
            logger.debug("[layout] TTS 재사용: 프레임=%d %s", idx, pre_path.name)
            return _get_audio_duration(out_path)

    # Fish Speech 신규 생성
    for attempt in range(2):
        try:
            await fish_synthesize(text=text, scene_type=scene_type, voice_key=voice_key, output_path=out_path)
            break
        except Exception:
            if attempt == 0:
                logger.warning("[layout] TTS 청크 %d 실패 — 5초 후 재시도", idx, exc_info=True)
                await asyncio.sleep(5.0)  # Fish Speech 부하 완화 대기
            else:
                logger.error("[layout] TTS 청크 %d 최종 실패", idx)
                return 0.0

    if not out_path.exists() or out_path.stat().st_size == 0:
        return 0.0
    return _get_audio_duration(out_path)


async def _generate_tts_chunks(
    plan: list[dict],
    sentences: list[dict],
    output_dir: Path,
    voice: str,
    rate: str,
    outro_duration: float = 1.5,
) -> list[float]:
    """plan 순서로 TTS를 생성하고 각 프레임의 지속 시간 목록을 반환한다.

    sentences[sent_idx]에 "audio" 키가 있으면 사전 생성 오디오 재사용.
    없으면 Fish Speech로 신규 생성.
    """
    import asyncio
    durations: list[float] = []
    for frame_idx, entry in enumerate(plan):
        sent_idx = entry.get("sent_idx")
        if sent_idx is not None and sent_idx < len(sentences):
            sent = sentences[sent_idx]
            text = sent["text"]
            pre_audio = sent.get("audio")          # 사전 생성 경로 (없으면 None)
            scene_type = entry.get("type", "img_text")
            chunk_voice = sent.get("voice_override") or voice
            dur = await _tts_chunk_async(text, frame_idx, output_dir, scene_type, pre_audio, chunk_voice)

            # 제목(intro) 읽기 후 본문 시작 전 숨고르기 삽입
            if scene_type == "intro" and dur > 0:
                chunk_path = output_dir / f"chunk_{frame_idx:03d}.wav"
                tmp_pad = chunk_path.with_suffix(".padded.wav")
                pad_result = subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", str(chunk_path),
                        "-af", f"apad=pad_dur={_INTRO_PAUSE_SEC}",
                        "-c:a", "pcm_s16le", str(tmp_pad),
                    ],
                    capture_output=True,
                )
                if pad_result.returncode == 0 and tmp_pad.exists() and tmp_pad.stat().st_size > 0:
                    tmp_pad.replace(chunk_path)
                    dur += _INTRO_PAUSE_SEC
                    logger.debug(
                        "[layout] intro TTS 뒤 %.1f초 숨고르기 삽입 (프레임=%d)", _INTRO_PAUSE_SEC, frame_idx
                    )
        else:
            out_path = output_dir / f"chunk_{frame_idx:03d}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                 "-t", str(outro_duration), "-c:a", "pcm_s16le", str(out_path)],
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
# SceneDecision 변환 유틸리티
# ---------------------------------------------------------------------------

def _unpack_line(item) -> tuple[str, str | None]:
    """text_lines 요소에서 (text, audio_path)를 추출한다.

    content_processor Phase 5 이후 text_lines 요소가
    str → {"text": str, "audio": str|None} dict로 교체되므로 양쪽 형식 모두 처리.
    """
    if isinstance(item, dict):
        return item.get("text", ""), item.get("audio")
    return str(item), None


def _scenes_to_plan_and_sentences(
    scenes: list,
) -> tuple[list[dict], list[dict], list[str]]:
    """SceneDecision 목록을 내부 렌더러 형식 (sentences, plan, images)으로 변환한다.

    text_only SceneDecision의 여러 text_lines는 각각 별도 plan 엔트리로 분리해
    렌더러의 누적 스태킹 메커니즘과 호환되도록 한다.

    text_lines 요소가 str 또는 dict{"text", "audio"} 모두 처리.
    사전 생성 audio 경로는 sentences의 "audio" 키에 보존되어
    _generate_tts_chunks에서 재사용된다.

    Returns:
        sentences : [{"text": str, "section": str, "audio": str|None}, ...]
        plan      : [{"type": str, "sent_idx": int|None, "img_idx": int|None}, ...]
        images    : [url_or_path, ...]  (plan의 img_idx 는 이 리스트 인덱스)
    """
    sentences: list[dict] = []
    plan: list[dict] = []
    images: list[str] = []

    for scene in scenes:
        img_idx: Optional[int] = None
        if scene.image_url:
            img_idx = len(images)
            images.append(scene.image_url)

        if scene.type == "intro":
            text, audio = _unpack_line(scene.text_lines[0]) if scene.text_lines else ("", None)
            sent_idx = len(sentences)
            sentences.append({"text": text, "section": "hook", "audio": audio, "voice_override": None})
            plan.append({"type": "intro", "sent_idx": sent_idx, "img_idx": img_idx})

        elif scene.type == "img_text":
            text, audio = _unpack_line(scene.text_lines[0]) if scene.text_lines else ("", None)
            sent_idx = len(sentences)
            sentences.append({
                "text": text, "section": "body", "audio": audio,
                "voice_override": scene.voice_override,
                "block_type": getattr(scene, "block_type", "body"),
                "author": getattr(scene, "author", None),
            })
            plan.append({"type": "img_text", "sent_idx": sent_idx, "img_idx": img_idx})

        elif scene.type == "text_only":
            # 여러 줄 → 각각 별도 plan 엔트리 → 렌더러가 누적 스태킹
            for line in scene.text_lines:
                text, audio = _unpack_line(line)
                sent_idx = len(sentences)
                sentences.append({
                    "text": text, "section": "body", "audio": audio,
                    "voice_override": scene.voice_override,
                    "block_type": getattr(scene, "block_type", "body"),
                    "author": getattr(scene, "author", None),
                })
                plan.append({"type": "text_only", "sent_idx": sent_idx, "img_idx": None})

        elif scene.type == "img_only":
            text, audio = _unpack_line(scene.text_lines[0]) if scene.text_lines else ("", None)
            sent_idx: Optional[int] = None
            if text:
                sent_idx = len(sentences)
                sentences.append({"text": text, "section": "body", "audio": audio, "voice_override": scene.voice_override})
            plan.append({"type": "img_only", "sent_idx": sent_idx, "img_idx": img_idx})

        elif scene.type == "outro":
            text, audio = _unpack_line(scene.text_lines[0]) if scene.text_lines else ("", None)
            sent_idx: Optional[int] = None
            if text:
                sent_idx = len(sentences)
                sentences.append({"text": text, "section": "closer", "audio": audio, "voice_override": None})
            plan.append({"type": "outro", "sent_idx": sent_idx, "img_idx": img_idx})

    return sentences, plan, images


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
) -> Path:
    """sentences / plan / images 를 받아 mp4를 생성한다.

    Steps 2, 4–11 을 담당. _plan_sequence 단계는 호출자가 수행한다.
    bgm_path: 우선 사용할 BGM 파일 경로. 없거나 파일이 존재하지 않으면 무시.
    """
    tmp_dir = MEDIA_DIR / "tmp" / f"layout_{post_id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 2: 베이스 프레임 베이킹 ──────────────────────
        base_frame = _create_base_frame(layout, title, font_dir, ASSETS_DIR)
        logger.info("[layout] 베이스 프레임 생성 완료 (제목 헤더 고정)")

        # ── Step 4: 이미지 사전 다운로드 ──────────────────────
        img_cache: dict[int, Optional[Image.Image]] = {}
        for entry in plan:
            img_idx = entry.get("img_idx")
            if img_idx is not None and img_idx not in img_cache:
                url = images[img_idx] if img_idx < len(images) else None
                img_cache[img_idx] = _load_image(url, tmp_dir) if url else None

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
            # Fish Speech 재웜업 — LLM 처리 시간(수 분) 동안 모델이 언로드됐을 수 있음
            from ai_worker.tts.fish_client import _warmup_model
            _run_async(_warmup_model())
            t0 = time.time()
            durations = _run_async(  # type: ignore[assignment]
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
                continue  # LLM에서 미리 분리된 경우 재계산 스킵
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

            elif scene_type == "img_text":
                img_pil = img_cache.get(img_idx) if img_idx is not None else None
                text = sentences[sent_idx]["text"] if sent_idx is not None else ""
                _render_img_text_frame(base_frame, img_pil, text, layout, font_dir, frame_path)

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

            elif scene_type == "img_only":
                img_pil = img_cache.get(img_idx) if img_idx is not None else None
                _render_img_only_frame(base_frame, img_pil, layout, frame_path)

            elif scene_type == "outro":
                img_pil = img_cache.get(img_idx) if img_idx is not None else None
                _render_outro_frame(base_frame, img_pil, "", layout, font_dir, frame_path)

            frame_paths.append(frame_path)

        logger.info("[layout] 프레임 %d장 완료 (%.2fs)", len(frame_paths), time.time() - t1)

        # ── Step 9: concat_list.txt ────────────────────────────
        concat_file = tmp_dir / "concat_list.txt"
        concat_lines: list[str] = []
        for fp, dur in zip(frame_paths, durations):
            concat_lines.append(f"file '{fp.resolve()}'\n")
            concat_lines.append(f"duration {dur:.4f}\n")
        if frame_paths:
            concat_lines.append(f"file '{frame_paths[-1].resolve()}'\n")
        concat_file.write_text("".join(concat_lines), encoding="utf-8")

        # ── Step 10: 타임스탬프 + SFX ─────────────────────────
        timings: list[float] = []
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

        # BGM 처리: bgm_path 우선, 없으면 BGM 없이 인코딩
        effective_bgm: Path | None = None
        if bgm_path is not None and Path(bgm_path).exists():
            effective_bgm = Path(bgm_path)
            logger.info("[layout] BGM 사용 (bgm_path): %s", effective_bgm.name)
        elif bgm_path is not None:
            logger.warning("[layout] bgm_path 파일 없음: %s — BGM 없이 인코딩", bgm_path)

        if effective_bgm is not None:
            # SFX 필터는 tts_input_idx=1 기준으로 생성됐으므로 BGM 입력 추가 시 인덱스 재조정 필요
            # BGM을 TTS 뒤(index=2)에 추가하고 SFX는 그 이후로 시프트
            bgm_sfx_extra, bgm_sfx_filter = _build_layout_sfx_filter(
                plan, timings, audio_dir, layout,
                tts_input_idx=1, sfx_offset=sfx_offset,
            )
            # SFX 필터의 tts 참조를 premix_tts로 대체하여 BGM과 함께 믹싱
            # BGM 볼륨: 0.15 (TTS 오디오에 ducking 없이 단순 볼륨 믹싱)
            bgm_audio_filter = (
                f"[1:a]apad[tts_pad];"
                f"[2:a]volume=0.15,aloop=loop=-1:size=2e+09[bgm_loop];"
                f"[tts_pad][bgm_loop]amix=inputs=2:duration=first:normalize=0[aout_premix]"
            )
            # SFX가 있으면 aout_premix에 추가 믹싱, 없으면 그대로 aout으로 사용
            if bgm_sfx_extra:
                # SFX 입력 인덱스를 3부터 시작하도록 sfx_filter 재생성
                bgm_extra_sfx, bgm_sfx_str = _build_layout_sfx_filter(
                    plan, timings, audio_dir, layout,
                    tts_input_idx=1, sfx_offset=sfx_offset,
                )
                # sfx_filter의 [1:a] 참조를 [aout_premix]로 교체하여 BGM 포함 믹싱
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
    """레이아웃 기반 쇼츠 영상 렌더링.

    Args:
        post:        Post 객체 (post.id, post.title, post.images 사용)
        script:      ScriptData 객체 (hook / body / closer)
        output_path: 최종 mp4 저장 경로
    """
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

    # ── Step 1: 문장 구조화 ────────────────────────────────────
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

    # ── Step 3: 씬 배분 계획 (기존 알고리즘) ──────────────────
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
    """SceneDirector 출력(SceneDecision 목록)으로 직접 렌더링.

    _plan_sequence 를 건너뛰고 사전에 계산된 씬 배분을 그대로 사용한다.
    resource_analyzer → scene_director 파이프라인과 함께 사용.

    Args:
        post:        Post 객체 (post.id, post.title 사용)
        scenes:      list[SceneDecision] — content_processor.process_content() 반환값
        output_path: 최종 mp4 저장 경로
    """
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

    # SceneDecision → 내부 렌더러 형식 변환
    sentences, plan, images = _scenes_to_plan_and_sentences(scenes)
    logger.info(
        "[layout:scenes] post_id=%d 씬=%d 문장=%d 이미지=%d",
        post.id, len(scenes), len(sentences), len(images),
    )

    # intro 씬에서 bgm_path 추출 (계약: bgm_path는 intro 씬에만 설정됨)
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
    )
