"""썰 렌더러 — 누적 텍스트 + 효과음 쇼츠 영상 생성.

기존 Ken Burns 슬라이드쇼 방식의 대안으로, 인터넷 밈 '썰 읽어주는 쇼츠' 스타일을
구현한다. 문장 단위 TTS + PIL 누적 텍스트 프레임 + 효과음 amix.

파이프라인:
  ScriptData → 문장 분리 → TTS 청크 생성 → PIL 프레임 생성 → FFmpeg concat/amix → mp4
"""
import asyncio
import json
import logging
import random
import shutil
import subprocess
import textwrap
from pathlib import Path

import edge_tts
from PIL import Image, ImageDraw, ImageFont

from config.settings import ASSETS_DIR, MEDIA_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 레이아웃 상수 (1116×2000 캔버스 기준 — FFmpeg가 1080×1920으로 리사이즈)
# ---------------------------------------------------------------------------
CANVAS_W = 1116
CANVAS_H = 2000

TEXT_X = 99
TEXT_X_RIGHT = 1016
TEXT_Y_TITLE = 265
TEXT_Y_SEP = 330
TEXT_Y_META = 340
TEXT_Y_BODY_START = 390
TEXT_Y_OVERFLOW_MAX = 1820

TITLE_FONT_SIZE = 46
BODY_FONT_SIZE = 46
COMMENT_FONT_SIZE = 40
META_FONT_SIZE = 28

LINE_HEIGHT = int(BODY_FONT_SIZE * 1.45)          # ≈ 67px
SENTENCE_GAP = int(LINE_HEIGHT * 0.6)             # ≈ 40px
COMMENT_LINE_HEIGHT = int(COMMENT_FONT_SIZE * 1.45)  # ≈ 58px
COMMENT_INDENT = 30
WRAP_WIDTH = 20   # 한 줄 최대 글자 수 (레거시 — pixel wrapper로 대체)
MAX_TEXT_WIDTH = TEXT_X_RIGHT - TEXT_X  # 917px


# ---------------------------------------------------------------------------
# 폰트 로더
# ---------------------------------------------------------------------------

def _load_font(font_dir: Path, filename: str, size: int) -> ImageFont.FreeTypeFont:
    """폰트 파일 로드. 없으면 시스템 한글 폰트 → PIL 기본 폰트 순으로 fallback."""
    # 1. assets/fonts/ 탐색
    font_path = font_dir / filename
    if font_path.exists():
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception:
            logger.warning("폰트 로드 실패: %s", font_path)

    # 2. 시스템 한글 폰트 탐색 (fc-list)
    try:
        result = subprocess.run(
            ["fc-list", ":lang=ko", "--format=%{file}\n"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.splitlines():
            p = line.strip()
            if p and Path(p).exists():
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    continue
    except Exception:
        pass

    # 3. 최후 fallback
    logger.warning("폰트 없음: %s — PIL 기본 폰트 사용 (한글 깨질 수 있음)", filename)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------

def _wrap_text_ko(text: str, width: int = WRAP_WIDTH) -> list[str]:
    """한국어 텍스트를 고정 글자 수(표시 폭 기준)로 줄바꿈.

    textwrap은 CJK 폭을 인식하지 못하므로 직접 구현.
    공백이 있으면 공백 위치에서 분리, 없으면 강제 분리.
    """
    lines: list[str] = []
    while len(text) > width:
        idx = text.rfind(" ", 0, width + 1)
        if idx <= 0:
            idx = width
        lines.append(text[:idx])
        text = text[idx:].lstrip()
    if text:
        lines.append(text)
    return lines


def _wrap_text_pixel(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
    indent: int = 0,
) -> list[str]:
    """픽셀 기반 텍스트 래핑 (들여쓰기 고려).

    Args:
        text:      래핑할 텍스트
        font:      렌더링에 사용할 폰트
        max_width: 전체 텍스트 영역 폭(px)
        indent:    들여쓰기 폭(px) — 실제 가용 폭은 max_width - indent
    """
    available_width = max_width - indent
    lines: list[str] = []
    current = ""
    for char in text:
        test = current + char
        try:
            w = font.getlength(test)
        except AttributeError:
            w = font.getbbox(test)[2]
        if w > available_width:
            if current:
                lines.append(current)
            current = char
        else:
            current = test
    if current:
        lines.append(current)
    return lines or [text]


def _calc_entry_height(text: str, is_comment: bool) -> int:
    """문장 하나를 렌더링할 때 필요한 픽셀 높이 (줄 높이 × 줄 수 + 문장 간격)."""
    lines = _wrap_text_ko(text)
    lh = COMMENT_LINE_HEIGHT if is_comment else LINE_HEIGHT
    return len(lines) * lh + SENTENCE_GAP


def _get_audio_duration(path: Path) -> float:
    """ffprobe로 오디오 파일 길이(초)를 반환한다."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def _generate_meta_text() -> str:
    """메타 정보 텍스트 생성. SSUL_META_RANDOMIZE=true이면 랜덤."""
    from config import settings as s
    if not getattr(s, "SSUL_META_RANDOMIZE", True):
        return "익명의 유저  |  22:29  |  조회수 48만"
    views = random.choice(["12만", "28만", "41만", "55만", "63만", "87만"])
    hour = random.randint(8, 23)
    minute = random.choice(["03", "17", "29", "44", "51"])
    return f"익명의 유저  |  {hour:02d}:{minute}  |  조회수 {views}"


def _get_sfx_for_sentence(section: str, text: str) -> tuple[str, float]:
    """문장 유형에 따른 (효과음 파일명, 볼륨) 반환."""
    if section == "hook":
        return ("click.mp3", 0.6)
    # 따옴표 포함 → 댓글 인용
    if any(q in text for q in ('"', "'", "\u2018", "\u2019", "\u201c", "\u201d")):
        return ("shutter.mp3", 0.5)
    # 반전/충격 키워드
    if any(w in text for w in ["ㄷㄷ", "충격", "반전", "실화", "헐", "대박", "미쳤"]):
        return ("ding.mp3", 0.4)
    # 부정적 키워드
    if any(w in text for w in ["최악", "실망", "짜증", "화났", "억울", "억장", "어이없"]):
        return ("error.mp3", 0.4)
    return ("pop.mp3", 0.45)


# ---------------------------------------------------------------------------
# PIL 프레임 생성
# ---------------------------------------------------------------------------

def create_ssul_frame(
    text_history: list[dict],   # [{"text": str, "section": str, "is_new": bool}]
    title: str,
    meta_text: str,
    template_path: Path,
    output_path: Path,
    font_dir: Path,
) -> Path:
    """PIL로 단일 프레임 이미지 생성 (1116×2000 캔버스).

    오버플로우 발생 시 오래된 문장을 앞에서 제거하여
    텍스트가 자연스럽게 위로 밀리는 효과를 연출한다.
    """
    img = Image.open(template_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    font_title = _load_font(font_dir, "NotoSansKR-Bold.ttf", TITLE_FONT_SIZE)
    font_body = _load_font(font_dir, "NotoSansKR-Medium.ttf", BODY_FONT_SIZE)
    font_comment = _load_font(font_dir, "NotoSansKR-Regular.ttf", COMMENT_FONT_SIZE)
    font_meta = _load_font(font_dir, "NotoSansKR-Regular.ttf", META_FONT_SIZE)

    # 게시글 타이틀
    draw.text((TEXT_X, TEXT_Y_TITLE), title, font=font_title, fill="#1A1A1A")

    # 구분선
    draw.line(
        [(TEXT_X, TEXT_Y_SEP), (TEXT_X_RIGHT, TEXT_Y_SEP)],
        fill="#DDDDDD", width=2,
    )

    # 메타 정보
    draw.text((TEXT_X, TEXT_Y_META), meta_text, font=font_meta, fill="#888888")

    # 오버플로우 처리: 전체 높이가 안전 영역을 초과하면 오래된 문장 제거
    history = list(text_history)
    while len(history) > 1:
        total_h = sum(
            _calc_entry_height(e["text"], e["section"] == "comment")
            for e in history
        )
        if TEXT_Y_BODY_START + total_h <= TEXT_Y_OVERFLOW_MAX:
            break
        history.pop(0)

    # 설정 로드 (런타임에 변경 가능하도록 매 프레임 조회)
    from config import settings as s
    prev_color: str = getattr(s, "SSUL_PREV_TEXT_COLOR", "#666666")
    new_color: str = getattr(s, "SSUL_NEW_TEXT_COLOR", "#000000")
    comment_bg_enable: bool = getattr(s, "SSUL_COMMENT_BG_ENABLE", True)

    # 본문 텍스트 렌더링
    current_y = TEXT_Y_BODY_START
    for entry in history:
        text = entry["text"]
        section = entry["section"]
        is_new = entry.get("is_new", False)
        is_comment = section == "comment"

        color = new_color if is_new else prev_color
        font = font_comment if is_comment else font_body
        lh = COMMENT_LINE_HEIGHT if is_comment else LINE_HEIGHT
        indent = COMMENT_INDENT if is_comment else 0
        x = TEXT_X + indent

        # 픽셀 기반 래핑 (들여쓰기 반영)
        lines = _wrap_text_pixel(text, font, MAX_TEXT_WIDTH, indent)

        # 댓글 배경 박스
        if is_comment and comment_bg_enable:
            box_y_start = current_y - 5
            box_y_end = current_y + len(lines) * lh + 5
            draw.rectangle(
                [(TEXT_X + 10, box_y_start), (TEXT_X_RIGHT - 10, box_y_end)],
                fill="#F0F0F0",
                outline="#DDDDDD",
                width=1,
            )

        for line in lines:
            if current_y > TEXT_Y_OVERFLOW_MAX:
                break
            draw.text((x, current_y), line, font=font, fill=color)
            current_y += lh
        current_y += SENTENCE_GAP

    img.save(str(output_path), "PNG")
    return output_path


# ---------------------------------------------------------------------------
# TTS 청크 생성 (비동기)
# ---------------------------------------------------------------------------

async def _tts_chunk_async(
    text: str,
    idx: int,
    output_dir: Path,
    voice: str,
    rate: str,
) -> float:
    """문장 하나에 대한 TTS mp3 생성. 실패 시 1회 재시도 후 0.5초 묵음 삽입.

    Returns:
        생성된 오디오 파일 길이(초)
    """
    out_path = output_dir / f"chunk_{idx:03d}.mp3"
    for attempt in range(2):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(out_path))

            # 앞부분 묵음 제거 (Edge-TTS 패딩 0.1~0.3초 제거 → SFX 싱크 개선)
            trimmed_path = out_path.with_suffix(".trimmed.mp3")
            trim_result = subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(out_path),
                    "-af", "silenceremove=start_periods=1:start_threshold=-50dB:start_duration=0.1",
                    "-c:a", "libmp3lame", "-b:a", "128k",
                    str(trimmed_path),
                ],
                capture_output=True,
            )
            if trim_result.returncode == 0 and trimmed_path.exists():
                trimmed_path.replace(out_path)

            return _get_audio_duration(out_path)
        except Exception:
            if attempt == 0:
                logger.warning("TTS 청크 %d 실패 — 재시도 중", idx, exc_info=True)
                await asyncio.sleep(0.5)
            else:
                logger.error("TTS 청크 %d 최종 실패 — 묵음 0.5초 삽입", idx)

    # 묵음 파일 생성
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
            "-t", "0.5", "-c:a", "libmp3lame", "-b:a", "64k",
            str(out_path),
        ],
        capture_output=True, check=True,
    )
    return 0.5


async def _generate_all_chunks(
    sentences: list[dict],
    output_dir: Path,
    voice: str,
    rate: str,
) -> list[float]:
    """모든 문장의 TTS 청크를 순차 생성하고 길이(초) 목록을 반환한다."""
    durations: list[float] = []
    for idx, sent in enumerate(sentences):
        dur = await _tts_chunk_async(sent["text"], idx, output_dir, voice, rate)
        durations.append(dur)
        logger.debug("TTS 청크 %d: %.2fs — %s…", idx, dur, sent["text"][:30])
    return durations


def _run_async(coro) -> object:
    """동기 컨텍스트에서 코루틴을 실행하는 헬퍼.

    이미 실행 중인 이벤트 루프가 있으면 새 스레드에서 asyncio.run() 실행.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result()
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# FFmpeg 헬퍼
# ---------------------------------------------------------------------------

def _merge_tts_chunks(chunks: list[Path], output_path: Path) -> None:
    """FFmpeg concat demuxer로 TTS 청크들을 하나의 mp3로 합산한다."""
    concat_file = output_path.parent / "tts_concat.txt"
    concat_file.write_text(
        "".join(f"file '{c.resolve()}'\n" for c in chunks),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(output_path),
        ],
        capture_output=True, check=True,
    )
    concat_file.unlink(missing_ok=True)


def _build_sfx_filter(
    sentences: list[dict],
    timings: list[float],          # 각 문장의 시작 타임스탬프(초)
    sfx_choices: list[tuple[str, float]],  # (파일명, 볼륨)
    audio_dir: Path,
    tts_input_idx: int = 1,        # FFmpeg 입력 중 merged_tts.mp3의 인덱스
    sfx_offset: float = -0.08,     # 전역 SFX 타이밍 오프셋(초, 음수=앞당김)
) -> tuple[list[str], str]:
    """효과음 amix용 FFmpeg 입력 목록과 filter_complex 문자열을 구성한다.

    ding.mp3, error.mp3는 길이가 1초 이상이므로 문장 시작 0.1초 앞에 배치.
    섹션 전환(hook→body, body→closer) 시 swoosh.mp3 추가.

    Returns:
        extra_inputs: ["-i", sfx_path, ...] 형태의 추가 FFmpeg 입력 인자
        filter_str:   filter_complex 문자열 (오디오 부분)
    """
    extra_inputs: list[str] = []
    filter_parts: list[str] = []
    sfx_labels: list[str] = []
    current_idx = tts_input_idx + 1  # SFX 파일 입력 시작 인덱스

    # 섹션 전환 타임스탬프 수집 (hook→body, body→closer)
    transition_times: list[float] = []
    prev_section = sentences[0]["section"] if sentences else ""
    for sent, t in zip(sentences, timings):
        if sent["section"] != prev_section:
            transition_times.append(t)
        prev_section = sent["section"]

    # 문장별 SFX
    for i, (sent, t_start, (sfx_file, vol)) in enumerate(
        zip(sentences, timings, sfx_choices)
    ):
        sfx_path = audio_dir / sfx_file
        if not sfx_path.exists():
            logger.warning("SFX 파일 없음: %s", sfx_path)
            continue

        # 긴 효과음은 0.1초, 전역 오프셋 추가 앞당겨 배치
        lead_in = 0.1 if sfx_file in ("ding.mp3", "error.mp3") else 0.0
        delay_ms = max(0, int((t_start - lead_in + sfx_offset) * 1000))
        label = f"sfx{i}"

        extra_inputs += ["-i", str(sfx_path)]
        filter_parts.append(
            f"[{current_idx}:a]adelay={delay_ms}|{delay_ms},volume={vol}[{label}]"
        )
        sfx_labels.append(f"[{label}]")
        current_idx += 1

    # 섹션 전환 swoosh
    swoosh_path = audio_dir / "swoosh.mp3"
    if swoosh_path.exists():
        for j, t_sw in enumerate(transition_times):
            delay_ms = max(0, int(t_sw * 1000))
            label = f"swoosh{j}"
            extra_inputs += ["-i", str(swoosh_path)]
            filter_parts.append(
                f"[{current_idx}:a]adelay={delay_ms}|{delay_ms},volume=0.35[{label}]"
            )
            sfx_labels.append(f"[{label}]")
            current_idx += 1

    tts_ref = f"[{tts_input_idx}:a]"
    if sfx_labels:
        all_refs = tts_ref + "".join(sfx_labels)
        n_inputs = 1 + len(sfx_labels)
        filter_str = (
            ";".join(filter_parts)
            + f";{all_refs}amix=inputs={n_inputs}:normalize=0[aout]"
        )
    else:
        # 효과음 없으면 TTS를 그대로 통과
        filter_str = f"{tts_ref}acopy[aout]"

    return extra_inputs, filter_str


def _resolve_codec() -> str:
    """h264_nvenc 가용 여부를 확인하고 코덱 이름을 반환한다."""
    from ai_worker.video import _check_nvenc
    return "h264_nvenc" if _check_nvenc() else "libx264"


def _get_encoder_args(codec: str) -> list[str]:
    if codec == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "medium", "-cq", "23", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-pix_fmt", "yuv420p"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_ssul_video(post, script, output_path: Path | None = None) -> Path:
    """양산형 쇼츠 영상을 렌더링하고 경로를 반환한다.

    Args:
        post:        Post 객체 (post.id, post.title 사용)
        script:      ScriptData 객체 (hook / body / closer)
        output_path: 최종 mp4 저장 경로. None이면 media/video/post_{id}.mp4

    Returns:
        생성된 mp4 파일 경로
    """
    from config import settings as s

    # 설정 로드
    template_path: Path = getattr(s, "SSUL_TEMPLATE_PATH", ASSETS_DIR / "backgrounds" / "base_template.png")
    audio_dir: Path = getattr(s, "SSUL_AUDIO_DIR", ASSETS_DIR / "audio")
    voice: str = getattr(s, "SSUL_TTS_VOICE", "ko-KR-SunHiNeural")
    rate: str = getattr(s, "SSUL_TTS_RATE", "+25%")
    sfx_offset: float = getattr(s, "SSUL_SFX_OFFSET", -0.08)
    font_dir: Path = ASSETS_DIR / "fonts"

    if not template_path.exists():
        raise FileNotFoundError(f"썰 템플릿 없음: {template_path}")

    # 최종 출력 경로
    video_dir = MEDIA_DIR / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        output_path = video_dir / f"post_{post.id}.mp4"

    # 임시 작업 디렉토리
    tmp_dir = MEDIA_DIR / "tmp" / f"ssul_{post.id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 1: 문장 분리 ─────────────────────────────────────
        sentences: list[dict] = []
        sentences.append({"text": script.hook, "section": "hook"})
        for body_text in script.body:
            is_quote = any(q in body_text for q in ('"', "'", "\u2018", "\u2019", "\u201c", "\u201d"))
            section = "comment" if is_quote else "body"
            sentences.append({"text": body_text, "section": section})
        sentences.append({"text": script.closer, "section": "closer"})

        logger.info("[ssul] post_id=%d 문장 %d개 렌더링 시작", post.id, len(sentences))

        # ── Step 2: TTS 청크 생성 ─────────────────────────────────
        import time as _time
        logger.info("[ssul] TTS 생성 시작")
        _t0 = _time.time()
        durations: list[float] = _run_async(  # type: ignore[assignment]
            _generate_all_chunks(sentences, tmp_dir, voice, rate)
        )
        total_dur = sum(durations)
        logger.info("[ssul] TTS 청크 완료: %d개, 총 %.1fs (생성 %.2fs)",
                    len(durations), total_dur, _time.time() - _t0)

        # ── Step 3: 타임스탬프 계산 ───────────────────────────────
        timings: list[float] = []
        t = 0.0
        for dur in durations:
            timings.append(t)
            t += dur

        # ── Step 4: SFX 선택 ──────────────────────────────────────
        sfx_choices = [_get_sfx_for_sentence(s["section"], s["text"]) for s in sentences]

        # ── Step 5: TTS concat → merged_tts.mp3 ───────────────────
        chunk_paths = [tmp_dir / f"chunk_{i:03d}.mp3" for i in range(len(sentences))]
        merged_tts = tmp_dir / "merged_tts.mp3"
        _merge_tts_chunks(chunk_paths, merged_tts)

        # ── Step 6: PIL 프레임 생성 (5-2-5 하이브리드 스크롤) ────
        max_visible: int = getattr(s, "SSUL_MAX_VISIBLE_SENTENCES", 5)
        scroll_out: int = getattr(s, "SSUL_SCROLL_OUT_COUNT", 2)

        title = (post.title or "")[:40]
        meta_text = _generate_meta_text()
        text_history: list[dict] = []
        frame_paths: list[Path] = []

        logger.info("[ssul] 이미지 프레임 생성 시작")
        _t1 = _time.time()
        for i, sent in enumerate(sentences):
            # 이전 문장들을 흐리게 표시
            for prev in text_history:
                prev["is_new"] = False

            # 5-2-5 FIFO: 최대 문장 수 초과 시 오래된 것부터 제거
            if len(text_history) >= max_visible:
                text_history = text_history[scroll_out:]

            text_history.append({
                "text": sent["text"],
                "section": sent["section"],
                "is_new": True,
            })
            frame_path = tmp_dir / f"frame_{i:03d}.png"
            create_ssul_frame(
                text_history, title, meta_text,
                template_path, frame_path, font_dir,
            )
            frame_paths.append(frame_path)

        logger.info("[ssul] PIL 프레임 %d장 생성 완료 (%.2fs)",
                    len(frame_paths), _time.time() - _t1)

        # ── Step 7: concat_list.txt ───────────────────────────────
        concat_file = tmp_dir / "concat_list.txt"
        lines: list[str] = []
        for fp, dur in zip(frame_paths, durations):
            lines.append(f"file '{fp.resolve()}'\n")
            lines.append(f"duration {dur:.4f}\n")
        # FFmpeg concat demuxer: 마지막 파일을 한 번 더 추가해야 올바른 길이 출력
        if frame_paths:
            lines.append(f"file '{frame_paths[-1].resolve()}'\n")
        concat_file.write_text("".join(lines), encoding="utf-8")

        # ── Step 8: 효과음 필터 구성 ──────────────────────────────
        extra_inputs, sfx_filter = _build_sfx_filter(
            sentences, timings, sfx_choices, audio_dir,
            tts_input_idx=1, sfx_offset=sfx_offset,
        )

        # ── Step 9: FFmpeg 최종 인코딩 ────────────────────────────
        codec = _resolve_codec()
        enc_args = _get_encoder_args(codec)

        # video/audio 모두 filter_complex에서 처리
        video_filter = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2[vout]"
        )
        filter_complex = f"{video_filter};{sfx_filter}"

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-i", str(merged_tts),
            *extra_inputs,
            "-filter_complex", filter_complex,
            "-map", "[vout]",
            "-map", "[aout]",
            *enc_args,
            "-c:a", "aac", "-b:a", "192k",
            "-r", "30",
            str(output_path),
        ]

        logger.info("[ssul] FFmpeg 인코딩 시작: %s", output_path.name)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("[ssul] FFmpeg 실패 (returncode=%d):\n%s",
                         result.returncode, result.stderr[-3000:])
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )

        logger.info("[ssul] 완료: %s (%.1fs)", output_path.name, total_dur)
        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
