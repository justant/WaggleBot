"""ai_worker/renderer/_frames.py — PIL 프레임 렌더링 함수 (internal)"""

import hashlib
import logging
import subprocess
from pathlib import Path
from typing import Optional

import requests
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# 캔버스 기본 상수 (layout.json 미로드 시 fallback)
CANVAS_W = 1080
CANVAS_H = 1920
HEADER_H = 160
HEADER_COLOR = "#4A44FF"


# ---------------------------------------------------------------------------
# 이미지 유틸리티
# ---------------------------------------------------------------------------

_dc_session: requests.Session | None = None


def _get_dc_session() -> requests.Session:
    """DCInside 이미지 다운로드용 세션 (쿠키 워밍업 포함)."""
    global _dc_session
    if _dc_session is not None:
        return _dc_session
    _dc_session = requests.Session()
    _dc_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    try:
        _dc_session.get("https://www.dcinside.com/", timeout=10)
        logger.debug("DCInside 이미지 세션 워밍업 OK (cookies=%d)",
                     len(_dc_session.cookies))
    except Exception:
        logger.debug("DCInside 이미지 세션 워밍업 실패 — 쿠키 없이 시도")
    return _dc_session


def _load_image(
    src: str, cache_dir: Path, max_retries: int = 2,
) -> Optional[Image.Image]:
    """URL 또는 로컬 경로에서 이미지 로드. 실패 시 재시도 후 None."""
    if src.startswith("http://") or src.startswith("https://"):
        url_hash = hashlib.md5(src.encode()).hexdigest()[:16]
        cache_path = cache_dir / f"img_{url_hash}.jpg"
        if not cache_path.exists():
            for attempt in range(max_retries + 1):
                try:
                    if "dcinside.com" in src:
                        sess = _get_dc_session()
                        resp = sess.get(src, timeout=15, headers={
                            "Referer": "https://gall.dcinside.com/",
                            "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                            "Sec-Fetch-Dest": "image",
                            "Sec-Fetch-Mode": "no-cors",
                            "Sec-Fetch-Site": "cross-site",
                        })
                    else:
                        _hdrs = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                                          "Chrome/131.0.0.0 Safari/537.36",
                            "Referer": f"{src.split('/')[0]}//{src.split('/')[2]}/",
                            "Accept": "image/*,*/*;q=0.8",
                        }
                        resp = requests.get(src, timeout=15, headers=_hdrs)
                    resp.raise_for_status()
                    if len(resp.content) < 200:
                        logger.warning(
                            "이미지 크기 의심 (%d bytes, 플레이스홀더?): %s",
                            len(resp.content), src,
                        )
                        return None
                    cache_path.write_bytes(resp.content)
                    break
                except Exception as e:
                    if attempt < max_retries:
                        import time
                        time.sleep(1 * (attempt + 1))
                        logger.debug(
                            "이미지 다운로드 재시도 (%d/%d): %s",
                            attempt + 1, max_retries, src,
                        )
                    else:
                        logger.warning(
                            "이미지 다운로드 실패 (재시도 %d회 후): %s — %s",
                            max_retries, src, e,
                        )
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


def _truncate(text: str, max_chars: int) -> str:
    """max_chars 초과 시 (max_chars-2)자 + '..'."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 2] + ".."


def _wrap_korean(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """한글 텍스트 줄바꿈 — 단어(공백) 단위 우선, 긴 단어는 강제 분리."""
    def _w(t: str) -> float:
        try:
            return font.getlength(t)
        except AttributeError:
            return float(font.getbbox(t)[2])

    if _w(text) <= max_width:
        return [text]

    words = text.split(" ")
    lines: list[str] = []
    current = ""

    for word in words:
        candidate = f"{current} {word}" if current else word
        if _w(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            # 단어 자체가 max_width 초과 → 글자 단위 강제 분리
            if _w(word) > max_width:
                for ch in word:
                    if current and _w(current + ch) > max_width:
                        lines.append(current)
                        current = ch
                    else:
                        current = (current or "") + ch
            else:
                current = word

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
    from ai_worker.renderer.layout import _load_font

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
        max_chars = ht.get("max_chars", 40)
        display = title[:max_chars] if len(title) > max_chars else title
        max_w = ht.get("max_width", 860)
        try:
            display_w = font.getlength(display)
        except AttributeError:
            display_w = float(font.getbbox(display)[2])
        if display_w <= max_w:
            lines = [display]
        else:
            # 픽셀 기반 글자 단위 truncation — "..." 포함 최대 길이
            suffix = "..."
            try:
                suffix_w = font.getlength(suffix)
            except AttributeError:
                suffix_w = float(font.getbbox(suffix)[2])
            truncated = ""
            for c in display:
                try:
                    cand_w = font.getlength(truncated + c)
                except AttributeError:
                    cand_w = float(font.getbbox(truncated + c)[2])
                if cand_w + suffix_w > max_w:
                    break
                truncated += c
            lines = [truncated.rstrip() + suffix]
        lh = int(ht.get("font_size", 52) * 1.3)
        draw = ImageDraw.Draw(base)

        x_pos = ht.get("x", 50)
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


def _render_image_text_frame(
    base_frame: Image.Image,
    img_pil: Optional[Image.Image],
    text: str,
    layout: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """씬 2: image_text — 베이스 프레임 + 이미지(900×900) + 하단 텍스트."""
    from ai_worker.renderer.layout import _load_font

    sc = layout["scenes"]["image_text"]
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
    text_history: list[dict],
    layout: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """씬 3: text_only — 동적 Y좌표로 텍스트 배치."""
    from ai_worker.renderer.layout import _load_font

    sc = layout["scenes"]["text_only"]
    ta = sc["elements"]["text_area"]

    font = _load_font(font_dir, "NotoSansKR-Medium.ttf", ta["font_size"])
    author_font = _load_font(font_dir, "NotoSansKR-Regular.ttf", max(int(ta["font_size"] * 0.55), 20))
    lh: int = ta.get("line_height", 120)
    slot_gap: int = ta.get("slot_gap", 5)
    new_color: str = ta.get("color", "#000000")
    prev_color: str = ta.get("prev_text_color", "#888888")
    comment_color: str = "#00BCD4"
    comment_prev_color: str = "#5E9EA0"
    author_color: str = "#9E9E9E"
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

        if block_type == "comment" and author:
            author_text = f"@{author}"
            try:
                aw = author_font.getlength(author_text)
            except AttributeError:
                aw = author_font.getbbox(author_text)[2]
            ax = int((cw - aw) / 2)
            draw.text((ax, slot_y), author_text, font=author_font, fill=author_color)
            slot_y += int(lh * 0.55)

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

        author_extra = int(lh * 0.55) if (block_type == "comment" and author) else 0
        current_y += len(entry["lines"]) * (lh + slot_gap) + author_extra

    img.save(str(out_path), "PNG")
    return out_path


def _render_image_only_frame(
    base_frame: Image.Image,
    img_pil: Optional[Image.Image],
    layout: dict,
    out_path: Path,
) -> Path:
    """씬 image_only — 베이스 프레임 + 이미지 전체 화면 cover 렌더링. 텍스트 없음."""
    sc = layout["scenes"]["image_only"]
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
    from ai_worker.renderer.layout import _load_font

    sc = layout["scenes"]["outro"]
    ia = sc["elements"]["image_area"]
    ot = sc["elements"]["overlay_text"]

    font = _load_font(font_dir, "NotoSansKR-Regular.ttf", ot["font_size"])
    lh = int(ot["font_size"] * 1.3)
    cw = layout["canvas"]["width"]

    img = base_frame.copy()
    draw = ImageDraw.Draw(img)

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


def _render_video_text_overlay(
    text: str,
    layout: dict,
    font_dir: Path,
    out_png: Path,
) -> Path:
    """비디오 텍스트를 투명 PNG 오버레이로 PIL 렌더링한다."""
    from ai_worker.renderer.layout import _load_font

    ta = layout["scenes"]["video_text"]["elements"]["text_area"]
    cw = layout["canvas"]["width"]
    ch = layout["canvas"]["height"]

    font = _load_font(font_dir, "NotoSansKR-Medium.ttf", ta["font_size"])
    lh = ta.get("line_height", int(ta["font_size"] * 1.4))
    stroke_color = ta.get("stroke_color", "")
    stroke_width = ta.get("stroke_width", 0)

    overlay = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    lines = _wrap_korean(text, font, ta["max_width"])
    total_h = len(lines) * lh
    y_start = ta["y"] - total_h // 2

    _draw_centered_text(
        draw, lines, font, y_start, lh,
        ta["color"], cw,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
    )

    overlay.save(str(out_png), "PNG")
    return out_png
