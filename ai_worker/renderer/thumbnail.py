"""썸네일 생성 모듈

hook_text와 원본 이미지 URL을 이용해 YouTube 썸네일(1280x720)을 생성한다.
style 파라미터로 dramatic / question / funny / news 4종 프리셋 지원.
"""

import logging
import math
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.settings import ASSETS_DIR, MEDIA_DIR

logger = logging.getLogger(__name__)

_THUMB_W = 1280
_THUMB_H = 720
_BASE_FONT_SIZE = 72

# ---------------------------------------------------------------------------
# 스타일 프리셋
# ---------------------------------------------------------------------------
_STYLES: dict[str, dict] = {
    "dramatic": {
        "gradient_color": (160, 0, 0),   # 빨간 그라데이션 오버레이
        "overlay_alpha": 160,
        "text_color": (255, 255, 255),
        "outline_color": (0, 0, 0),
        "text_angle": 45,                # 45° 기울기
        "icon": "⚠",
        "icon_color": (255, 220, 0),
        "use_image": True,
    },
    "question": {
        "gradient_color": (0, 50, 180),  # 파란 그라데이션
        "overlay_alpha": 140,
        "text_color": (255, 230, 0),     # 노란색
        "outline_color": (0, 0, 0),
        "text_angle": 0,
        "icon": "?",
        "icon_color": (255, 255, 255),
        "use_image": True,
    },
    "funny": {
        "gradient_color": (200, 130, 0),  # 밝은 톤
        "overlay_alpha": 90,
        "text_color": (255, 80, 0),
        "outline_color": (30, 30, 30),
        "text_angle": 0,
        "icon": ":D",
        "icon_color": (255, 255, 100),
        "use_image": True,
    },
    "news": {
        "gradient_color": (10, 30, 80),   # 뉴스 진청색 — 단색 그라데이션
        "overlay_alpha": 255,             # 불투명 (이미지 없음)
        "text_color": (255, 255, 255),
        "outline_color": (0, 0, 80),
        "text_angle": 0,
        "icon": None,
        "icon_color": None,
        "use_image": False,
        "show_date": True,
    },
}

_DEFAULT_STYLE = "dramatic"


# ---------------------------------------------------------------------------
# 배경 생성
# ---------------------------------------------------------------------------
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

_SITE_REFERERS: dict[str, str] = {
    "dcinside.com": "https://gall.dcinside.com/",
}


def _image_headers(url: str) -> dict[str, str]:
    """사이트별 이미지 다운로드 헤더 반환. Referer 없으면 hotlink 차단되는 사이트 대응."""
    headers = {"User-Agent": _UA}
    for domain, referer in _SITE_REFERERS.items():
        if domain in url:
            headers["Referer"] = referer
            break
    return headers


def _download_image(url: str, timeout: int = 15) -> Optional[Image.Image]:
    try:
        resp = requests.get(url, timeout=timeout, headers=_image_headers(url))
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as exc:
        logger.warning("이미지 다운로드 실패 (%s): %s", url, exc)
        return None


def _fill_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """이미지를 target 비율로 center-crop 후 리사이즈."""
    src_w, src_h = img.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        img = img.crop((left, 0, left + new_w, src_h))
    else:
        new_h = int(src_w / target_ratio)
        top = (src_h - new_h) // 2
        img = img.crop((0, top, src_w, top + new_h))

    return img.resize((target_w, target_h), Image.LANCZOS)


def _gradient_overlay(color: tuple[int, int, int], alpha_max: int) -> Image.Image:
    """위→투명, 아래→color+alpha_max 그라데이션 RGBA 레이어."""
    layer = Image.new("RGBA", (_THUMB_W, _THUMB_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    r, g, b = color
    for y in range(_THUMB_H):
        ratio = y / _THUMB_H
        a = int(alpha_max * ratio)
        draw.line([(0, y), (_THUMB_W, y)], fill=(r, g, b, a))
    return layer


def _gradient_background(color: tuple[int, int, int]) -> Image.Image:
    """뉴스 스타일: 단색 그라데이션 배경 (RGB)."""
    bg = Image.new("RGB", (_THUMB_W, _THUMB_H))
    draw = ImageDraw.Draw(bg)
    r, g, b = color
    for y in range(_THUMB_H):
        ratio = y / _THUMB_H
        # 위가 조금 밝고, 아래로 갈수록 어두워짐
        factor = 1.4 - ratio * 0.8
        rr = min(255, int(r * factor))
        gg = min(255, int(g * factor))
        bb = min(255, int(b * factor))
        draw.line([(0, y), (_THUMB_W, y)], fill=(rr, gg, bb))
    return bg


def _make_background(image_url: Optional[str], style: str) -> Image.Image:
    """스타일에 맞는 배경 이미지를 생성한다."""
    cfg = _STYLES.get(style, _STYLES[_DEFAULT_STYLE])

    if cfg["use_image"] and image_url:
        img = _download_image(image_url)
        if img:
            bg = _fill_crop(img, _THUMB_W, _THUMB_H)
            overlay = _gradient_overlay(cfg["gradient_color"], cfg["overlay_alpha"])
            bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
            return bg

    # news 스타일 또는 이미지 없음 → 그라데이션 배경
    return _gradient_background(cfg["gradient_color"])


# ---------------------------------------------------------------------------
# 폰트 로드
# ---------------------------------------------------------------------------
def _load_font(font_path: Optional[Path], size: int) -> ImageFont.FreeTypeFont:
    candidates: list[Path] = []
    if font_path:
        candidates.append(font_path)
    if ASSETS_DIR.exists():
        candidates.extend(ASSETS_DIR.glob("**/NanumGothic*.ttf"))

    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            pass

    system_paths = [
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/nanum/NanumGothic.ttf"),
    ]
    for path in system_paths:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                pass

    logger.warning("NanumGothic 폰트 없음 → PIL 기본 폰트 사용")
    return ImageFont.load_default()


def _font_path_str(font: ImageFont.FreeTypeFont) -> Optional[str]:
    """ImageFont 객체에서 파일 경로를 추출한다. (Pillow 버전 차이 대응)"""
    for attr in ("path", "filename", "font"):
        val = getattr(font, attr, None)
        if isinstance(val, str) and val.endswith((".ttf", ".otf")):
            return val
    return None


# ---------------------------------------------------------------------------
# 텍스트 렌더링
# ---------------------------------------------------------------------------
def _wrap_text(text: str, max_chars: int = 16) -> list[str]:
    """한국어 텍스트를 max_chars 기준으로 줄바꿈."""
    words: list[str] = []
    line = ""
    for ch in text:
        if len(line) >= max_chars and ch in (" ", ",", ".", "!", "?", "~"):
            words.append(line)
            line = ""
        line += ch
    if line:
        words.append(line)
    return words


def _draw_outlined_text(
    draw: ImageDraw.ImageDraw,
    pos: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    text_color: tuple[int, int, int],
    outline_color: tuple[int, int, int],
    outline_size: int = 3,
) -> None:
    """외곽선(stroke) 텍스트를 그린다."""
    x, y = pos
    for dx in range(-outline_size, outline_size + 1):
        for dy in range(-outline_size, outline_size + 1):
            if abs(dx) + abs(dy) <= outline_size + 1:
                draw.text((x + dx, y + dy), text, font=font, fill=(*outline_color, 220))
    draw.text((x, y), text, font=font, fill=(*text_color, 255))


def _draw_text_layer(
    canvas_size: tuple[int, int],
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    text_color: tuple[int, int, int],
    outline_color: tuple[int, int, int],
) -> Image.Image:
    """텍스트 라인들을 RGBA 레이어에 중앙 배치해 반환한다."""
    layer = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    line_h = _BASE_FONT_SIZE + 16
    total_h = line_h * len(lines)
    start_y = (canvas_size[1] - total_h) // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (canvas_size[0] - text_w) // 2
        y = start_y + i * line_h
        _draw_outlined_text(draw, (x, y), line, font, text_color, outline_color)

    return layer


def _draw_normal_text(
    canvas: Image.Image,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    text_color: tuple[int, int, int],
    outline_color: tuple[int, int, int],
) -> None:
    """텍스트를 직접 캔버스에 그린다."""
    draw = ImageDraw.Draw(canvas)
    line_h = _BASE_FONT_SIZE + 16
    total_h = line_h * len(lines)
    start_y = (_THUMB_H - total_h) // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (_THUMB_W - text_w) // 2
        y = start_y + i * line_h
        _draw_outlined_text(draw, (x, y), line, font, text_color, outline_color)


def _draw_rotated_text(
    canvas: Image.Image,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    text_color: tuple[int, int, int],
    outline_color: tuple[int, int, int],
    angle: float,
) -> None:
    """텍스트 레이어를 angle도 회전시켜 캔버스에 합성한다 (dramatic 전용)."""
    txt_layer = _draw_text_layer(
        (canvas.width, canvas.height), lines, font, text_color, outline_color
    )
    rotated = txt_layer.rotate(angle, expand=False, center=(canvas.width // 2, canvas.height // 2))
    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba = Image.alpha_composite(canvas_rgba, rotated)
    canvas.paste(canvas_rgba.convert("RGB"))


def _draw_news_text(
    canvas: Image.Image,
    lines: list[str],
    font: ImageFont.FreeTypeFont,
    text_color: tuple[int, int, int],
    font_path_s: Optional[str],
) -> None:
    """뉴스 자막 스타일 (하단 바 + 날짜)."""
    draw = ImageDraw.Draw(canvas)

    bar_h = 90
    bar_y = _THUMB_H - bar_h - 50

    # 빨간 속보 바
    draw.rectangle([(0, bar_y), (_THUMB_W, bar_y + bar_h)], fill=(200, 0, 0))

    # "속보" 레이블
    label_size = 30
    label_font = font
    if font_path_s:
        try:
            label_font = ImageFont.truetype(font_path_s, label_size)
        except Exception:
            pass

    draw.text((28, bar_y + 22), "속보", font=label_font, fill=(255, 230, 0))
    # 구분선
    draw.rectangle([(120, bar_y + 15), (126, bar_y + bar_h - 15)], fill=(255, 255, 255, 160))

    # 본문 텍스트 (1줄로 줄임)
    text = " ".join(lines)[:50]
    main_size = 36
    main_font = font
    if font_path_s:
        try:
            main_font = ImageFont.truetype(font_path_s, main_size)
        except Exception:
            pass
    draw.text((146, bar_y + 22), text, font=main_font, fill=(*text_color, 255))

    # 상단 날짜
    today = date.today().strftime("%Y.%m.%d")
    draw.text((_THUMB_W - 160, 28), today, font=label_font, fill=(180, 180, 180))

    # 채널명 스타일 상단 바 (선택)
    draw.rectangle([(0, 0), (_THUMB_W, 8)], fill=(200, 0, 0))


def _draw_icon(
    canvas: Image.Image,
    icon: Optional[str],
    icon_color: Optional[tuple[int, int, int]],
    font: ImageFont.FreeTypeFont,
    font_path_s: Optional[str],
) -> None:
    """아이콘/이모지를 우상단에 렌더링한다."""
    if not icon:
        return

    icon_size = 90
    icon_font = font
    if font_path_s:
        try:
            icon_font = ImageFont.truetype(font_path_s, icon_size)
        except Exception:
            pass

    draw = ImageDraw.Draw(canvas)
    color = icon_color or (255, 255, 255)

    # 글리프 너비 확인 (지원 안 되는 경우 0)
    try:
        bbox = draw.textbbox((0, 0), icon, font=icon_font)
        glyph_w = bbox[2] - bbox[0]
    except Exception:
        glyph_w = 0

    if glyph_w < 5:
        return  # 해당 폰트에서 렌더 불가 → 생략

    x = _THUMB_W - glyph_w - 30
    y = 20
    # 아이콘 그림자
    draw.text((x + 3, y + 3), icon, font=icon_font, fill=(0, 0, 0, 160))
    draw.text((x, y), icon, font=icon_font, fill=(*color, 240))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def generate_thumbnail(
    hook_text: str,
    images: list[str],
    output_path: Path,
    style: str = "dramatic",
    font_path: Optional[Path] = None,
) -> Path:
    """YouTube 썸네일 생성 (1280x720).

    Args:
        hook_text: 썸네일에 표시할 후킹 텍스트
        images: 배경에 사용할 이미지 URL 목록 (첫 번째 사용)
        output_path: 저장 경로 (.jpg)
        style: 'dramatic' | 'question' | 'funny' | 'news'
        font_path: 커스텀 폰트 경로 (Optional)

    Returns:
        저장된 썸네일 경로
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = _STYLES.get(style, _STYLES[_DEFAULT_STYLE])
    first_image_url = images[0] if images else None

    bg = _make_background(first_image_url, style)
    font = _load_font(font_path, _BASE_FONT_SIZE)
    font_path_s = _font_path_str(font)

    lines = _wrap_text(hook_text, max_chars=16)
    text_color: tuple[int, int, int] = cfg["text_color"]
    outline_color: tuple[int, int, int] = cfg["outline_color"]
    angle: float = cfg.get("text_angle", 0)

    if style == "news":
        _draw_news_text(bg, lines, font, text_color, font_path_s)
    elif angle != 0:
        _draw_rotated_text(bg, lines, font, text_color, outline_color, angle)
    else:
        _draw_normal_text(bg, lines, font, text_color, outline_color)

    _draw_icon(bg, cfg.get("icon"), cfg.get("icon_color"), font, font_path_s)

    bg.convert("RGB").save(str(output_path), "JPEG", quality=90)
    logger.info("썸네일 저장: %s (style=%s)", output_path, style)
    return output_path


def get_thumbnail_path(site_code: str, origin_id: str) -> Path:
    """썸네일 경로 반환. media/thumbnails/{site_code}/post_{origin_id}.jpg"""
    thumb_dir = MEDIA_DIR / "thumbnails" / site_code
    thumb_dir.mkdir(parents=True, exist_ok=True)
    return thumb_dir / f"post_{origin_id}.jpg"
