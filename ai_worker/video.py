"""영상 생성 모듈 – FFmpeg subprocess 직접 호출 방식.

TTS 오디오 + 이미지(또는 배경영상) + 자막 + BGM → 9:16 쇼츠 영상 렌더링.
"""
import logging
import random
import subprocess
import tempfile
import textwrap
from pathlib import Path

import requests
from PIL import Image

from config.settings import ASSETS_DIR, MEDIA_DIR

logger = logging.getLogger(__name__)

_VIDEO_DIR = MEDIA_DIR / "video"
_TEMP_DIR = MEDIA_DIR / "tmp"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def render_video(
    post,
    audio_path: Path,
    summary_text: str,
    cfg: dict[str, str],
) -> Path:
    """메인 진입점. 쇼츠 영상을 생성하고 경로를 반환한다."""
    _VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)

    resolution = cfg.get("video_resolution", "1080x1920")
    width, height = (int(v) for v in resolution.split("x"))
    codec = cfg.get("video_codec", "h264_nvenc")
    bgm_vol = float(cfg.get("bgm_volume", "0.15"))
    font_name = cfg.get("subtitle_font", "NanumGothic")
    font = _resolve_font_path(font_name)

    output_path = _VIDEO_DIR / f"post_{post.id}.mp4"
    audio_path = Path(audio_path)
    duration = _probe_duration(audio_path)

    images: list[str] = post.images or []
    if images:
        video_input = _build_slideshow(images, width, height, duration, post.id)
    else:
        video_input = _build_background_loop(width, height, duration)

    # ASS 자막 파일 생성 (ScriptData JSON이면 동적 자막, 평문이면 drawtext 폴백)
    ass_path: Path | None = None
    try:
        from ai_worker.llm import ScriptData
        from ai_worker.subtitle import write_ass_file
        script = ScriptData.from_json(summary_text)
        ass_path = _TEMP_DIR / f"sub_{post.id}.ass"
        write_ass_file(
            hook=script.hook,
            body=script.body,
            closer=script.closer,
            duration=duration,
            mood=script.mood,
            fontname=font_name,
            output_path=ass_path,
            width=width,
            height=height,
        )
    except Exception:
        logger.warning("ASS 자막 생성 실패 → drawtext 폴백", exc_info=True)

    _compose_final(
        video_input=video_input,
        audio_path=audio_path,
        output_path=output_path,
        summary_text=summary_text,
        ass_path=ass_path,
        duration=duration,
        width=width,
        height=height,
        codec=codec,
        font=font,
        bgm_vol=bgm_vol,
    )

    # 임시 파일 정리
    if video_input.parent == _TEMP_DIR:
        video_input.unlink(missing_ok=True)
    if ass_path:
        ass_path.unlink(missing_ok=True)

    logger.info("영상 생성 완료: %s (%.1f초)", output_path.name, duration)
    return output_path


# ---------------------------------------------------------------------------
# 오디오 길이 측정
# ---------------------------------------------------------------------------
def _probe_duration(audio_path: Path) -> float:
    """ffprobe로 오디오 길이(초)를 반환한다."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


# ---------------------------------------------------------------------------
# 이미지 슬라이드쇼 (Ken Burns + xfade 장면 전환)
# ---------------------------------------------------------------------------
def _build_slideshow(
    image_urls: list[str],
    width: int,
    height: int,
    duration: float,
    post_id: int,
    transitions: list[str] | None = None,
) -> Path:
    """이미지 다운로드 → 리사이즈 → Ken Burns zoompan + xfade 장면 전환 슬라이드쇼 생성."""
    img_dir = _TEMP_DIR / f"imgs_{post_id}"
    img_dir.mkdir(parents=True, exist_ok=True)

    local_imgs: list[Path] = []
    for idx, url in enumerate(image_urls[:10]):  # 최대 10장
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning("이미지 다운로드 실패: %s", url)
            continue

        img_path = img_dir / f"{idx:03d}.jpg"
        img_path.write_bytes(resp.content)
        _resize_cover(img_path, width, height)
        local_imgs.append(img_path)

    if not local_imgs:
        logger.warning("다운로드 성공 이미지 없음 → 배경영상 폴백")
        return _build_background_loop(width, height, duration)

    n = len(local_imgs)
    fps = 30
    trans_dur = 0.5  # 장면 전환 지속 시간 (초)

    # 전환 중첩 시간을 보정해 총 출력 길이 = duration 유지
    # 총길이 = n * seg_dur - (n-1) * trans_dur → seg_dur = (duration + (n-1)*trans_dur) / n
    seg_dur = (duration + (n - 1) * trans_dur) / n if n > 1 else duration
    assigned = _assign_transitions(n, transitions)

    output = _TEMP_DIR / f"slideshow_{post_id}.mp4"

    # 이미지별 -loop 1 -t {seg_dur} 입력
    cmd: list[str] = ["ffmpeg", "-y"]
    for img in local_imgs:
        cmd += ["-loop", "1", "-t", f"{seg_dur:.3f}", "-i", str(img)]

    # filter_complex 구성
    filter_parts: list[str] = []

    # 각 이미지에 zoompan 적용 (짝수: 줌인, 홀수: 줌아웃으로 시각 변화)
    for i in range(n):
        d_frames = max(1, int(seg_dur * fps))
        if i % 2 == 0:
            zp = (
                f"zoompan=z='min(zoom+0.0015,1.3)'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={d_frames}:s={width}x{height}:fps={fps}"
            )
        else:
            zp = (
                f"zoompan=z='if(lte(zoom,1.0),1.3,max(zoom-0.0015,1.0))'"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                f":d={d_frames}:s={width}x{height}:fps={fps}"
            )
        filter_parts.append(f"[{i}:v]{zp},setpts=PTS-STARTPTS[v{i}]")

    # xfade 체이닝: offset = (i+1) * (seg_dur - trans_dur)
    if n > 1:
        prev_label = "v0"
        for i in range(n - 1):
            offset = (i + 1) * (seg_dur - trans_dur)
            t = assigned[i]
            out_label = "vout" if i == n - 2 else f"x{i}"
            filter_parts.append(
                f"[{prev_label}][v{i + 1}]xfade=transition={t}"
                f":duration={trans_dur:.3f}:offset={offset:.3f}[{out_label}]"
            )
            prev_label = out_label
        final_label = "vout"
    else:
        final_label = "v0"

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{final_label}]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        str(output),
    ]

    subprocess.run(cmd, capture_output=True, text=True, check=True)

    # 임시 파일 정리
    for img in local_imgs:
        img.unlink(missing_ok=True)
    img_dir.rmdir()

    return output


def _assign_transitions(n: int, custom: list[str] | None) -> list[str]:
    """N개 이미지에 대한 N-1개 장면 전환 효과를 결정한다.

    전환 전략:
    - 첫 전환 (hook → body): circleopen — 주의 집중
    - 마지막 전환 (body → closer): fadeblack — 마무리
    - 중간 전환: slideleft / dissolve / fade 순환
    """
    n_trans = n - 1
    if n_trans <= 0:
        return []
    if custom and len(custom) == n_trans:
        return custom

    _MID_CYCLE = ("slideleft", "dissolve", "fade")
    result: list[str] = []
    mid_idx = 0
    for i in range(n_trans):
        if n_trans == 1:
            result.append("circleopen")
        elif i == 0:
            result.append("circleopen")
        elif i == n_trans - 1:
            result.append("fadeblack")
        else:
            result.append(_MID_CYCLE[mid_idx % len(_MID_CYCLE)])
            mid_idx += 1
    return result


def _resize_cover(img_path: Path, target_w: int, target_h: int) -> None:
    """이미지를 target 비율로 crop+resize (cover 모드)."""
    with Image.open(img_path) as img:
        img_ratio = img.width / img.height
        target_ratio = target_w / target_h

        if img_ratio > target_ratio:
            new_w = int(img.height * target_ratio)
            left = (img.width - new_w) // 2
            img = img.crop((left, 0, left + new_w, img.height))
        else:
            new_h = int(img.width / target_ratio)
            top = (img.height - new_h) // 2
            img = img.crop((0, top, img.width, top + new_h))

        img = img.resize((target_w, target_h), Image.LANCZOS)
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(img_path, "JPEG", quality=90)


# ---------------------------------------------------------------------------
# 배경영상 루프
# ---------------------------------------------------------------------------
def _build_background_loop(width: int, height: int, duration: float) -> Path:
    """assets/backgrounds/에서 랜덤 영상을 골라 duration만큼 loop."""
    bg_dir = ASSETS_DIR / "backgrounds"
    candidates = list(bg_dir.glob("*.mp4")) + list(bg_dir.glob("*.webm"))

    if not candidates:
        # 배경영상 없으면 검은 화면 생성
        logger.warning("배경영상 없음 → 검은 화면 생성")
        output = _TEMP_DIR / "black_bg.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={width}x{height}:r=30:d={duration:.3f}",
                "-c:v", "libx264", "-preset", "fast",
                "-pix_fmt", "yuv420p",
                str(output),
            ],
            capture_output=True, text=True, check=True,
        )
        return output

    bg = random.choice(candidates)
    output = _TEMP_DIR / "bg_loop.mp4"

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-stream_loop", "-1", "-i", str(bg),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                   f"crop={width}:{height}",
            "-t", f"{duration:.3f}",
            "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            str(output),
        ],
        capture_output=True, text=True, check=True,
    )
    return output


# ---------------------------------------------------------------------------
# 최종 합성 (영상 + 오디오 + 자막 + BGM)
# ---------------------------------------------------------------------------
def _compose_final(
    *,
    video_input: Path,
    audio_path: Path,
    output_path: Path,
    summary_text: str,
    ass_path: "Path | None",
    duration: float,
    width: int,
    height: int,
    codec: str,
    font: str,
    bgm_vol: float,
) -> None:
    """영상 + TTS 오디오 + 자막(ASS 우선 / drawtext 폴백) + BGM을 합성한다."""

    if ass_path and ass_path.exists():
        # ASS 동적 자막 필터
        # fontsdir: assets/fonts/ 또는 폰트 파일 부모 디렉터리
        font_dir = str(Path(font).parent) if font else str(Path(__file__).parent.parent / "assets" / "fonts")
        ass_str      = str(ass_path).replace("\\", "/")
        font_dir_str = font_dir.replace("\\", "/")
        video_filter = (
            f"[0:v]subtitles=filename='{ass_str}'"
            f":fontsdir='{font_dir_str}'[v]"
        )
        logger.debug("ASS 자막 필터 사용: %s", ass_path.name)
    else:
        # Legacy drawtext 폴백
        clean_text = _strip_markdown(summary_text)
        wrapped    = _wrap_korean(clean_text, width=18)
        escaped    = _escape_drawtext(wrapped)
        fontsize   = int(width * 0.042)   # ~45px @1080w
        fontfile_opt = f":fontfile={font}" if font else ""
        video_filter = (
            f"[0:v]drawtext=text='{escaped}'"
            f"{fontfile_opt}"
            f":fontsize={fontsize}"
            f":fontcolor=white"
            f":borderw=3:bordercolor=black"
            f":box=1:boxcolor=black@0.5:boxborderw=10"
            f":x=(w-text_w)/2:y=h*0.75"
            f":line_spacing=12[v]"
        )
        logger.debug("drawtext 폴백 자막 사용")

    # BGM 믹싱
    bgm_files = list((ASSETS_DIR / "bgm").glob("*.mp3")) + \
                list((ASSETS_DIR / "bgm").glob("*.wav"))

    inputs = ["-i", str(video_input), "-i", str(audio_path)]
    if bgm_files:
        bgm = random.choice(bgm_files)
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        # sidechaincompress: TTS가 나올 때 BGM 볼륨 자동 감소 (auto-ducking)
        audio_filter = (
            f"[1:a]apad[tts];"
            f"[2:a]volume={bgm_vol}[bgm];"
            f"[bgm][tts]sidechaincompress="
            f"threshold=0.02:ratio=6:attack=200:release=1000[ducked];"
            f"[tts][ducked]amix=inputs=2:duration=first[aout]"
        )
        audio_map = ["-map", "[aout]"]
    else:
        audio_filter = "[1:a]apad[aout]"
        audio_map = ["-map", "[aout]"]

    # NVENC 폴백
    enc_args = _get_encoder_args(codec)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", f"{video_filter};{audio_filter}",
        "-map", "[v]",
        *audio_map,
        *enc_args,
        "-r", "30",
        "-t", f"{duration:.3f}",
        "-shortest",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 and codec == "h264_nvenc":
        logger.warning("NVENC 실패 → libx264 폴백: %s", result.stderr[-200:])
        enc_args = _get_encoder_args("libx264")
        cmd = [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", f"{video_filter};{audio_filter}",
            "-map", "[v]",
            *audio_map,
            *enc_args,
            "-r", "30",
            "-t", f"{duration:.3f}",
            "-shortest",
            str(output_path),
        ]
        subprocess.run(cmd, capture_output=True, text=True, check=True)
    elif result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )


def _get_encoder_args(codec: str) -> list[str]:
    """코덱별 인코딩 인자 반환."""
    if codec == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-preset", "p4",
            "-rc", "vbr",
            "-cq", "23",
            "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
    ]


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------
def _resolve_font_path(font_name: str) -> str:
    """폰트 이름으로 절대 경로를 반환한다.

    탐색 순서:
    1. assets/fonts/
    2. /usr/share/fonts/ 하위 재귀 탐색 (시스템 폰트)
    """
    if not font_name.endswith((".ttf", ".otf")):
        font_name = f"{font_name}.ttf"

    # 1. assets/fonts/
    font_path = ASSETS_DIR / "fonts" / font_name
    if font_path.exists():
        return str(font_path)

    # 2. 시스템 폰트 경로 탐색
    system_font_dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts")]
    for font_dir in system_font_dirs:
        for found in font_dir.rglob(font_name):
            logger.info("시스템 폰트 사용: %s", found)
            return str(found)

    logger.warning("폰트 파일 없음: %s → FFmpeg 기본 폰트 사용", font_name)
    return ""


def _strip_markdown(text: str) -> str:
    """LLM이 출력한 마크다운 서식 제거."""
    import re
    text = re.sub(r"\*{1,3}(.+?)\*{1,3}", r"\1", text, flags=re.DOTALL)  # **bold**, *italic*
    text = re.sub(r"#{1,6}\s*", "", text)                   # # 헤딩
    text = re.sub(r"^-{3,}\s*$", "", text, flags=re.MULTILINE)  # --- 수평선
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)   # [link](url)
    text = re.sub(r"\[([^\]]*)\]", r"\1", text)             # [label]
    text = re.sub(r"`{1,3}(.+?)`{1,3}", r"\1", text)       # `code`
    text = re.sub(r"^\s*[-*>]\s+", "", text, flags=re.MULTILINE)  # 목록/인용
    text = re.sub(r"\n{3,}", "\n\n", text)                  # 연속 공백 줄 정리
    return text.strip()


def _escape_drawtext(text: str) -> str:
    """FFmpeg drawtext 필터용 텍스트 이스케이프."""
    for ch in ("\\", "'", ":", ";", "%", "{", "}", '"'):
        text = text.replace(ch, f"\\{ch}")
    # 개행 → drawtext 줄바꿈
    text = text.replace("\n", "\\n")
    return text


def _wrap_korean(text: str, width: int = 18) -> str:
    """한국어 텍스트를 지정 폭으로 줄바꿈."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        wrapped = textwrap.fill(paragraph, width=width)
        lines.append(wrapped)
    return "\n".join(lines)
