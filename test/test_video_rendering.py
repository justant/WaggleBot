"""비디오 씬 포함 최종 렌더링 통합 테스트.

실행 방법 (Docker 환경, FFmpeg 필요):
  docker compose exec ai_worker python -m pytest test/test_video_rendering.py -v

로컬 환경에서 FFmpeg가 없으면 테스트가 자동 skip됩니다.

테스트 결과물 위치:
  test/test_video_rendering_output/
  test/test_video_rendering_output/test_result.md
"""
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("test/test_video_rendering_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")

requires_ffmpeg = pytest.mark.skipif(
    _FFMPEG is None, reason="FFmpeg not installed (Docker 환경에서 실행하세요)"
)


def _create_test_video(output_path: Path, duration: float = 3.0) -> Path:
    """FFmpeg로 테스트용 합성 비디오를 생성한다."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size=512x512:rate=30",
        "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        pytest.skip(f"FFmpeg test video 생성 실패: {result.stderr[:200]}")
    return output_path


def _create_test_png(output_path: Path, width: int = 1080, height: int = 1920) -> Path:
    """PIL로 테스트용 PNG 이미지를 생성한다."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color=(40, 40, 60))
    img.save(str(output_path), "PNG")
    return output_path


class TestVideoRenderingUnit:
    """FFmpeg 불필요 — 순수 Python 단위 테스트."""

    def test_escape_ffmpeg_text(self):
        """_escape_ffmpeg_text가 특수문자를 올바르게 이스케이프한다."""
        from ai_worker.renderer.layout import _escape_ffmpeg_text
        assert "\\\\" in _escape_ffmpeg_text("back\\slash")
        assert "\\'" in _escape_ffmpeg_text("it's")
        assert "\\:" in _escape_ffmpeg_text("key:value")

    def test_get_scene_for_entry_none(self):
        """scenes_list가 None이면 None을 반환한다."""
        from ai_worker.renderer.layout import _get_scene_for_entry
        result = _get_scene_for_entry(
            {"type": "text_only", "sent_idx": 0},
            [{"text": "hello"}],
            None,
        )
        assert result is None

    def test_get_scene_for_entry_match(self):
        """텍스트가 일치하는 scene을 찾는다."""
        from unittest.mock import MagicMock
        from ai_worker.renderer.layout import _get_scene_for_entry

        scene = MagicMock()
        scene.text_lines = [{"text": "테스트 문장입니다"}]

        result = _get_scene_for_entry(
            {"type": "text_only", "sent_idx": 0},
            [{"text": "테스트 문장입니다"}],
            [scene],
        )
        assert result is scene

    def test_get_scene_for_entry_no_match(self):
        """텍스트가 일치하지 않으면 None을 반환한다."""
        from unittest.mock import MagicMock
        from ai_worker.renderer.layout import _get_scene_for_entry

        scene = MagicMock()
        scene.text_lines = [{"text": "다른 문장"}]

        result = _get_scene_for_entry(
            {"type": "text_only", "sent_idx": 0},
            [{"text": "전혀 다른 내용"}],
            [scene],
        )
        assert result is None


@requires_ffmpeg
class TestVideoRenderingFFmpeg:
    """FFmpeg 필요 — Docker 환경에서 실행."""

    def test_static_segment_generation(self):
        """정적 PNG를 mp4 세그먼트로 변환할 수 있다."""
        from ai_worker.renderer.layout import _render_static_segment

        frame_png = OUTPUT_DIR / "test_static_frame.png"
        _create_test_png(frame_png)

        segment_path = OUTPUT_DIR / "test_static_segment.mp4"
        result = _render_static_segment(frame_png, 2.0, segment_path)

        assert result.exists(), "세그먼트 파일이 생성되지 않음"
        assert result.stat().st_size > 0

    def test_video_segment_generation(self):
        """Mock 비디오 클립으로 video_segment를 생성할 수 있다."""
        from unittest.mock import MagicMock
        from PIL import Image
        from ai_worker.renderer.layout import _render_video_segment

        clip_path = OUTPUT_DIR / "test_clip.mp4"
        _create_test_video(clip_path, duration=3.0)

        base_frame = Image.new("RGB", (1080, 1920), color=(40, 40, 60))

        scene = MagicMock()
        scene.video_clip_path = str(clip_path)

        layout = {
            "canvas": {"width": 1080, "height": 1920},
            "scenes": {
                "video_text": {
                    "elements": {
                        "video_area": {"x": 90, "y": 550, "width": 900, "height": 900},
                        "text_area": {
                            "x": 540, "y": 1570, "font_size": 60,
                            "color": "#FFFFFF", "stroke_color": "#000000",
                            "stroke_width": 3,
                        },
                    }
                }
            },
        }

        font_dir = Path("assets/fonts")
        if not font_dir.exists():
            pytest.skip("assets/fonts 디렉터리 없음")

        segment_path = OUTPUT_DIR / "test_video_segment.mp4"
        result = _render_video_segment(
            base_frame=base_frame,
            scene=scene,
            text="테스트 자막",
            duration=2.0,
            layout=layout,
            font_dir=font_dir,
            output_path=segment_path,
        )
        assert result.exists()

    def test_hybrid_concat(self):
        """정적 세그먼트를 concat하면 하나의 mp4가 된다."""
        frame1 = OUTPUT_DIR / "concat_frame1.png"
        frame2 = OUTPUT_DIR / "concat_frame2.png"
        _create_test_png(frame1)
        _create_test_png(frame2)

        seg1 = OUTPUT_DIR / "concat_seg1.mp4"
        seg2 = OUTPUT_DIR / "concat_seg2.mp4"

        from ai_worker.renderer.layout import _render_static_segment
        _render_static_segment(frame1, 1.5, seg1)
        _render_static_segment(frame2, 1.5, seg2)

        concat_file = OUTPUT_DIR / "concat_test.txt"
        concat_file.write_text(
            f"file '{seg1.resolve()}'\nfile '{seg2.resolve()}'\n",
            encoding="utf-8",
        )

        merged = OUTPUT_DIR / "concat_merged.mp4"
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(merged),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"concat 실패: {result.stderr[:500]}"
        assert merged.exists()
        assert merged.stat().st_size > 0

    def test_segment_format_consistency(self):
        """세그먼트가 올바른 해상도/코덱을 갖는지 ffprobe로 확인한다."""
        if _FFPROBE is None:
            pytest.skip("ffprobe not available")

        frame = OUTPUT_DIR / "fmt_frame.png"
        _create_test_png(frame)

        seg = OUTPUT_DIR / "fmt_seg.mp4"
        from ai_worker.renderer.layout import _render_static_segment
        _render_static_segment(frame, 2.0, seg)

        probe = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height,codec_name,r_frame_rate",
             "-of", "json", str(seg)],
            capture_output=True, text=True, timeout=10,
        )
        assert probe.returncode == 0, "ffprobe 실행 실패"

        import json
        info = json.loads(probe.stdout)
        stream = info["streams"][0]
        assert stream["width"] == 1080
        assert stream["height"] == 1920
        assert stream["codec_name"] in ("h264", "hevc")


def generate_test_result():
    """테스트 결과 MD 파일 생성."""
    result_md = OUTPUT_DIR / "test_result.md"
    result_md.write_text("""# Video Rendering 테스트 결과

## 실행 환경
- Python: 3.12
- FFmpeg 필요 (FFmpeg 테스트), 불필요 (Unit 테스트)

## 실행 방법
```bash
# Unit 테스트 (FFmpeg 불필요)
python -m pytest test/test_video_rendering.py -v -k "Unit" --tb=short

# FFmpeg 테스트 (Docker 환경)
docker compose exec ai_worker python -m pytest test/test_video_rendering.py -v --tb=short
```

## 테스트 항목
| # | 테스트 함수 | FFmpeg 필요 | 설명 | 결과 |
|---|------------|:-----------:|------|------|
| 1 | test_escape_ffmpeg_text | N | 텍스트 이스케이프 | ⬜ |
| 2 | test_get_scene_for_entry_none | N | scenes_list=None 처리 | ⬜ |
| 3 | test_get_scene_for_entry_match | N | 씬 매칭 성공 | ⬜ |
| 4 | test_get_scene_for_entry_no_match | N | 씬 매칭 실패 | ⬜ |
| 5 | test_static_segment_generation | Y | 정적 PNG → mp4 | ⬜ |
| 6 | test_video_segment_generation | Y | 비디오 + 베이스 합성 | ⬜ |
| 7 | test_hybrid_concat | Y | 세그먼트 concat | ⬜ |
| 8 | test_segment_format_consistency | Y | 해상도/코덱 검증 | ⬜ |

## 수동 검증 체크리스트
- [ ] 영상 재생 확인
- [ ] 비디오 씬 배경 움직임 확인
- [ ] 오디오 동기화 확인
- [ ] 에러 로그 없음 확인
""", encoding="utf-8")


if __name__ == "__main__":
    generate_test_result()
    pytest.main([__file__, "-v"])
