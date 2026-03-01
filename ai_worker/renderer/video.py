"""영상 생성 모듈 – FFmpeg subprocess 직접 호출 방식.

TTS 오디오 + 이미지(또는 배경영상) + 자막 + BGM → 9:16 쇼츠 영상 렌더링.
"""
import hashlib
import logging
import random
import shutil
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image

from config.settings import ASSETS_DIR, MEDIA_DIR

logger = logging.getLogger(__name__)

_VIDEO_DIR = MEDIA_DIR / "video"
_TEMP_DIR = MEDIA_DIR / "tmp"
_IMG_CACHE_DIR = MEDIA_DIR / "tmp" / "img_cache"  # 이미지 리사이즈 캐시

# NVENC 가용 여부 캐시 (None = 미확인)
_nvenc_available: bool | None = None


def _check_nvenc() -> bool:
    """h264_nvenc 인코더 실제 동작 여부를 한 번만 확인하고 캐시."""
    global _nvenc_available
    if _nvenc_available is not None:
        return _nvenc_available
    probe = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "nullsrc=s=64x64:d=0.1",
            "-c:v", "h264_nvenc", "-f", "null", "-",
        ],
        capture_output=True,
    )
    _nvenc_available = probe.returncode == 0
    if _nvenc_available:
        logger.info("NVENC 사용 가능 — h264_nvenc 인코딩 활성화")
    else:
        logger.error("NVENC 사용 불가 — RTX 3090 GPU 환경 필수 (NVIDIA 드라이버 확인 필요)")
    return _nvenc_available


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def render_preview(
    post,
    audio_path: Path,
    summary_text: str,
    cfg: dict[str, str],
) -> Path:
    """프리뷰 전용 렌더링 — 항상 480×854 libx264(CPU), GPU 점유 없음.

    고화질 렌더링(`render_video`) 전 운영자 확인용 저화질 영상 생성.
    출력 파일: media/video/{site_code}/post_{origin_id}_SD.mp4
    """
    _site_video_dir = _VIDEO_DIR / post.site_code
    _site_video_dir.mkdir(parents=True, exist_ok=True)
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)

    width, height = 480, 854
    codec = "libx264"
    bgm_vol = float(cfg.get("bgm_volume", "0.15"))
    font_name = cfg.get("subtitle_font", "NanumGothic")
    font = _resolve_font_path(font_name)

    output_path = _site_video_dir / f"post_{post.origin_id}_SD.mp4"
    audio_path = Path(audio_path)
    duration = _probe_duration(audio_path)

    images: list[str] = post.images or []
    if images:
        video_input = _build_slideshow(images, width, height, duration, post.id, codec=codec)
    else:
        video_input = _build_background_loop(width, height, duration, codec=codec)

    ass_path: Path | None = None
    comment_timings: list[tuple[float, float]] = []
    try:
        from ai_worker.llm.client import ScriptData
        from ai_worker.renderer.subtitle import get_comment_timings, write_ass_file
        script = ScriptData.from_json(summary_text)
        ass_path = _TEMP_DIR / f"sub_preview_{post.id}.ass"
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
        comment_timings = get_comment_timings(
            hook=script.hook,
            body=script.body,
            closer=script.closer,
            duration=duration,
        )
    except Exception:
        logger.warning("프리뷰 ASS 자막 생성 실패 → drawtext 폴백", exc_info=True)

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
        comment_timings=comment_timings,
        post_id=post.id,
    )

    if video_input.parent == _TEMP_DIR:
        video_input.unlink(missing_ok=True)
    if ass_path:
        ass_path.unlink(missing_ok=True)
    dt_text_file = _TEMP_DIR / f"drawtext_{output_path.stem}.txt"
    dt_text_file.unlink(missing_ok=True)

    logger.info("프리뷰 생성 완료: %s (%.1f초)", output_path.name, duration)
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
    codec: str = "libx264",
) -> Path:
    """이미지 다운로드 → 리사이즈 → Ken Burns zoompan + xfade 장면 전환 슬라이드쇼 생성."""
    img_dir = _TEMP_DIR / f"imgs_{post_id}"
    img_dir.mkdir(parents=True, exist_ok=True)

    _IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local_imgs: list[Path] = []
    _skipped = 0
    for idx, url in enumerate(image_urls):
        url_hash = hashlib.md5(url.encode()).hexdigest()
        cache_key = f"{url_hash}_{width}x{height}.jpg"
        cached_path = _IMG_CACHE_DIR / cache_key
        img_path = img_dir / f"{idx:03d}.jpg"

        if cached_path.exists():
            shutil.copy2(cached_path, img_path)
            local_imgs.append(img_path)
            logger.debug("이미지 캐시 히트: %s…", url[:60])
            continue

        raw_data = _download_image_with_retry(url, max_retries=2)
        if raw_data is None:
            _skipped += 1
            continue

        img_path.write_bytes(raw_data)
        try:
            _resize_cover(img_path, width, height)
            shutil.copy2(img_path, cached_path)  # 캐시 저장
        except Exception:
            logger.warning("이미지 처리 실패 (손상/비이미지 응답), 건너뜀: %s", url)
            img_path.unlink(missing_ok=True)
            _skipped += 1
            continue
        local_imgs.append(img_path)

    if _skipped:
        logger.warning(
            "이미지 %d/%d장 다운로드 실패 (post_id=%d)",
            _skipped, len(image_urls), post_id,
        )

    if not local_imgs:
        logger.warning("다운로드 성공 이미지 없음 → 배경영상 폴백")
        return _build_background_loop(width, height, duration, codec=codec)

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
        filter_parts.append(f"[{i}:v]{zp},setpts=PTS-STARTPTS,fps={fps}[v{i}]")

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
        *_get_encoder_args(codec),
        str(output),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("슬라이드쇼 렌더링 실패:\n%s", result.stderr[-2000:])
        raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)

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


_video_dc_session: requests.Session | None = None


def _get_dc_session() -> requests.Session:
    """DCInside 이미지 다운로드용 세션 (쿠키 워밍업 포함)."""
    global _video_dc_session
    if _video_dc_session is not None:
        return _video_dc_session
    _video_dc_session = requests.Session()
    _video_dc_session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    })
    try:
        _video_dc_session.get("https://www.dcinside.com/", timeout=10)
        logger.debug("video DC 세션 워밍업 OK (cookies=%d)",
                     len(_video_dc_session.cookies))
    except Exception:
        logger.debug("video DC 세션 워밍업 실패 — 쿠키 없이 시도")
    return _video_dc_session


def _is_dc_url(url: str) -> bool:
    """DCInside CDN URL 여부 확인."""
    hostname = urlparse(url).hostname or ""
    return any(hostname.endswith(d) for d in ("dcinside.com", "dcinside.co.kr"))


def _download_image_with_retry(
    url: str, max_retries: int = 2, min_size: int = 200,
) -> bytes | None:
    """이미지를 다운로드하고 재시도 로직을 적용한다.

    Args:
        url: 이미지 URL
        max_retries: 최대 재시도 횟수 (0이면 1회만 시도)
        min_size: 플레이스홀더 판별 최소 바이트 수

    Returns:
        이미지 바이트 데이터. 실패 시 None.
    """
    for attempt in range(max_retries + 1):
        try:
            if _is_dc_url(url):
                sess = _get_dc_session()
                resp = sess.get(url, timeout=15, headers={
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
                    "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
                    "Accept": "image/*,*/*;q=0.8",
                }
                resp = requests.get(url, timeout=15, headers=_hdrs)
            resp.raise_for_status()
            if len(resp.content) < min_size:
                logger.warning(
                    "이미지 크기 의심 (%d bytes, 플레이스홀더?): %s",
                    len(resp.content), url,
                )
                return None
            return resp.content
        except requests.RequestException:
            if attempt < max_retries:
                time.sleep(1 * (attempt + 1))
                logger.debug("이미지 다운로드 재시도 (%d/%d): %s", attempt + 1, max_retries, url)
            else:
                logger.warning("이미지 다운로드 실패 (재시도 %d회 후): %s", max_retries, url)
    return None


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
def _build_background_loop(width: int, height: int, duration: float, codec: str = "libx264") -> Path:
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
                *_get_encoder_args(codec),
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
            *_get_encoder_args(codec),
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
    comment_timings: "list[tuple[float, float]] | None" = None,
    post_id: int | None = None,
) -> None:
    """영상 + TTS 오디오 + 자막(ASS 우선 / drawtext 폴백) + BGM + 댓글 효과를 합성한다."""

    timings = comment_timings or []
    shake_vf = _build_shake_filter(timings)

    sub_filter: str = ""
    dt_filter: str = ""

    if ass_path and ass_path.exists():
        # ASS 동적 자막 필터
        font_dir = str(Path(font).parent) if font else str(Path(__file__).parent.parent.parent / "assets" / "fonts")
        ass_str      = str(ass_path).replace("\\", "/")
        font_dir_str = font_dir.replace("\\", "/")
        sub_filter = (
            f"[0:v]subtitles=filename='{ass_str}':fontsdir='{font_dir_str}'"
        )
        if shake_vf:
            video_filter = f"{sub_filter}[sub];[sub]{shake_vf}[v]"
        else:
            video_filter = f"{sub_filter}[v]"
        logger.debug("ASS 자막 필터 사용: %s (shake=%s)", ass_path.name, bool(shake_vf))
    else:
        # Legacy drawtext 폴백 — textfile= 옵션으로 이스케이프 문제 우회
        clean_text = _strip_markdown(summary_text)
        wrapped    = _wrap_korean(clean_text, width=18)
        text_file  = _TEMP_DIR / f"drawtext_{output_path.stem}.txt"
        text_file.write_text(wrapped, encoding="utf-8")
        fontsize   = int(width * 0.042)   # ~45px @1080w
        fontfile_opt = f":fontfile={font}" if font else ""
        dt_filter = (
            f"[0:v]drawtext=textfile='{text_file}'"
            f"{fontfile_opt}"
            f":fontsize={fontsize}"
            f":fontcolor=white"
            f":borderw=3:bordercolor=black"
            f":box=1:boxcolor=black@0.5:boxborderw=10"
            f":x=(w-text_w)/2:y=h*0.75"
            f":line_spacing=12"
        )
        if shake_vf:
            video_filter = f"{dt_filter}[sub];[sub]{shake_vf}[v]"
        else:
            video_filter = f"{dt_filter}[v]"
        logger.debug("drawtext 폴백 자막 사용 (textfile=%s)", text_file.name)

    # BGM 믹싱
    bgm_files = list((ASSETS_DIR / "bgm").glob("*.mp3")) + \
                list((ASSETS_DIR / "bgm").glob("*.wav"))

    inputs = ["-i", str(video_input), "-i", str(audio_path)]
    if bgm_files:
        bgm = random.Random(post_id or 0).choice(bgm_files)  # post마다 동일 BGM 재사용
        inputs += ["-stream_loop", "-1", "-i", str(bgm)]
        # sidechaincompress: TTS가 나올 때 BGM 볼륨 자동 감소 (auto-ducking)
        audio_filter = (
            f"[1:a]apad[tts];"
            f"[2:a]volume={bgm_vol}[bgm];"
            f"[bgm][tts]sidechaincompress="
            f"threshold=0.02:ratio=6:attack=200:release=1000[ducked];"
            f"[tts][ducked]amix=inputs=2:duration=first[premix]"
        )
        n_base_inputs = 3  # video + tts + bgm
    else:
        audio_filter = "[1:a]apad[premix]"
        n_base_inputs = 2  # video + tts

    # 댓글 효과음 믹싱 (assets/sfx/comment_ding.mp3 존재 시)
    sfx_extra_inputs, sfx_filter_parts = _build_sfx_parts(timings, n_base_inputs)
    if sfx_filter_parts:
        n_sfx = len(sfx_filter_parts)
        sfx_labels = "".join(f"[sfx{i}]" for i in range(n_sfx))
        audio_filter += (
            ";" + ";".join(sfx_filter_parts)
            + f";[premix]{sfx_labels}amix=inputs={1 + n_sfx}:duration=first[aout]"
        )
        inputs += sfx_extra_inputs
        logger.debug("댓글 효과음 %d회 믹싱", n_sfx)
    else:
        audio_filter = audio_filter.replace("[premix]", "[aout]")

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

    if result.returncode != 0:
        # geq 흔들림 필터가 포함된 경우 → 제거 후 재시도
        if shake_vf:
            logger.warning(
                "geq 흔들림 필터 실패 (코드=%d) → 흔들림 없이 재시도", result.returncode
            )
            fc_orig = f"{video_filter};{audio_filter}"
            # video_filter에서 shake 체인 제거: "[sub]{shake}[v]" → "[v]"
            # shake가 없는 video_filter를 재구성
            if ass_path and ass_path.exists():
                vf_no_shake = f"{sub_filter}[v]"
            else:
                vf_no_shake = f"{dt_filter}[v]"
            fc_no_shake = f"{vf_no_shake};{audio_filter}"
            cmd_no_shake = [fc_no_shake if c == fc_orig else c for c in cmd]
            result2 = subprocess.run(cmd_no_shake, capture_output=True, text=True)
            if result2.returncode == 0:
                logger.info("흔들림 없는 재시도 성공")
                return
            logger.error("흔들림 없는 재시도도 실패:\n%s", result2.stderr[-2000:])
            raise subprocess.CalledProcessError(
                result2.returncode, cmd_no_shake, result2.stdout, result2.stderr
            )
        logger.error("_compose_final 실패:\n%s", result.stderr[-2000:])
        raise subprocess.CalledProcessError(
            result.returncode, cmd, result.stdout, result.stderr
        )


def _build_shake_filter(comment_timings: list[tuple[float, float]]) -> str:
    """댓글 인용 구간 시작 0.3초 동안 1~2px 수평 흔들림 geq 필터를 반환한다.

    댓글이 없으면 빈 문자열을 반환한다.
    """
    if not comment_timings:
        return ""

    shake_dur = 0.3
    # between(T, t1, t2) → 1 if t1 ≤ T ≤ t2, else 0
    # geq eval에서 공백 없이 작성 (파싱 안정성)
    intervals = "+".join(
        f"gte(T,{t1:.3f})*lte(T,{min(t1 + shake_dur, t2):.3f})"
        for t1, t2 in comment_timings
    )
    # 2px 사인파 흔들림 — 댓글 구간에서만 활성
    dx = f"(({intervals})*(2*sin(T*30*PI)))"
    return (
        f"geq=lum='lum(X-{dx},Y)'"
        f":cb='cb(X-{dx},Y)'"
        f":cr='cr(X-{dx},Y)'"
    )


def _build_sfx_parts(
    comment_timings: list[tuple[float, float]],
    n_base_inputs: int,
) -> tuple[list[str], list[str]]:
    """댓글 시작 시점 각각에 효과음을 재생하는 FFmpeg 입력 목록과 필터 파트를 반환한다.

    assets/sfx/comment_ding.mp3 가 없으면 ([], []) 반환한다.
    """
    sfx_path = ASSETS_DIR / "sfx" / "comment_ding.mp3"
    if not sfx_path.exists() or not comment_timings:
        return [], []

    extra_inputs: list[str] = []
    filter_parts: list[str] = []

    for i, (t_start, _) in enumerate(comment_timings):
        delay_ms = int(t_start * 1000)
        idx = n_base_inputs + i
        extra_inputs += ["-i", str(sfx_path)]
        filter_parts.append(
            f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume=0.4[sfx{i}]"
        )

    return extra_inputs, filter_parts


def _get_encoder_args(codec: str) -> list[str]:
    """코덱별 인코딩 인자 반환."""
    if codec == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc",
            "-preset", "medium",
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
