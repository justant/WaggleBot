"""렌더링 품질 스크린샷 테스트 — PIL 프레임 캡처.

3가지 케이스(짧은/적당한/긴 텍스트)에 대해 base, image_text, text_only, video_text
프레임을 PNG로 저장하여 글자 크기·제목 말줄임·폰트 통일 등을 시각적으로 검증한다.

사용법:
    python test/test_render_screenshots.py
"""
import json
import sys
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from PIL import Image, ImageDraw

from ai_worker.renderer.layout import (
    _create_base_frame,
    _draw_centered_text,
    _load_font,
    _render_image_text_frame,
    _render_text_only_frame,
    _wrap_korean,
)

# ── 경로 설정 ──
LAYOUT_PATH = _PROJECT_ROOT / "config" / "layout.json"
FONT_DIR = _PROJECT_ROOT / "assets" / "fonts"
OUT_DIR = _PROJECT_ROOT / "_result" / "render_screenshots"
OUT_DIR.mkdir(parents=True, exist_ok=True)

layout = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))


# ── 테스트 케이스 정의 ──
CASES = [
    {
        "name": "case1",
        "title": "맛집 추천",
        "image_text": "정말 맛있어요",
        "text_only": [
            {"lines": ["추천합니다"], "is_new": True, "block_type": "body", "author": None},
        ],
    },
    {
        "name": "case2",
        "title": "오늘 회사에서 있었던 일",
        "image_text": "이런 일이 있었는데 여러분 어떻게 생각하세요?",
        "text_only": [
            {"lines": ["오늘 회사에서 진짜 어이없는"], "is_new": False, "block_type": "body", "author": None},
            {"lines": ["일이 있었는데 들어볼래요?"], "is_new": True, "block_type": "body", "author": None},
        ],
    },
    {
        "name": "case3",
        "title": "탕수육을 이렇게 먹는게 정말 못배워먹은건가요 진짜 궁금합니다",
        "image_text": "부먹이든 찍먹이든 맛있게 먹으면 그만이지 왜 그렇게 따지는건지 모르겠다",
        "text_only": [
            {"lines": ["친구들이랑 탕수육 시켰는데 소스를 부어"], "is_new": False, "block_type": "body", "author": None},
            {"lines": ["먹냐 찍어먹냐 가지고 진짜 심하게 싸"], "is_new": False, "block_type": "body", "author": None},
            {"lines": ["웠거든요 이게 싸울 일인가 진짜 궁금"], "is_new": True, "block_type": "body", "author": None},
        ],
    },
]


def _render_video_text_frame(
    base_frame: Image.Image,
    text: str,
    lo: dict,
    font_dir: Path,
    out_path: Path,
) -> Path:
    """video_text 씬을 PIL로 렌더링 (비디오 없이 회색 placeholder + 텍스트 오버레이)."""
    ta = lo["scenes"]["video_text"]["elements"]["text_area"]
    va = lo["scenes"]["video_text"]["elements"]["video_area"]
    cw = lo["canvas"]["width"]

    font = _load_font(font_dir, "NotoSansKR-Medium.ttf", ta["font_size"])
    lh = ta.get("line_height", int(ta["font_size"] * 1.4))
    stroke_color = ta.get("stroke_color", "")
    stroke_width = ta.get("stroke_width", 0)

    img = base_frame.copy()
    draw = ImageDraw.Draw(img)

    # 비디오 영역 placeholder (회색)
    try:
        draw.rounded_rectangle(
            [(va["x"], va["y"]), (va["x"] + va["width"], va["y"] + va["height"])],
            radius=va.get("border_radius", 0), fill="#666666",
        )
    except AttributeError:
        draw.rectangle(
            [(va["x"], va["y"]), (va["x"] + va["width"], va["y"] + va["height"])],
            fill="#666666",
        )

    # 텍스트 오버레이 — _draw_centered_text()로 다른 씬과 동일한 폰트 렌더링
    lines = _wrap_korean(text, font, ta["max_width"])
    total_h = len(lines) * lh
    y_start = ta["y"] - total_h // 2

    _draw_centered_text(
        draw, lines, font, y_start, lh,
        ta["color"], cw,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
    )

    img.save(str(out_path), "PNG")
    return out_path


def main() -> None:
    print(f"출력 디렉토리: {OUT_DIR}")
    total = 0

    for case in CASES:
        name = case["name"]
        title = case["title"]
        print(f"\n── {name}: 제목='{title}' ({len(title)}자) ──")

        # 1. base frame
        base = _create_base_frame(layout, title, FONT_DIR, FONT_DIR.parent)
        base_path = OUT_DIR / f"{name}_base.png"
        base.save(str(base_path), "PNG")
        print(f"  [OK] {base_path.name}")
        total += 1

        # 2. image_text (이미지 없이 회색 placeholder)
        image_text_path = OUT_DIR / f"{name}_image_text.png"
        _render_image_text_frame(
            base, None, case["image_text"], layout, FONT_DIR, image_text_path,
        )
        print(f"  [OK] {image_text_path.name}")
        total += 1

        # 3. text_only
        text_only_path = OUT_DIR / f"{name}_text_only.png"
        _render_text_only_frame(
            base, case["text_only"], layout, FONT_DIR, text_only_path,
        )
        print(f"  [OK] {text_only_path.name}")
        total += 1

        # 4. video_text (PIL 렌더링 — 비디오 없이 placeholder)
        video_text_path = OUT_DIR / f"{name}_video_text.png"
        _render_video_text_frame(
            base, case["image_text"], layout, FONT_DIR, video_text_path,
        )
        print(f"  [OK] {video_text_path.name}")
        total += 1

    print(f"\n완료: {total}개 PNG 생성 → {OUT_DIR}")


if __name__ == "__main__":
    main()
