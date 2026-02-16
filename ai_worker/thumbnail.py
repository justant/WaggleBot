"""
썸네일 생성 모듈

hook_text와 원본 이미지 URL을 이용해 YouTube 썸네일(1280x720)을 생성한다.
"""

import logging
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from config.settings import ASSETS_DIR, MEDIA_DIR

logger = logging.getLogger(__name__)

_THUMB_W = 1280
_THUMB_H = 720
_FONT_SIZE = 72
_SHADOW_OFFSET = 4


def _download_image(url: str, timeout: int = 15) -> Optional[Image.Image]:
    """URL에서 이미지 다운로드. 실패 시 None 반환."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        from io import BytesIO
        return Image.open(BytesIO(resp.content)).convert("RGB")
    except Exception as e:
        logger.warning("이미지 다운로드 실패 (%s): %s", url, e)
        return None


def _make_background(image_url: Optional[str]) -> Image.Image:
    """배경 이미지 생성: 이미지 있으면 fill-crop + 어둡게, 없으면 그라데이션."""
    if image_url:
        img = _download_image(image_url)
        if img:
            # fill crop — 대상 비율로 중앙 크롭
            src_w, src_h = img.size
            target_ratio = _THUMB_W / _THUMB_H
            src_ratio = src_w / src_h

            if src_ratio > target_ratio:
                new_h = src_h
                new_w = int(src_h * target_ratio)
            else:
                new_w = src_w
                new_h = int(src_w / target_ratio)

            left = (src_w - new_w) // 2
            top = (src_h - new_h) // 2
            img = img.crop((left, top, left + new_w, top + new_h))
            img = img.resize((_THUMB_W, _THUMB_H), Image.LANCZOS)

            # 반투명 어두운 오버레이 (alpha 0.5)
            overlay = Image.new("RGBA", (_THUMB_W, _THUMB_H), (0, 0, 0, 128))
            img = img.convert("RGBA")
            img = Image.alpha_composite(img, overlay).convert("RGB")
            return img

    # 그라데이션 배경 (어두운 파랑 → 검정)
    bg = Image.new("RGB", (_THUMB_W, _THUMB_H))
    draw = ImageDraw.Draw(bg)
    for y in range(_THUMB_H):
        ratio = y / _THUMB_H
        r = int(10 * (1 - ratio))
        g = int(20 * (1 - ratio))
        b = int(60 * (1 - ratio))
        draw.line([(0, y), (_THUMB_W, y)], fill=(r, g, b))
    return bg


def _load_font(font_path: Optional[Path], size: int) -> ImageFont.FreeTypeFont:
    """폰트 로드. 없으면 PIL 기본 폰트로 폴백."""
    candidates: list[Path] = []
    if font_path:
        candidates.append(font_path)
    # ASSETS_DIR에서 NanumGothic 탐색
    if ASSETS_DIR.exists():
        candidates.extend(ASSETS_DIR.glob("**/NanumGothic*.ttf"))

    for path in candidates:
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            pass

    # 시스템 NanumGothic 탐색
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

    logger.warning("NanumGothic 폰트를 찾을 수 없어 PIL 기본 폰트를 사용합니다.")
    return ImageFont.load_default()


def _draw_text_with_shadow(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    canvas_w: int,
    canvas_h: int,
) -> None:
    """텍스트를 중앙에 그림자와 함께 그린다."""
    # 줄바꿈 처리 (20자 기준)
    words = list(text)
    lines: list[str] = []
    line = ""
    for ch in words:
        if len(line) >= 20 and ch in (" ", ",", ".", "!", "?", "~"):
            lines.append(line)
            line = ""
        line += ch
    if line:
        lines.append(line)

    line_height = _FONT_SIZE + 10
    total_height = line_height * len(lines)
    start_y = (canvas_h - total_height) // 2

    for i, ln in enumerate(lines):
        bbox = draw.textbbox((0, 0), ln, font=font)
        text_w = bbox[2] - bbox[0]
        x = (canvas_w - text_w) // 2
        y = start_y + i * line_height

        # 그림자
        draw.text((x + _SHADOW_OFFSET, y + _SHADOW_OFFSET), ln, font=font, fill=(0, 0, 0, 220))
        # 본문 (흰색)
        draw.text((x, y), ln, font=font, fill=(255, 255, 255, 255))


def generate_thumbnail(
    hook_text: str,
    images: list[str],
    output_path: Path,
    font_path: Optional[Path] = None,
) -> Path:
    """
    YouTube 썸네일 생성 (1280x720).

    Args:
        hook_text: 썸네일에 표시할 후킹 텍스트
        images: 배경에 사용할 이미지 URL 목록 (첫 번째 사용)
        output_path: 저장 경로 (.jpg)
        font_path: 커스텀 폰트 경로 (Optional)

    Returns:
        저장된 썸네일 경로
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    first_image_url = images[0] if images else None
    bg = _make_background(first_image_url)

    draw = ImageDraw.Draw(bg, "RGBA")
    font = _load_font(font_path, _FONT_SIZE)

    _draw_text_with_shadow(draw, hook_text, font, _THUMB_W, _THUMB_H)

    bg.convert("RGB").save(str(output_path), "JPEG", quality=90)
    logger.info("썸네일 저장: %s", output_path)
    return output_path


def get_thumbnail_path(post_id: int) -> Path:
    """게시글 ID에 대한 기본 썸네일 경로 반환."""
    return MEDIA_DIR / "thumbnails" / f"post_{post_id}.jpg"
