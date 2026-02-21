#!/usr/bin/env python3
"""레이아웃 텍스트 오버플로우 테스트 스크립트.

_truncate() 없이 다양한 길이의 텍스트를 렌더링하여
레이아웃 범위를 넘어가는 양상을 확인한다.

Usage (Docker):
    docker compose exec ai_worker python ai_worker/renderer/test_layout.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# 프로젝트 루트를 sys.path에 추가 (직접 실행 시)
_HERE = Path(__file__).resolve().parent   # test/
_ROOT = _HERE.parent                       # project root
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai_worker.renderer.layout import (
    _load_layout,
    _load_font,
    _wrap_korean,
    _draw_centered_text,
    _fit_cover,
    _paste_rounded,
    _create_base_frame,
    _render_text_only_frame,
    _render_img_text_frame,
    _render_intro_frame,
)
from config.settings import ASSETS_DIR, MEDIA_DIR

# ──────────────────────────────────────────────────────────────────────────────
# 테스트 시나리오 정의
# ──────────────────────────────────────────────────────────────────────────────

# 실제 LLM 출력 데이터 (사용자 제공)
REAL_SCRIPT = {
    "hook": "턱끝필러 진짜 이뻐?",
    "body": [
        "요즘 턱 끝 필러로 개 뾰족하게 만드는 거 대세네",      # 26자
        "입술 필러 맞아서 준나 듀~ 하게 만들어도 인스타 넘쳐남ㅋㅋ",  # 30자
        "인조틱하다고 느껴지는 건 저만?",                       # 15자
        "베댓 '이건 좀 심하지 않음?'",                          # 14자
    ],
    "closer": "여러분들의 생각은 어떤가요?",
    "title": "턱끝필러 개 뾰족하게 만드는 트렌드 진짜 이쁘지?",
}

# 텍스트 길이 변형 시나리오 (text_only 씬 테스트용)
TEXT_ONLY_SCENARIOS = [
    {
        "label": "S01_very_short",
        "desc": "매우 짧음 (8자)",
        "texts": ["짧은 텍스트", "짧은 텍스트2", "짧은3"],
    },
    {
        "label": "S02_at_limit",
        "desc": "20자 한도 근처",
        "texts": ["필러로 예뻐지는 트렌드야", "인스타에 넘쳐나는 얼굴들", "진짜 이쁘긴 한데..."],
    },
    {
        "label": "S03_real_data",
        "desc": "실제 데이터 (truncate 제거 후)",
        "texts": REAL_SCRIPT["body"],
    },
    {
        "label": "S04_long",
        "desc": "긴 텍스트 (30~40자)",
        "texts": [
            "턱 끝 필러로 개 뾰족하게 만드는 게 진짜 트렌드가 됐다는데",
            "입술 필러 맞아서 준나 두껍게 만들어도 인스타에 넘쳐나더라고",
            "이게 자연스럽다고 느끼는 사람이 있다는 게 신기하다",
        ],
    },
    {
        "label": "S05_very_long",
        "desc": "매우 긴 텍스트 (50자+)",
        "texts": [
            "요즘 턱 끝 필러로 개 뾰족하게 만드는 거 유행이라고 하는데 솔직히 너무 인위적으로 보이지 않나요",
            "입술 필러 맞아서 준나 듀~ 하게 만들어도 인스타에 넘쳐난다는 거 보면 트렌드가 변한 것 같아",
        ],
    },
]

# img_text 씬 텍스트 시나리오
IMG_TEXT_SCENARIOS = [
    {"label": "IT01_short", "desc": "짧음 (10자)", "text": "필러 유행 중"},
    {"label": "IT02_at_limit", "desc": "20자 한도", "text": "인스타 필러 트렌드 변화"},
    {"label": "IT03_real", "desc": "실제 데이터 1번", "text": REAL_SCRIPT["body"][0]},
    {"label": "IT04_long", "desc": "긴 텍스트 (30자)", "text": "입술 필러 맞아서 준나 두껍게 만들어도 인스타 넘쳐남"},
    {"label": "IT05_very_long", "desc": "매우 긴 텍스트 (50자+)", "text": "요즘 턱 끝 필러로 개 뾰족하게 만드는 거 유행이라고 하는데 솔직히 너무 인위적으로 보이지 않나요"},
]


# ──────────────────────────────────────────────────────────────────────────────
# 레이블 오버레이 유틸리티
# ──────────────────────────────────────────────────────────────────────────────

def _draw_label(img: Image.Image, label: str, desc: str) -> Image.Image:
    """프레임 상단에 시나리오 레이블과 설명을 반투명 배경으로 표시."""
    img = img.copy()
    draw = ImageDraw.Draw(img, "RGBA")
    # 반투명 검정 배경
    draw.rectangle([(0, 0), (img.width, 90)], fill=(0, 0, 0, 180))
    try:
        # 영문/숫자 레이블은 기본 폰트 사용
        font_lbl = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_desc = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    except Exception:
        font_lbl = ImageFont.load_default()
        font_desc = font_lbl
    draw.text((20, 8), label, font=font_lbl, fill=(255, 255, 0, 255))
    draw.text((20, 52), desc, font=font_desc, fill=(200, 200, 200, 255))
    return img.convert("RGB")


def _draw_char_count(img: Image.Image, text: str, y: int, canvas_w: int) -> Image.Image:
    """텍스트 오른쪽에 글자 수를 표시 (디버그용)."""
    img = img.copy()
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    count_str = f"({len(text)}자)"
    draw.text((canvas_w - 160, y), count_str, font=font, fill=(255, 80, 80))
    return img


# ──────────────────────────────────────────────────────────────────────────────
# 테스트 프레임 생성
# ──────────────────────────────────────────────────────────────────────────────

def render_text_only_test(
    layout: dict,
    font_dir: Path,
    base_frame: Image.Image,
    scenario: dict,
    out_path: Path,
) -> list[Path]:
    """text_only 씬 누적 스태킹 테스트 — 각 슬롯 추가마다 1프레임 생성."""
    sc = layout["scenes"]["text_only"]
    ta = sc["elements"]["text_area"]
    font = _load_font(font_dir, "NotoSansKR-Medium.ttf", ta["font_size"])
    to_max_w = ta["max_width"]

    texts = scenario["texts"]
    label = scenario["label"]
    desc = scenario["desc"]
    cw = layout["canvas"]["width"]

    frames: list[Path] = []
    text_history: list[dict] = []

    for i, raw_text in enumerate(texts):
        # truncate 없이 그대로 줄바꿈
        lines = _wrap_korean(raw_text, font, to_max_w)

        for prev in text_history:
            prev["is_new"] = False

        if len(text_history) >= ta.get("max_slots", 3):
            text_history = []

        text_history.append({"lines": lines, "is_new": True})

        frame_path = out_path / f"{label}_slot{i + 1:02d}.png"
        _render_text_only_frame(base_frame, text_history, layout, font_dir, frame_path)

        # 레이블 오버레이 추가
        img = Image.open(frame_path)
        img = _draw_label(img, f"{label} | slot {i + 1}", f"{desc} — \"{raw_text[:30]}{'...' if len(raw_text) > 30 else ''}\" ({len(raw_text)}자, {len(lines)}줄)")
        img.save(str(frame_path))

        frames.append(frame_path)

    return frames


def render_img_text_test(
    layout: dict,
    font_dir: Path,
    base_frame: Image.Image,
    scenario: dict,
    out_path: Path,
) -> Path:
    """img_text 씬 테스트 — truncate 없이 텍스트 렌더링."""
    raw_text = scenario["text"]
    label = scenario["label"]
    desc = scenario["desc"]

    frame_path = out_path / f"{label}.png"

    # img_pil=None (이미지 없이 텍스트만 테스트)
    _render_img_text_frame(base_frame, None, raw_text, layout, font_dir, frame_path)

    # 레이블 오버레이
    img = Image.open(frame_path)
    img = _draw_label(img, label, f"{desc} — ({len(raw_text)}자)")
    img.save(str(frame_path))

    return frame_path


# ──────────────────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    layout = _load_layout()
    font_dir = ASSETS_DIR / "fonts"

    out_dir = _HERE / "test_layout_output"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[test_layout] 출력 디렉터리: {out_dir}")

    # 테스트 제목으로 베이스 프레임 생성
    base_frame = _create_base_frame(layout, REAL_SCRIPT["title"], font_dir, ASSETS_DIR)
    print("[test_layout] 베이스 프레임 생성 완료")

    all_frames: list[Path] = []

    # ── intro 프레임 (표지) ────────────────────────────────────
    intro_path = out_dir / "intro.png"
    _render_intro_frame(base_frame, intro_path)
    img = Image.open(intro_path)
    img = _draw_label(img, "테스트 시작", "_truncate() 제거 후 오버플로우 확인")
    img.save(str(intro_path))
    all_frames.append(intro_path)
    print("[test_layout] intro 프레임 생성")

    # ── text_only 시나리오 ─────────────────────────────────────
    print("\n[test_layout] text_only 시나리오 생성 중...")
    for scenario in TEXT_ONLY_SCENARIOS:
        frames = render_text_only_test(layout, font_dir, base_frame, scenario, out_dir)
        all_frames.extend(frames)
        print(f"  {scenario['label']}: {len(frames)}프레임")

    # ── img_text 시나리오 ──────────────────────────────────────
    print("\n[test_layout] img_text 시나리오 생성 중...")
    for scenario in IMG_TEXT_SCENARIOS:
        frame = render_img_text_test(layout, font_dir, base_frame, scenario, out_dir)
        all_frames.append(frame)
        print(f"  {scenario['label']}: 1프레임")

    print(f"\n[test_layout] 총 {len(all_frames)}프레임 생성 완료")

    # ── FFmpeg 무음 MP4 생성 ───────────────────────────────────
    # concat 리스트 작성 (프레임당 2.5초)
    concat_file = out_dir / "concat.txt"
    lines_txt = []
    for fp in all_frames:
        lines_txt.append(f"file '{fp.resolve()}'\n")
        lines_txt.append("duration 2.5\n")
    if all_frames:
        lines_txt.append(f"file '{all_frames[-1].resolve()}'\n")
    concat_file.write_text("".join(lines_txt), encoding="utf-8")

    # 코덱 선택 (nvenc 시도 → libx264 폴백)
    output_mp4 = out_dir / "test_layout.mp4"
    for codec in ["h264_nvenc", "libx264"]:
        enc_args = (
            ["-c:v", "h264_nvenc", "-preset", "medium", "-cq", "23"]
            if codec == "h264_nvenc"
            else ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            *enc_args,
            "-pix_fmt", "yuv420p",
            "-r", "1",  # 1fps (정지 프레임 영상)
            "-an",  # 무음
            str(output_mp4),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            print(f"\n[test_layout] MP4 생성 완료 ({codec}): {output_mp4}")
            break
        if codec == "h264_nvenc":
            print(f"[test_layout] h264_nvenc 실패, libx264 시도...")
    else:
        print(f"[test_layout] FFmpeg 오류:\n{result.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)

    # 결과 요약
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)
    print(f"출력 MP4: {output_mp4}")
    print(f"개별 PNG: {out_dir}/")
    print()
    print("각 씬의 텍스트 글자 수 및 줄 수:")
    sc_to = layout["scenes"]["text_only"]
    ta = sc_to["elements"]["text_area"]
    font = _load_font(font_dir, "NotoSansKR-Medium.ttf", ta["font_size"])
    max_w = ta["max_width"]
    y_coords = ta.get("y_coords", [550, 700, 850])
    lh = ta.get("line_height", 110)

    print(f"\n[text_only] y_coords={y_coords}, line_height={lh}, max_width={max_w}")
    print(f"  슬롯 간격: {y_coords[1] - y_coords[0]}px  (넘치면 다음 슬롯 침범)")
    for scenario in TEXT_ONLY_SCENARIOS:
        print(f"\n  {scenario['label']} ({scenario['desc']}):")
        for t in scenario["texts"]:
            lines = _wrap_korean(t, font, max_w)
            wrap_h = len(lines) * lh
            print(f"    [{len(t)}자, {len(lines)}줄, {wrap_h}px] \"{t[:35]}{'...' if len(t) > 35 else ''}\"")


if __name__ == "__main__":
    main()
