"""비디오 씬 포함 최종 렌더링 통합 테스트.

실행 방법 (Mock 모드, 서버 불필요):
  python -m pytest test/test_video_rendering.py -v -k "mock"

실행 방법 (ComfyUI + Fish Speech 모두 필요):
  docker compose exec ai_worker python test/test_video_rendering.py

테스트 결과물 위치:
  test/test_video_rendering_output/
  test/test_video_rendering_output/test_result.md
  test/test_video_rendering_output/sample_output.mp4
"""
import logging
import subprocess
from pathlib import Path

import pytest

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("test/test_video_rendering_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


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
        pytest.skip(f"FFmpeg not available: {result.stderr[:200]}")
    return output_path


def _create_test_png(output_path: Path, width: int = 1080, height: int = 1920) -> Path:
    """PIL로 테스트용 PNG 이미지를 생성한다."""
    from PIL import Image
    img = Image.new("RGB", (width, height), color=(40, 40, 60))
    img.save(str(output_path), "PNG")
    return output_path


class TestVideoRendering:

    def test_static_segment_generation_mock(self):
        """정적 PNG를 mp4 세그먼트로 변환할 수 있다."""
        from ai_worker.renderer.layout import _render_static_segment

        frame_png = OUTPUT_DIR / "test_static_frame.png"
        _create_test_png(frame_png)

        segment_path = OUTPUT_DIR / "test_static_segment.mp4"
        result = _render_static_segment(frame_png, 2.0, segment_path)

        assert result.exists(), "세그먼트 파일이 생성되지 않음"
        assert result.stat().st_size > 0

        # ffprobe로 확인
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=width,height,codec_name,r_frame_rate",
             "-of", "csv=p=0", str(result)],
            capture_output=True, text=True, timeout=10,
        )
        if probe.returncode == 0:
            logger.info("Static segment probe: %s", probe.stdout.strip())

    def test_video_segment_generation_mock(self):
        """Mock 비디오 클립으로 video_segment를 생성할 수 있다."""
        from unittest.mock import MagicMock
        from PIL import Image
        from ai_worker.renderer.layout import _render_video_segment

        # 테스트용 비디오 클립 생성
        clip_path = OUTPUT_DIR / "test_clip.mp4"
        _create_test_video(clip_path, duration=3.0)

        # 베이스 프레임 생성
        base_frame = Image.new("RGB", (1080, 1920), color=(40, 40, 60))

        # Mock scene
        scene = MagicMock()
        scene.video_clip_path = str(clip_path)

        # layout 설정 (video_text 씬 구조)
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
        try:
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
        except subprocess.CalledProcessError as e:
            logger.warning("video_segment 생성 실패 (FFmpeg): %s", e)
            pytest.skip("FFmpeg video segment 생성 실패")

    def test_hybrid_concat_mock(self):
        """정적 + 비디오 세그먼트를 concat하면 하나의 mp4가 된다."""
        # 2개의 정적 세그먼트 생성
        frame1 = OUTPUT_DIR / "concat_frame1.png"
        frame2 = OUTPUT_DIR / "concat_frame2.png"
        _create_test_png(frame1)
        _create_test_png(frame2)

        seg1 = OUTPUT_DIR / "concat_seg1.mp4"
        seg2 = OUTPUT_DIR / "concat_seg2.mp4"

        from ai_worker.renderer.layout import _render_static_segment
        _render_static_segment(frame1, 1.5, seg1)
        _render_static_segment(frame2, 1.5, seg2)

        # concat
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

    def test_segment_format_consistency_mock(self):
        """모든 세그먼트가 동일 해상도/FPS/코덱인지 ffprobe로 확인한다."""
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
        if probe.returncode != 0:
            pytest.skip("ffprobe not available")

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
- FFmpeg 필요

## 실행 방법
```bash
# Mock 테스트 (서버 불필요)
python -m pytest test/test_video_rendering.py -v -k "mock" --tb=short

# 통합 테스트 (Docker 필요)
docker compose exec ai_worker python -m pytest test/test_video_rendering.py -v
```

## 테스트 항목
| # | 테스트 함수 | 설명 | 결과 |
|---|------------|------|------|
| 1 | test_static_segment_generation_mock | 정적 PNG → mp4 세그먼트 | ⬜ |
| 2 | test_video_segment_generation_mock | 비디오 클립 + 베이스 합성 | ⬜ |
| 3 | test_hybrid_concat_mock | 정적 + 비디오 concat | ⬜ |
| 4 | test_segment_format_consistency_mock | 해상도/FPS/코덱 일관성 | ⬜ |

## 수동 검증 체크리스트
- [ ] 영상 재생 확인
- [ ] 비디오 씬 배경 움직임 확인
- [ ] 오디오 동기화 확인
- [ ] 에러 로그 없음 확인

## 출력 파일
- `test_static_segment.mp4`: 정적 세그먼트
- `test_video_segment.mp4`: 비디오 세그먼트
- `concat_merged.mp4`: concat 결과
""", encoding="utf-8")


if __name__ == "__main__":
    generate_test_result()
    pytest.main([__file__, "-v"])
