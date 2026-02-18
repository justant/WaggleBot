"""썰 렌더러 v4 — 초대형 폰트 + 중앙 정렬 + 3줄 페이지 넘김.

기존 Ken Burns 슬라이드쇼 방식의 대안으로, 인터넷 밈 '썰 읽어주는 쇼츠' 스타일을
구현한다. 문장 단위 TTS + PIL 누적 텍스트 프레임 + 효과음 amix.

파이프라인:
  ScriptData → 문장 분리 → 줄바꿈 사전 계산 → TTS 청크 생성
  → PIL 프레임 생성 (3줄 Clear) → FFmpeg concat/amix → mp4
"""
import asyncio
import logging
import random
import shutil
import subprocess
import time
from pathlib import Path

import edge_tts
from PIL import Image, ImageDraw, ImageFont

from config.settings import ASSETS_DIR, MEDIA_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 레이아웃 상수 v4 (1116×2000 캔버스 — FFmpeg가 1080×1920으로 리사이즈)
# ---------------------------------------------------------------------------
CANVAS_W = 1116
CANVAS_H = 2000

# 텍스트 영역
TEXT_X_CENTER = CANVAS_W // 2          # 558 — 중앙 정렬 기준점
MAX_TEXT_WIDTH = 950                   # 좌우 83px 여백

# 상단 고정 영역
TEXT_X = 99                            # 타이틀/메타 좌측 기준 (기존 유지)
TEXT_X_RIGHT = 1016
TEXT_Y_TITLE = 265
TEXT_Y_SEP = 330
TEXT_Y_META = 340

# 본문 시작 Y (중앙 집중)
TEXT_Y_BODY_START = 500                # 기존 390 → 110px 하향

# 폰트 크기 (v4: 1.8배 확대)
TITLE_FONT_SIZE = 52
BODY_FONT_SIZE = 85
COMMENT_FONT_SIZE = 70
META_FONT_SIZE = 32

# 줄 높이 / 간격
LINE_HEIGHT = int(BODY_FONT_SIZE * 1.4)           # 119px
SENTENCE_GAP = int(LINE_HEIGHT * 0.4)             # 48px
COMMENT_LINE_HEIGHT = int(COMMENT_FONT_SIZE * 1.4)  # 98px


# ---------------------------------------------------------------------------
# 폰트 로더
# ---------------------------------------------------------------------------

def _load_font(font_dir: Path, filename: str, size: int) -> ImageFont.FreeTypeFont:
    """폰트 파일 로드. 없으면 시스템 한글 폰트 → PIL 기본 폰트 순으로 fallback."""
    font_path = font_dir / filename
    if font_path.exists():
        try:
            return ImageFont.truetype(str(font_path), size)
        except Exception:
            logger.warning("폰트 로드 실패: %s", font_path)

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

    logger.warning("폰트 없음: %s — PIL 기본 폰트 사용 (한글 깨질 수 있음)", filename)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ---------------------------------------------------------------------------
# 유틸리티
# ---------------------------------------------------------------------------

def _wrap_text_pixel(
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    """픽셀 폭 기반 줄바꿈 (중앙 정렬용, 공백 우선 분리).

    1. 공백으로 단어를 분리하여 줄을 구성한다.
    2. 단어 자체가 max_width를 초과하면 글자 단위로 강제 분할한다.
    """
    if not text:
        return []

    def _getw(t: str) -> float:
        try:
            return font.getlength(t)
        except AttributeError:
            return font.getbbox(t)[2]

    lines: list[str] = []
    words = text.split(" ")
    current_line: list[str] = []
    current_width: float = 0.0
    space_w = _getw(" ")

    for word in words:
        word_w = _getw(word)

        # 단어 자체가 max_width 초과 → 글자 단위 강제 분할
        if word_w > max_width:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = []
                current_width = 0.0
            sub = ""
            sub_w: float = 0.0
            for ch in word:
                ch_w = _getw(ch)
                if sub_w + ch_w > max_width:
                    if sub:
                        lines.append(sub)
                    sub = ch
                    sub_w = ch_w
                else:
                    sub += ch
                    sub_w += ch_w
            if sub:
                current_line = [sub]
                current_width = sub_w
            continue

        # 현재 줄에 단어 추가 시 폭 계산
        add_w = word_w + (space_w if current_line else 0.0)
        if current_width + add_w <= max_width:
            current_line.append(word)
            current_width += add_w
        else:
            if current_line:
                lines.append(" ".join(current_line))
            current_line = [word]
            current_width = word_w

    if current_line:
        lines.append(" ".join(current_line))

    return lines or [text]


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
    if any(q in text for q in ('"', "'", "\u2018", "\u2019", "\u201c", "\u201d")):
        return ("shutter.mp3", 0.5)
    if any(w in text for w in ["ㄷㄷ", "충격", "반전", "실화", "헐", "대박", "미쳤"]):
        return ("ding.mp3", 0.4)
    if any(w in text for w in ["최악", "실망", "짜증", "화났", "억울", "억장", "어이없"]):
        return ("error.mp3", 0.4)
    return ("pop.mp3", 0.45)


# ---------------------------------------------------------------------------
# PIL 프레임 생성
# ---------------------------------------------------------------------------

def create_ssul_frame(
    text_history: list[dict],   # [{"lines": list[str], "section": str, "is_new": bool}]
    title: str,
    meta_text: str,
    template_path: Path,
    output_path: Path,
    font_dir: Path,
) -> Path:
    """PIL로 단일 프레임 이미지 생성 (1116×2000 캔버스).

    각 줄마다 getlength()로 폭을 측정하여 가로 중앙 정렬한다.
    댓글 항목에는 둥근 모서리 배경 박스를 그린다.
    """
    from config import settings as s

    # 설정 로드
    prev_color: str = getattr(s, "SSUL_PREV_TEXT_COLOR", "#666666")
    new_color: str = getattr(s, "SSUL_NEW_TEXT_COLOR", "#000000")
    comment_bg_enable: bool = getattr(s, "SSUL_COMMENT_BG_ENABLE", True)
    comment_bg_color: str = getattr(s, "SSUL_COMMENT_BG_COLOR", "#F5F5F5")
    comment_border_color: str = getattr(s, "SSUL_COMMENT_BORDER_COLOR", "#DDDDDD")
    comment_border_radius: int = getattr(s, "SSUL_COMMENT_BORDER_RADIUS", 15)

    # 폰트 (v4 크기)
    f_title_sz: int = getattr(s, "SSUL_FONT_SIZE_TITLE", TITLE_FONT_SIZE)
    f_body_sz: int = getattr(s, "SSUL_FONT_SIZE_BODY", BODY_FONT_SIZE)
    f_cmt_sz: int = getattr(s, "SSUL_FONT_SIZE_COMMENT", COMMENT_FONT_SIZE)
    f_meta_sz: int = getattr(s, "SSUL_FONT_SIZE_META", META_FONT_SIZE)

    font_title = _load_font(font_dir, "NotoSansKR-Bold.ttf", f_title_sz)
    font_body = _load_font(font_dir, "NotoSansKR-Medium.ttf", f_body_sz)
    font_comment = _load_font(font_dir, "NotoSansKR-Regular.ttf", f_cmt_sz)
    font_meta = _load_font(font_dir, "NotoSansKR-Regular.ttf", f_meta_sz)

    img = Image.open(template_path).convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── 상단 고정 영역 ──────────────────────────────────────────
    draw.text((TEXT_X, TEXT_Y_TITLE), title, font=font_title, fill="#1A1A1A")
    draw.line([(TEXT_X, TEXT_Y_SEP), (TEXT_X_RIGHT, TEXT_Y_SEP)], fill="#DDDDDD", width=2)
    draw.text((TEXT_X, TEXT_Y_META), meta_text, font=font_meta, fill="#888888")

    # ── 본문 텍스트 렌더링 (중앙 정렬) ─────────────────────────
    bottom_limit = CANVAS_H - 100   # 하단 안전 여백
    current_y = TEXT_Y_BODY_START

    for entry in text_history:
        lines: list[str] = entry["lines"]
        section: str = entry["section"]
        is_new: bool = entry.get("is_new", False)
        is_comment: bool = section == "comment"

        color = new_color if is_new else prev_color
        font = font_comment if is_comment else font_body
        lh = COMMENT_LINE_HEIGHT if is_comment else LINE_HEIGHT

        # 댓글 배경 박스 (rounded_rectangle — Pillow 8.2+ 필요)
        if is_comment and comment_bg_enable:
            block_h = len(lines) * lh
            box_left = 60
            box_right = CANVAS_W - 60
            try:
                draw.rounded_rectangle(
                    [(box_left, current_y - 10), (box_right, current_y + block_h + 10)],
                    radius=comment_border_radius,
                    fill=comment_bg_color,
                    outline=comment_border_color,
                    width=2,
                )
            except AttributeError:
                # Pillow < 8.2 fallback
                draw.rectangle(
                    [(box_left, current_y - 10), (box_right, current_y + block_h + 10)],
                    fill=comment_bg_color,
                    outline=comment_border_color,
                    width=2,
                )

        # 각 줄마다 폭 측정 → 중앙 x 계산 → 렌더링
        for line in lines:
            if current_y + lh > bottom_limit:
                break
            try:
                line_w = font.getlength(line)
            except AttributeError:
                line_w = font.getbbox(line)[2]
            center_x = int((CANVAS_W - line_w) / 2)
            if is_comment:
                center_x += 20   # 댓글은 살짝 우측 오프셋
            draw.text((center_x, current_y), line, font=font, fill=color)
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
    """문장 하나에 대한 TTS mp3 생성 + 앞부분 묵음 제거.

    빈 텍스트 → 0.0 즉시 반환.
    TTS 실패 → 1회 재시도 후 0.0 반환 (묵음 삽입하지 않아 concat 타이밍 오염 방지).
    """
    out_path = output_dir / f"chunk_{idx:03d}.mp3"

    if not text or not text.strip():
        return 0.0

    for attempt in range(2):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(out_path))
            break
        except Exception:
            if attempt == 0:
                logger.warning("TTS 청크 %d 실패 — 재시도 중", idx, exc_info=True)
                await asyncio.sleep(0.5)
            else:
                logger.error("TTS 청크 %d 최종 실패 — 건너뜀", idx)
                return 0.0

    # 앞부분 묵음 제거 (Edge-TTS 패딩 0.1~0.3초 → SFX 싱크 개선)
    trimmed = out_path.with_name(f"{out_path.stem}_trim.mp3")
    try:
        trim_result = subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(out_path),
                "-af", "silenceremove=start_periods=1:start_threshold=-50dB:start_duration=0.1",
                "-c:a", "libmp3lame", "-q:a", "2",
                str(trimmed),
            ],
            capture_output=True, timeout=10,
        )
        if trim_result.returncode == 0 and trimmed.exists() and trimmed.stat().st_size > 0:
            trimmed.replace(out_path)
        else:
            trimmed.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("묵음 제거 실패 (원본 사용): %s", e)
        trimmed.unlink(missing_ok=True)

    return _get_audio_duration(out_path)


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
    """동기 컨텍스트에서 코루틴을 실행하는 헬퍼."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# FFmpeg 헬퍼
# ---------------------------------------------------------------------------

def _merge_tts_chunks(chunks: list[Path], output_path: Path) -> None:
    """FFmpeg concat demuxer로 TTS 청크들을 하나의 mp3로 합산한다."""
    # 존재하는 청크만 포함 (duration=0.0 → 파일 없을 수 있음)
    valid_chunks = [c for c in chunks if c.exists() and c.stat().st_size > 0]
    if not valid_chunks:
        raise RuntimeError("유효한 TTS 청크가 없습니다.")

    concat_file = output_path.parent / "tts_concat.txt"
    concat_file.write_text(
        "".join(f"file '{c.resolve()}'\n" for c in valid_chunks),
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
    timings: list[float],
    sfx_choices: list[tuple[str, float]],
    audio_dir: Path,
    tts_input_idx: int = 1,
    sfx_offset: float = -0.15,
) -> tuple[list[str], str]:
    """효과음 amix용 FFmpeg 입력 목록과 filter_complex 문자열을 구성한다.

    ding/error/shutter.mp3는 발음 전 0.15초 앞에 배치.
    섹션 전환(hook→body, body→closer) 시 swoosh.mp3 추가.
    sfx_offset: 전역 타이밍 오프셋 (음수 = 앞당김).
    """
    extra_inputs: list[str] = []
    filter_parts: list[str] = []
    sfx_labels: list[str] = []
    current_idx = tts_input_idx + 1

    # 섹션 전환 타임스탬프
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

        lead_in = 0.15 if sfx_file in ("ding.mp3", "error.mp3", "shutter.mp3") else 0.0
        final_delay = t_start - lead_in + sfx_offset
        delay_ms = max(0, int(final_delay * 1000))
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
            delay_ms = max(0, int((t_sw + sfx_offset) * 1000))
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
        n = 1 + len(sfx_labels)
        filter_str = ";".join(filter_parts) + f";{all_refs}amix=inputs={n}:normalize=0[aout]"
    else:
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
    """썰 렌더러 v4 — 초대형 폰트 + 중앙 정렬 + 3줄 페이지 넘김.

    Args:
        post:        Post 객체 (post.id, post.title 사용)
        script:      ScriptData 객체 (hook / body / closer)
        output_path: 최종 mp4 저장 경로. None이면 media/video/post_{id}.mp4
    """
    from config import settings as s

    # ── 설정 로드 ──────────────────────────────────────────────
    template_path: Path = getattr(s, "SSUL_TEMPLATE_PATH", ASSETS_DIR / "backgrounds" / "base_template.png")
    audio_dir: Path = getattr(s, "SSUL_AUDIO_DIR", ASSETS_DIR / "audio")
    voice: str = getattr(s, "SSUL_TTS_VOICE", "ko-KR-SunHiNeural")
    rate: str = getattr(s, "SSUL_TTS_RATE", "+25%")
    sfx_offset: float = getattr(s, "SSUL_SFX_OFFSET", -0.15)
    max_lines_per_page: int = getattr(s, "SSUL_MAX_LINES_PER_PAGE", 3)
    max_text_width: int = getattr(s, "SSUL_MAX_TEXT_WIDTH", MAX_TEXT_WIDTH)
    font_dir: Path = ASSETS_DIR / "fonts"

    if not template_path.exists():
        raise FileNotFoundError(f"썰 템플릿 없음: {template_path}")

    video_dir = MEDIA_DIR / "video"
    video_dir.mkdir(parents=True, exist_ok=True)
    if output_path is None:
        output_path = video_dir / f"post_{post.id}.mp4"

    tmp_dir = MEDIA_DIR / "tmp" / f"ssul_{post.id}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        # ── Step 1: 문장 구조화 ────────────────────────────────
        sentences: list[dict] = []
        sentences.append({"text": script.hook, "section": "hook"})
        for body_text in script.body:
            is_quote = any(q in body_text for q in ('"', "'", "\u2018", "\u2019", "\u201c", "\u201d"))
            sentences.append({"text": body_text, "section": "comment" if is_quote else "body"})
        sentences.append({"text": script.closer, "section": "closer"})

        logger.info("[ssul] post_id=%d 문장 %d개 렌더링 시작 (v4)", post.id, len(sentences))

        # ── Step 2: 줄바꿈 사전 계산 (렌더링 전 font 필요) ────
        f_body_sz: int = getattr(s, "SSUL_FONT_SIZE_BODY", BODY_FONT_SIZE)
        f_cmt_sz: int = getattr(s, "SSUL_FONT_SIZE_COMMENT", COMMENT_FONT_SIZE)
        font_body_pre = _load_font(font_dir, "NotoSansKR-Medium.ttf", f_body_sz)
        font_cmt_pre = _load_font(font_dir, "NotoSansKR-Regular.ttf", f_cmt_sz)

        for sent in sentences:
            is_comment = sent["section"] == "comment"
            font_pre = font_cmt_pre if is_comment else font_body_pre
            sent["lines"] = _wrap_text_pixel(sent["text"], font_pre, max_text_width)

        # ── Step 3: TTS 청크 생성 ──────────────────────────────
        logger.info("[ssul] TTS 생성 시작")
        t0 = time.time()
        durations: list[float] = _run_async(  # type: ignore[assignment]
            _generate_all_chunks(sentences, tmp_dir, voice, rate)
        )
        total_dur = sum(durations)
        logger.info("[ssul] TTS 완료: %d개, 총 %.1fs (생성 %.2fs)",
                    len(durations), total_dur, time.time() - t0)

        # ── Step 4: 타임스탬프 계산 ────────────────────────────
        timings: list[float] = []
        acc = 0.0
        for dur in durations:
            timings.append(acc)
            acc += dur

        # ── Step 5: SFX 선택 ───────────────────────────────────
        sfx_choices = [_get_sfx_for_sentence(sent["section"], sent["text"]) for sent in sentences]

        # ── Step 6: TTS concat → merged_tts.mp3 ───────────────
        chunk_paths = [tmp_dir / f"chunk_{i:03d}.mp3" for i in range(len(sentences))]
        merged_tts = tmp_dir / "merged_tts.mp3"
        _merge_tts_chunks(chunk_paths, merged_tts)

        # ── Step 7: 프레임 생성 (3줄 페이지 넘김) ─────────────
        title = (post.title or "")[:40]
        meta_text = _generate_meta_text()
        text_history: list[dict] = []
        frame_paths: list[Path] = []

        logger.info("[ssul] 이미지 프레임 생성 시작")
        t1 = time.time()

        for i, sent in enumerate(sentences):
            # 이전 문장 흐리게
            for prev in text_history:
                prev["is_new"] = False

            new_entry: dict = {
                "lines": sent["lines"],
                "section": sent["section"],
                "is_new": True,
            }

            current_total_lines = sum(len(e["lines"]) for e in text_history)
            new_line_count = len(new_entry["lines"])

            # 3줄 초과 시 화면 완전 Clear
            if text_history and current_total_lines + new_line_count > max_lines_per_page:
                if new_line_count > max_lines_per_page:
                    # 단일 문장이 한계를 초과해도 무조건 표시
                    logger.warning(
                        "[ssul] 문장 %d: %d줄 (최대 %d줄 초과) — 단일 표시",
                        i, new_line_count, max_lines_per_page,
                    )
                text_history = []

            text_history.append(new_entry)

            frame_path = tmp_dir / f"frame_{i:03d}.png"
            create_ssul_frame(
                text_history, title, meta_text,
                template_path, frame_path, font_dir,
            )
            frame_paths.append(frame_path)

        logger.info("[ssul] PIL 프레임 %d장 완료 (%.2fs)", len(frame_paths), time.time() - t1)

        # ── Step 8: concat_list.txt ────────────────────────────
        concat_file = tmp_dir / "concat_list.txt"
        concat_lines: list[str] = []
        for fp, dur in zip(frame_paths, durations):
            concat_lines.append(f"file '{fp.resolve()}'\n")
            concat_lines.append(f"duration {dur:.4f}\n")
        if frame_paths:
            concat_lines.append(f"file '{frame_paths[-1].resolve()}'\n")
        concat_file.write_text("".join(concat_lines), encoding="utf-8")

        # ── Step 9: 효과음 필터 구성 ───────────────────────────
        extra_inputs, sfx_filter = _build_sfx_filter(
            sentences, timings, sfx_choices, audio_dir,
            tts_input_idx=1, sfx_offset=sfx_offset,
        )

        # ── Step 10: FFmpeg 최종 인코딩 ────────────────────────
        codec = _resolve_codec()
        enc_args = _get_encoder_args(codec)

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
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            logger.error("[ssul] FFmpeg 실패 (returncode=%d):\n%s",
                         result.returncode, result.stderr[-3000:])
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr,
            )

        logger.info("[ssul] 완료: %s (총 %.1fs)", output_path.name, total_dur)
        return output_path

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
