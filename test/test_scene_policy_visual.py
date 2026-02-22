#!/usr/bin/env python3
"""Scene Policy 시각적 렌더링 테스트.

DB에서 실제 Content를 가져와 SceneDirector로 씬을 구성하고,
각 씬을 PNG 프레임으로 렌더링 후 MP4로 출력한다.

실행 (Docker 필수):
    docker compose exec ai_worker python test/test_scene_policy_visual.py

출력:
    test/test_scene_policy_visual_output/
        case_A1_*/  case_A2_*/ — 이미지 0장 + 긴 텍스트 (2건)
        case_B1_*/  case_B2_/ — 이미지 3장 + 텍스트 (2건)
        case_C1_*/  case_C2_*/ — 이미지 10장 + 짧은 텍스트 (2건)
        test_scene_policy.mp4
"""
import json
import subprocess
import sys
from pathlib import Path

import requests
from PIL import Image

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ai_worker.pipeline.resource_analyzer import ResourceProfile
from ai_worker.pipeline.scene_director import SceneDecision, SceneDirector
from ai_worker.renderer.layout import (
    _create_base_frame,
    _load_font,
    _load_layout,
    _render_img_only_frame,
    _render_img_text_frame,
    _render_intro_frame,
    _render_outro_frame,
    _render_text_only_frame,
    _wrap_korean,
)
from config.settings import ASSETS_DIR, MEDIA_DIR
from db.models import Content, Post, ScriptData
from db.session import SessionLocal

OUT_DIR = _HERE / "test_scene_policy_visual_output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

FONT_DIR = ASSETS_DIR / "fonts"

# 케이스별 mood 오버라이드 (DB 데이터는 모두 'funny' 레거시)
CASE_MOODS = {
    "A": "horror",
    "B": "humor",
    "C": "shock",
}

# 테스트할 게시글 (case → [post_id, post_id])
CASE_POSTS = {
    "A": [748, 746],   # 0장: 자녀없는 이혼 이후 명절(268자), 아내에게 이혼하자고(234자)
    "B": [643, 635],   # 3장: 정국이 여친(147자), 홍은채 나이 들면(89자)
    "C": [791, 545],   # 10장: 계념없는 모녀(99자), 키키 하음(96자)
}


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

def _download_url_image(url: str, out_path: Path) -> bool:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://pann.nate.com/",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200 and len(resp.content) > 1000:
            out_path.write_bytes(resp.content)
            return True
    except Exception:
        pass
    return False


def _load_post_images(post: Post) -> list[Path]:
    """Post.images 목록에서 실제 이미지 Path 반환 (URL이면 임시 다운로드)."""
    images = post.images or []
    tmp_dir = OUT_DIR / "tmp_post_images" / str(post.id)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    result: list[Path] = []

    for idx, img in enumerate(images):
        img_str = str(img)
        if not img_str.startswith("http"):
            p = Path(img_str) if Path(img_str).is_absolute() else MEDIA_DIR / img_str
            if p.exists():
                result.append(p)
            continue
        tmp_path = tmp_dir / f"img_{idx:02d}.jpg"
        if tmp_path.exists() and tmp_path.stat().st_size > 1000:
            result.append(tmp_path)
            continue
        if _download_url_image(img_str, tmp_path):
            result.append(tmp_path)

    return result


def _open_image(path: str | Path | None) -> Image.Image | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return Image.open(p).convert("RGB")
    except Exception:
        return None


def _label_frame(img: Image.Image, line1: str, line2: str = "") -> Image.Image:
    from PIL import ImageDraw, ImageFont
    img = img.copy()
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle([(0, 0), (img.width, 115)], fill=(0, 0, 0, 210))
    try:
        f1 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
        f2 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 27)
    except Exception:
        f1 = f2 = ImageFont.load_default()
    draw.text((20, 10), line1, font=f1, fill=(255, 220, 0, 255))
    if line2:
        draw.text((20, 62), line2[:72], font=f2, fill=(200, 200, 200, 255))
    return img.convert("RGB")


# ---------------------------------------------------------------------------
# 씬 → PNG 렌더링
# ---------------------------------------------------------------------------

def render_scene(
    scene: SceneDecision,
    idx: int,
    prefix: str,
    layout: dict,
    base_frame: Image.Image,
    text_history: list,
) -> Path:
    out_path = OUT_DIR / f"{prefix}_{idx:02d}_{scene.type}.png"
    img_pil = _open_image(scene.image_url)

    if scene.type == "intro":
        _render_intro_frame(base_frame, out_path)

    elif scene.type == "img_text":
        text = scene.text_lines[0] if scene.text_lines else ""
        _render_img_text_frame(base_frame, img_pil, text, layout, FONT_DIR, out_path)

    elif scene.type == "img_only":
        _render_img_only_frame(base_frame, img_pil, layout, out_path)

    elif scene.type == "outro":
        # fixed_text는 TTS 전용 — 화면에는 표시하지 않음 (overlay_text="")
        _render_outro_frame(base_frame, img_pil, "", layout, FONT_DIR, out_path)

    elif scene.type == "text_only":
        text = scene.text_lines[0] if scene.text_lines else ""
        ta = layout["scenes"]["text_only"]["elements"]["text_area"]
        font = _load_font(FONT_DIR, "NotoSansKR-Medium.ttf", ta["font_size"])
        lines = _wrap_korean(text, font, ta["max_width"])
        for prev in text_history:
            prev["is_new"] = False
        if len(text_history) >= ta.get("max_slots", 3):
            text_history.clear()
        text_history.append({"lines": lines, "is_new": True})
        _render_text_only_frame(base_frame, list(text_history), layout, FONT_DIR, out_path)

    # 레이블 오버레이
    img_url_name = Path(scene.image_url).name if scene.image_url else "없음"
    tts_note = f"[TTS: {scene.text_lines[0][:20]}...]" if scene.type == "outro" and scene.text_lines else ""
    line1 = f"[{idx:02d}] {scene.type}  mood={scene.mood}  tts_emotion={scene.tts_emotion or '-'}"
    line2 = f"img={img_url_name}  {tts_note}"
    img = Image.open(out_path)
    img = _label_frame(img, line1, line2)
    img.save(str(out_path))
    return out_path


# ---------------------------------------------------------------------------
# 케이스 실행
# ---------------------------------------------------------------------------

def run_case(
    prefix: str,
    post: Post,
    content: Content,
    override_mood: str,
) -> list[Path]:
    sd = content.get_script()
    if not sd:
        print(f"  ⚠️  ScriptData 없음 ({post.id})")
        return []

    post_images = _load_post_images(post)
    body_chars = sum(len(" ".join(b.get("lines", []))) for b in sd.body)

    print(f"\n  {'─'*56}")
    print(f"  [{prefix}] post_id={post.id}  이미지={len(post_images)}장  본문={body_chars}자")
    print(f"  제목: {post.title[:50]}")
    print(f"  mood: DB='{sd.mood}' → override='{override_mood}'")

    layout = _load_layout()
    base_frame = _create_base_frame(
        layout, sd.title_suggestion or post.title, FONT_DIR, ASSETS_DIR
    )

    images_str = [str(p) for p in post_images]
    profile = ResourceProfile(
        image_count=len(images_str),
        text_length=body_chars,
        estimated_sentences=len(sd.body),
        ratio=len(images_str) / max(len(sd.body), 1),
        strategy="balanced",
    )
    director = SceneDirector(
        profile=profile,
        images=images_str,
        script=json.loads(sd.to_json()),
        mood=override_mood,
    )
    scenes = director.direct()

    print(f"\n  씬 구성 ({len(scenes)}개):")
    for i, s in enumerate(scenes):
        img_name = Path(s.image_url).name if s.image_url else "없음"
        tts_note = f"  ← TTS only, 화면출력 X" if s.type == "outro" else ""
        print(f"    [{i+1:02d}] {s.type:<10} img={img_name}{tts_note}")

    frames: list[Path] = []
    text_history: list = []
    for i, scene in enumerate(scenes):
        fp = render_scene(scene, i + 1, prefix, layout, base_frame, text_history)
        frames.append(fp)

    print(f"  ✅ {len(frames)}프레임 생성")
    return frames


# ---------------------------------------------------------------------------
# MP4 생성
# ---------------------------------------------------------------------------

def make_mp4(all_frames: list[Path], output_path: Path) -> None:
    concat_file = OUT_DIR / "concat.txt"
    lines = []
    for fp in all_frames:
        lines.append(f"file '{fp.resolve()}'\n")
        lines.append("duration 2.0\n")
    if all_frames:
        lines.append(f"file '{all_frames[-1].resolve()}'\n")
    concat_file.write_text("".join(lines), encoding="utf-8")

    for codec in ["h264_nvenc", "libx264"]:
        enc = (
            ["-c:v", "h264_nvenc", "-preset", "medium", "-cq", "23"]
            if codec == "h264_nvenc"
            else ["-c:v", "libx264", "-preset", "fast", "-crf", "23"]
        )
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            *enc, "-pix_fmt", "yuv420p", "-r", "1", "-an",
            str(output_path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            size_mb = output_path.stat().st_size / 1024 / 1024
            print(f"\n✅ MP4 생성: {output_path}  ({size_mb:.1f}MB)")
            return
    print("❌ FFmpeg 실패", file=sys.stderr)


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("Scene Policy 시각적 렌더링 테스트")
    print("(DB 실제 Content 기반, 케이스별 2건씩 총 6건)")
    print(f"출력: {OUT_DIR}")
    print("=" * 60)

    all_frames: list[Path] = []

    with SessionLocal() as db:
        for case_key, post_ids in CASE_POSTS.items():
            mood = CASE_MOODS[case_key]
            case_desc = {"A": "이미지 0장 + 긴 텍스트", "B": "이미지 3장", "C": "이미지 10장"}[case_key]
            print(f"\n{'='*60}")
            print(f"  CASE {case_key}: {case_desc}  (mood={mood})")
            print(f"{'='*60}")

            for n, pid in enumerate(post_ids, start=1):
                post = db.query(Post).filter_by(id=pid).first()
                content = db.query(Content).filter_by(post_id=pid).first()
                if not post or not content:
                    print(f"  ⚠️  post_id={pid} 없음")
                    continue
                prefix = f"case_{case_key}{n}"
                frames = run_case(prefix, post, content, mood)
                all_frames.extend(frames)

    if all_frames:
        output_mp4 = OUT_DIR / "test_scene_policy.mp4"
        make_mp4(all_frames, output_mp4)
        print(f"\n{'='*60}")
        print(f"총 {len(all_frames)}프레임")
        print(f"PNG: {OUT_DIR}/")
        print(f"MP4: {output_mp4}")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
