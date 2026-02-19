"""
ASS 동적 자막 생성 모듈

ScriptData(hook/body/closer/mood) → 타이밍 기반 .ass 파일 생성.

스타일 프리셋:
  funny     → casual    (노란 훅, 흰 본문, 청록 댓글)
  shocking  → dramatic  (빨간 아웃라인 훅, 흔들림 없는 강조)
  serious   → news      (배경띠, 뉴스 자막)
  heartwarming → warm   (부드러운 노란 강조)
"""
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 색상 상수
# ---------------------------------------------------------------------------
# 스타일 정의: &HAABBGGRR  (AA=투명도 00=불투명)
_WHITE       = "&H00FFFFFF"
_BLACK       = "&H00000000"
_RED         = "&H000000FF"
_YELLOW      = "&H0000FFFF"
_CYAN        = "&H00FFFF00"
_TRANSPARENT = "&HFF000000"
_SEMI_BLACK  = "&H80000000"   # 50% 투명 검은 배경
_YELLOW_SEMI = "&H6000FFFF"   # 62% 불투명 노란 배경 (CommentBubble 전용)

# 인라인 오버라이드 태그: &HBBGGRR&  (알파 없음)
_OVR_YELLOW = "&H00FFFF&"
_OVR_WHITE  = "&HFFFFFF&"
_OVR_CYAN   = "&HFFFF00&"


# ---------------------------------------------------------------------------
# 스타일 빌더
# ---------------------------------------------------------------------------

def _style_line(
    name: str, fontname: str, fontsize: int,
    primary: str, outline: str, back: str,
    bold: int, italic: int,
    outline_w: int, shadow: int,
    alignment: int, margin_v: int,
    border_style: int = 1,
) -> str:
    """ASS [V4+ Styles] 한 줄 생성.

    border_style:
      1 = 아웃라인+그림자 (기본)
      3 = 불투명 배경 박스 (CommentBubble)
    """
    return (
        f"Style: {name},{fontname},{fontsize},"
        f"{primary},&H000000FF,{outline},{back},"
        f"{bold},{italic},0,0,"
        f"100,100,0,0,"
        f"{border_style},{outline_w},{shadow},"
        f"{alignment},20,20,{margin_v},1"
    )


def _build_styles(mood: str, fontname: str, base_size: int) -> str:
    """mood에 맞는 Hook/Default/Comment/Closer 4종 스타일 블록 반환."""
    mv = 60  # 화면 하단 여백 픽셀

    # (name, fontname, size, primary, outline, back, bold, italic, outline_w, shadow, align, mv)
    presets: dict[str, list[tuple]] = {
        "shocking": [
            ("Hook",    fontname, base_size + 10, _WHITE,  _RED,        _TRANSPARENT, 1, 0, 4, 2, 2, mv),
            ("Default", fontname, base_size,      _WHITE,  _BLACK,      _TRANSPARENT, 1, 0, 3, 1, 2, mv),
            ("Comment", fontname, base_size - 6,  _CYAN,   _BLACK,      _SEMI_BLACK,  0, 1, 2, 1, 2, mv),
            ("Closer",  fontname, base_size - 2,  _YELLOW, _BLACK,      _TRANSPARENT, 1, 0, 3, 1, 2, mv),
        ],
        "serious": [
            ("Hook",    fontname, base_size + 6,  _YELLOW, _BLACK,      _SEMI_BLACK,  1, 0, 0, 0, 2, mv),
            ("Default", fontname, base_size,      _WHITE,  _BLACK,      _SEMI_BLACK,  1, 0, 0, 0, 2, mv),
            ("Comment", fontname, base_size - 6,  _CYAN,   _BLACK,      _SEMI_BLACK,  0, 1, 0, 0, 2, mv),
            ("Closer",  fontname, base_size - 2,  _WHITE,  _BLACK,      _SEMI_BLACK,  0, 0, 0, 0, 2, mv),
        ],
        "heartwarming": [
            ("Hook",    fontname, base_size + 6,  _YELLOW, _BLACK,      _TRANSPARENT, 1, 0, 3, 1, 2, mv),
            ("Default", fontname, base_size,      _WHITE,  _BLACK,      _TRANSPARENT, 0, 0, 3, 1, 2, mv),
            ("Comment", fontname, base_size - 6,  _YELLOW, _BLACK,      _SEMI_BLACK,  0, 1, 2, 1, 2, mv),
            ("Closer",  fontname, base_size - 2,  _WHITE,  _BLACK,      _TRANSPARENT, 0, 0, 3, 1, 2, mv),
        ],
        "funny": [
            ("Hook",    fontname, base_size + 8,  _YELLOW, _BLACK,      _TRANSPARENT, 1, 0, 3, 2, 2, mv),
            ("Default", fontname, base_size,      _WHITE,  _BLACK,      _TRANSPARENT, 1, 0, 3, 1, 2, mv),
            ("Comment", fontname, base_size - 6,  _CYAN,   _BLACK,      _SEMI_BLACK,  0, 1, 2, 1, 2, mv),
            ("Closer",  fontname, base_size - 2,  _YELLOW, _BLACK,      _TRANSPARENT, 1, 0, 3, 1, 2, mv),
        ],
    }

    rows = presets.get(mood, presets["funny"])
    base_styles = "\n".join(_style_line(*r) for r in rows)

    # CommentBubble: 모든 mood 공통 — 상단 노란 배경 말풍선
    # alignment=8 (top center), BorderStyle=3 (opaque box), 검은 텍스트
    bubble_style = _style_line(
        "CommentBubble", fontname, base_size - 2,
        _BLACK, _BLACK, _YELLOW_SEMI,
        1, 0, 0, 0,
        alignment=8, margin_v=80, border_style=3,
    )
    return base_styles + "\n" + bubble_style


# ---------------------------------------------------------------------------
# 타이밍
# ---------------------------------------------------------------------------

def _time_str(secs: float) -> str:
    """초 → ASS 시간 H:MM:SS.cs"""
    secs = max(0.0, secs)
    h  = int(secs // 3600)
    m  = int((secs % 3600) // 60)
    s  = int(secs % 60)
    cs = int(round((secs % 1) * 100))
    if cs >= 100:
        s += 1
        cs = 0
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _proportional_timings(
    sentences: list[str],
    total: float,
    gap: float = 0.12,
) -> list[tuple[float, float]]:
    """
    글자 수 비례 타이밍 계산.
    gap: 앞 자막이 다음 자막 시작 전 gap초 먼저 퇴장 (자연스러운 전환).
    """
    char_counts = [max(len(s.strip()), 1) for s in sentences]
    total_chars = sum(char_counts)
    n = len(sentences)

    result: list[tuple[float, float]] = []
    current = 0.0
    for i, chars in enumerate(char_counts):
        seg = (chars / total_chars) * total
        end = current + seg - (gap if i < n - 1 else 0.0)
        result.append((round(current, 3), round(end, 3)))
        current += seg

    return result


# ---------------------------------------------------------------------------
# 텍스트 처리
# ---------------------------------------------------------------------------

def _esc_ass(text: str) -> str:
    """ASS Dialogue 텍스트 이스케이프."""
    text = text.replace("\\", "")
    # { }는 ASS 오버라이드 블록 → 전각으로 교체
    text = text.replace("{", "｛").replace("}", "｝")
    text = text.replace("\n", "\\N")
    return text


def _highlight_quotes(text: str) -> str:
    """따옴표 안 텍스트를 노란색 인라인 오버라이드로 강조."""
    def repl(m: re.Match) -> str:
        inner = m.group(1) or m.group(2) or ""
        return f"{{\\c{_OVR_YELLOW}}}{inner}{{\\c{_OVR_WHITE}}}"

    return re.sub(r"'([^']{1,80})'|\"([^\"]{1,80})\"", repl, text)


def _is_comment_sentence(text: str) -> bool:
    """댓글 인용 문장 판별 (따옴표 포함 + 인용 맥락)."""
    has_quote = bool(re.search(r"'[^']+'|\"[^\"]+\"", text))
    has_context = any(kw in text for kw in ("댓글", "인용", "ㅋㅋ", "ㅎㅎ"))
    starts_quoted = text.strip().startswith(("'", '"'))
    return has_quote and (has_context or starts_quoted)


# ---------------------------------------------------------------------------
# ASS 파일 빌더
# ---------------------------------------------------------------------------

def build_ass(
    hook: str,
    body: list[str],
    closer: str,
    duration: float,
    mood: str,
    fontname: str,
    width: int,
    height: int,
) -> str:
    """ASS 자막 파일 전체 내용(문자열) 생성."""
    base_size    = max(40, int(width * 0.050))   # 1080w → 54px
    style_block  = _build_styles(mood, fontname, base_size)

    header_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour,"
        " BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,"
        " BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        style_block,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    sentences = [hook] + list(body) + [closer]
    timings   = _proportional_timings(sentences, duration)

    dialogues: list[str] = []
    n = len(sentences)

    for i, (sentence, (start, end)) in enumerate(zip(sentences, timings)):
        if not sentence.strip():
            continue

        raw = _esc_ass(sentence)

        if i == 0:
            # 1막: Hook — 강한 페이드인
            text = f"{{\\fad(300,200)}}{raw}"
            dialogues.append(
                f"Dialogue: 0,{_time_str(start)},{_time_str(end)},Hook,,0,0,0,,{text}"
            )

        elif i == n - 1:
            # 3막: Closer — 페이드아웃 길게
            text = f"{{\\fad(250,400)}}{raw}"
            dialogues.append(
                f"Dialogue: 0,{_time_str(start)},{_time_str(end)},Closer,,0,0,0,,{text}"
            )

        else:
            # 2막: Body — 댓글 인용 여부로 스타일 분기
            # CommentBubble: 상단 노란 말풍선 / Default: 하단 일반 자막
            is_comment = _is_comment_sentence(sentence)
            style = "CommentBubble" if is_comment else "Default"
            highlighted = _highlight_quotes(raw)
            # CommentBubble은 페이드인만 (말풍선이 튀어나오는 느낌)
            fade = "{\\fad(150,100)}" if is_comment else "{\\fad(250,200)}"
            text = f"{fade}{highlighted}"
            dialogues.append(
                f"Dialogue: 0,{_time_str(start)},{_time_str(end)},{style},,0,0,0,,{text}"
            )

    return "\n".join(header_lines) + "\n" + "\n".join(dialogues) + "\n"


def get_comment_timings(
    hook: str,
    body: list[str],
    closer: str,
    duration: float,
) -> list[tuple[float, float]]:
    """댓글 인용 문장의 (start, end) 타이밍 목록을 반환한다.

    video.py에서 shake 효과 및 효과음 타이밍 계산에 사용된다.
    """
    sentences = [hook] + list(body) + [closer]
    timings = _proportional_timings(sentences, duration)

    result: list[tuple[float, float]] = []
    for i, (sentence, timing) in enumerate(zip(sentences, timings)):
        # hook(0)과 closer(last)는 제외, body만 검사
        if 0 < i < len(sentences) - 1 and _is_comment_sentence(sentence):
            result.append(timing)
    return result


def write_ass_file(
    hook: str,
    body: list[str],
    closer: str,
    duration: float,
    mood: str,
    fontname: str,
    output_path: Path,
    width: int = 1080,
    height: int = 1920,
) -> Path:
    """ASS 파일을 output_path에 저장하고 경로를 반환."""
    content = build_ass(hook, body, closer, duration, mood, fontname, width, height)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8-sig")   # BOM: libass 호환

    n_sentences = 1 + len(body) + 1
    logger.info("ASS 자막 생성: %s (%d 문장, mood=%s)", output_path.name, n_sentences, mood)
    return output_path
