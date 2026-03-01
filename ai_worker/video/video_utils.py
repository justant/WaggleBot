"""생성된 비디오 클립 후처리 유틸리티.

FFmpeg로 클립을 레이아웃에 맞게 리사이즈, 루프, FPS 정규화한다.

의존성 규칙:
- ai_worker.tts 모듈을 절대 import하지 않는다.
- subprocess + FFmpeg만 사용.
"""

import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def validate_frame_count(n: int) -> int:
    """프레임 수를 LTX-2의 1+8k 규칙에 맞는 가장 가까운 유효값으로 보정.

    LTX-2 프레임 규칙: 1 + 8k (9, 17, 25, 33, 41, 49, 57, 65, 73, 81, 89, 97, ...)
    """
    if n < 9:
        return 9
    k = round((n - 1) / 8)
    return 1 + 8 * k


def validate_resolution(width: int, height: int) -> tuple[int, int]:
    """해상도를 LTX-2가 요구하는 8의 배수 규칙에 맞게 보정.

    LTX-2 VAE는 내부적으로 32배수 latent를 사용하지만,
    입력 해상도는 8의 배수면 충분하다 (공식 권장: 1280×720, 1920×1080).
    """
    w = round(width / 8) * 8
    h = round(height / 8) * 8
    return max(w, 64), max(h, 64)


def _resolve_intermediate_codec() -> tuple[str, list[str]]:
    """중간 처리용 코덱 — RTX 3090 전용, 항상 h264_nvenc."""
    return "h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p1", "-pix_fmt", "yuv420p"]


def get_video_duration(path: Path) -> float:
    """ffprobe로 비디오 길이(초)를 반환."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def resize_clip_to_layout(
    input_path: Path,
    output_path: Path,
    width: int = 900,
    height: int = 900,
    fps: int = 24,
) -> Path:
    """비디오를 지정 크기로 center-crop + resize + FPS 정규화.

    layout.json의 video_area 크기(900x900)에 맞춘다.
    """
    _, enc_args = _resolve_intermediate_codec()

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"fps={fps}"
        ),
        *enc_args,
        "-an",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"resize_clip 실패: {result.stderr[-500:]}")
    logger.debug("[video_utils] resize: %s → %s (%dx%d)", input_path.name, output_path.name, width, height)
    return output_path


def loop_or_trim_clip(
    input_path: Path,
    output_path: Path,
    target_duration: float,
) -> Path:
    """클립 길이를 target_duration에 맞춘다.

    - 클립이 짧으면: stream_loop으로 루프
    - 클립이 길면: -t로 트림
    - 거의 같으면 (±0.2초): 그대로 복사
    """
    clip_dur = get_video_duration(input_path)
    diff = abs(clip_dur - target_duration)

    if diff <= 0.2:
        import shutil
        shutil.copy2(input_path, output_path)
    elif clip_dur < target_duration:
        import math
        loop_count = math.ceil(target_duration / clip_dur)
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", str(loop_count),
            "-i", str(input_path),
            "-t", f"{target_duration:.3f}",
            "-c", "copy",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"loop_clip 실패: {result.stderr[-500:]}")
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(input_path),
            "-t", f"{target_duration:.3f}",
            "-c", "copy",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"trim_clip 실패: {result.stderr[-500:]}")

    logger.debug(
        "[video_utils] loop_or_trim: %.2fs → %.2fs (%s)",
        clip_dur, target_duration, output_path.name,
    )
    return output_path


def normalize_clip_format(
    input_path: Path,
    output_path: Path,
    width: int = 1080,
    height: int = 1920,
    fps: int = 24,
) -> Path:
    """클립을 최종 출력 포맷으로 정규화.

    모든 세그먼트가 동일 포맷이어야 concat이 가능하다.
    - 코덱: h264 (yuv420p)
    - 해상도: 1080x1920 (9:16)
    - FPS: 24
    - 오디오: 없음 (별도 합성)
    """
    _, enc_args = _resolve_intermediate_codec()

    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}",
        *enc_args,
        "-an",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"normalize_clip 실패: {result.stderr[-500:]}")
    return output_path
