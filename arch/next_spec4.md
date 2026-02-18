# WaggleBot 썰렌더러 최종 수정 지시서 (v3 Final)

## 영상 분석 + 동료 피드백 종합

### 현재 상태 (waggle_test2.mp4 분석)
- ❌ 텍스트 왼쪽 정렬 (x=99 고정)
- ❌ 폰트 너무 작음 (46px)
- ❌ Clear 로직 미작동 (5개 이상 누적됨)
- ❌ 이전 텍스트 흐림 처리 안 됨 (모두 검정색)
- ❌ 댓글 배경 박스 없음
- ❌ 효과음 싱크 늦음

### 최종 목표 (동료 설계서 반영)
- ✅ **초대형 폰트 (85px)** + 중앙 정렬
- ✅ **3줄 단위 페이지 넘김** (완전 Clear 방식)
- ✅ 깔끔한 화면 전환 (카드 뉴스 스타일)

---

## 1. 레이아웃 & 타이포그래피 (전면 개편)

### 1.1 좌표 재정의

```python
# 캔버스 (1116x2000 → FFmpeg가 1080x1920으로 리사이즈)
CANVAS_W = 1116
CANVAS_H = 2000

# 텍스트 중앙 정렬 기준점
TEXT_X_CENTER = CANVAS_W // 2  # 558

# 본문 영역 (하향 조정하여 중앙 집중)
TEXT_Y_START = 500  # 기존 390에서 110px 아래로

# 최대 텍스트 폭 (중앙 기준 좌우 475px)
MAX_TEXT_WIDTH = 950  # 좌우 여백 83px 확보

# 상단 고정 영역 (기존 유지)
TEXT_Y_TITLE = 265
TEXT_Y_SEP = 330
TEXT_Y_META = 340
```

### 1.2 폰트 크기 (1.8배 확대)

```python
# 🔥 CRITICAL: 폰트 크기 대폭 확대
BODY_FONT_SIZE = 85      # 46px → 85px (주목도 향상)
COMMENT_FONT_SIZE = 70   # 40px → 70px (댓글도 크게)
TITLE_FONT_SIZE = 52     # 타이틀은 약간만 키움
META_FONT_SIZE = 32      # 메타 정보

# 줄 높이 (폰트 확대에 따른 행간 조정)
LINE_HEIGHT = int(BODY_FONT_SIZE * 1.4)           # 119px
SENTENCE_GAP = int(LINE_HEIGHT * 0.4)             # 48px
COMMENT_LINE_HEIGHT = int(COMMENT_FONT_SIZE * 1.4)  # 98px
```

**이유:**
- 모바일 쇼츠는 세로로 빠르게 스와이프하며 봄
- 작은 글씨는 눈에 안 들어옴 → 초대형 폰트 필수
- 템플릿이 약 3% 축소되므로 원본을 더 크게

---

## 2. 중앙 정렬 구현 (CRITICAL)

### 2.1 핵심 원리

**각 줄마다 폭을 측정하고 중앙 x 좌표 계산:**

```python
def create_ssul_frame(...):
    # ...
    
    for entry in text_history:
        lines = entry["lines"]  # 이미 래핑된 줄 리스트
        section = entry["section"]
        is_new = entry.get("is_new", False)
        is_comment = section == "comment"

        # 색상 (이전/새 문장 구분)
        color = "#000000" if is_new else "#666666"
        
        # 폰트 선택
        font = font_comment if is_comment else font_body
        lh = COMMENT_LINE_HEIGHT if is_comment else LINE_HEIGHT
        
        # 🔥 댓글 배경 박스 (블록 단위로 먼저 그리기)
        if is_comment and getattr(settings, "SSUL_COMMENT_BG_ENABLE", True):
            block_height = len(lines) * lh
            box_y_start = current_y - 10
            box_y_end = current_y + block_height + 10
            
            # 중앙 정렬 배경 박스 (좌우 여백 40px)
            box_left = 60
            box_right = CANVAS_W - 60
            
            draw.rounded_rectangle(
                [(box_left, box_y_start), (box_right, box_y_end)],
                radius=15,
                fill="#F5F5F5",
                outline="#DDDDDD",
                width=2
            )

        # 🔥 각 줄마다 중앙 정렬
        for line in lines:
            if current_y + lh > CANVAS_H - 100:  # 하단 여백 확보
                break
            
            # 줄 폭 측정
            line_width = font.getlength(line)
            
            # 중앙 x 좌표 계산
            center_x = (CANVAS_W - line_width) // 2
            
            # 댓글이면 약간 들여쓰기 (선택사항)
            if is_comment:
                center_x += 20  # 살짝 오른쪽
            
            # 중앙 정렬 렌더링
            draw.text((center_x, current_y), line, font=font, fill=color)
            current_y += lh
        
        current_y += SENTENCE_GAP
```

### 2.2 래핑 함수 수정

```python
def _wrap_text_pixel(
    text: str, 
    font: ImageFont.FreeTypeFont, 
    max_width: int
) -> list[str]:
    """픽셀 폭 기반 줄바꿈 (중앙 정렬용)."""
    if not text:
        return []

    lines = []
    words = text.split(' ')
    current_line = []
    current_width = 0
    space_width = font.getlength(' ')

    for word in words:
        word_width = font.getlength(word)
        
        # 단어가 max_width보다 길면 강제 분할
        if word_width > max_width:
            if current_line:
                lines.append(' '.join(current_line))
                current_line = []
                current_width = 0
            
            # 글자 단위 분할
            sub_word = ""
            sub_width = 0
            for char in word:
                char_width = font.getlength(char)
                if sub_width + char_width > max_width:
                    if sub_word:
                        lines.append(sub_word)
                    sub_word = char
                    sub_width = char_width
                else:
                    sub_word += char
                    sub_width += char_width
            
            if sub_word:
                current_line = [sub_word]
                current_width = sub_width
            continue

        # 일반 줄바꿈
        expected_width = current_width + word_width + (space_width if current_line else 0)
        
        if expected_width <= max_width:
            current_line.append(word)
            current_width = expected_width
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
            current_width = word_width

    if current_line:
        lines.append(' '.join(current_line))
    
    return lines
```

---

## 3. 페이지 넘김 로직 (3줄 단위 Clear)

### 3.1 핵심 로직

```python
# 설정값
MAX_LINES_PER_PAGE = 3  # 🔥 3줄 초과 시 화면 Clear

def render_ssul_video(...):
    # ...
    
    # 문장별 줄바꿈 미리 수행
    for sent in sentences:
        is_comment = sent["section"] == "comment"
        font = font_comment if is_comment else font_body
        
        lines = _wrap_text_pixel(sent["text"], font, MAX_TEXT_WIDTH)
        sent["lines"] = lines  # 저장
    
    # 프레임 생성
    text_history = []
    frame_paths = []
    
    for i, sent in enumerate(sentences):
        # 🔥 Step 1: 이전 문장 흐리게
        for prev in text_history:
            prev["is_new"] = False
        
        new_entry = {
            "lines": sent["lines"],
            "section": sent["section"],
            "is_new": True,
        }
        
        # 🔥 Step 2: 총 줄 수 계산
        current_total_lines = sum(len(e["lines"]) for e in text_history)
        new_line_count = len(new_entry["lines"])
        
        # 🔥 Step 3: 3줄 초과 시 완전 Clear
        if current_total_lines + new_line_count > MAX_LINES_PER_PAGE:
            text_history = []  # 화면 비우기
        
        # 🔥 Step 4: 새 문장 추가
        text_history.append(new_entry)
        
        # 프레임 생성
        frame_path = tmp_dir / f"frame_{i:03d}.png"
        create_ssul_frame(
            text_history, title, meta_text,
            template_path, frame_path, font_dir
        )
        frame_paths.append(frame_path)
```

### 3.2 페이지 넘김 예시

**시나리오 1: 짧은 문장 연속**
```
문장1 (1줄) → 화면에 그림 [총 1줄]
문장2 (1줄) → 추가 [총 2줄]
문장3 (1줄) → 추가 [총 3줄]
---
문장4 (1줄) → 3+1=4 > 3 → Clear 발동
               화면 비움 → 문장4만 표시 [총 1줄]
```

**시나리오 2: 긴 문장 등장**
```
문장1 (1줄) → [총 1줄]
문장2 (3줄) → 1+3=4 > 3 → Clear 발동
               화면 비움 → 문장2만 표시 [총 3줄]
```

**시나리오 3: 초대형 문장**
```
문장1 (5줄) → 0+5 > 3 이지만 문장1만 표시 [총 5줄]
               (단일 문장은 예외 처리 — 무조건 표시)
```

---

## 4. 효과음 싱크 개선

### 4.1 TTS 묵음 제거

```python
async def _tts_chunk_async(
    text: str, 
    idx: int, 
    output_dir: Path, 
    voice: str, 
    rate: str
) -> float:
    """TTS 생성 + 앞부분 묵음 제거."""
    out_path = output_dir / f"chunk_{idx:03d}.mp3"
    
    if not text or not text.strip():
        return 0.0

    # TTS 생성
    for attempt in range(2):
        try:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(out_path))
            break
        except Exception:
            if attempt == 0:
                await asyncio.sleep(0.5)
            else:
                logger.error("TTS 청크 %d 실패", idx)
                return 0.0
    
    # 🔥 앞부분 묵음 제거 (싱크 개선)
    trimmed = out_path.with_name(f"{out_path.stem}_trim.mp3")
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", str(out_path),
            "-af", "silenceremove=start_periods=1:start_threshold=-50dB:start_duration=0.1",
            "-c:a", "libmp3lame", "-q:a", "2",
            str(trimmed)
        ], capture_output=True, check=True, timeout=10)
        
        if trimmed.exists() and trimmed.stat().st_size > 0:
            trimmed.replace(out_path)
    except Exception as e:
        logger.warning("묵음 제거 실패 (원본 사용): %s", e)
    
    return _get_audio_duration(out_path)
```

### 4.2 효과음 타이밍 조정

```python
# 🔥 효과음을 더 앞당김 (텍스트와 동시 재생)
GLOBAL_SFX_OFFSET = -0.15  # -0.05에서 -0.15로

def _build_sfx_filter(...):
    # ...
    for i, (sent, t_start, (sfx_file, vol)) in enumerate(zip(sentences, timings, sfx_choices)):
        # 긴 효과음(ding, error)은 추가로 더 당김
        lead_in = 0.15 if sfx_file in ("ding.mp3", "error.mp3", "shutter.mp3") else 0.0
        
        # 🔥 최종 타이밍
        final_delay = t_start - lead_in + GLOBAL_SFX_OFFSET
        delay_ms = max(0, int(final_delay * 1000))
        
        # ...
```

---

## 5. settings.py 최종 설정값

```python
# ==================== 썰 렌더러 v3 ====================

# 폰트 크기 (대폭 확대)
SSUL_FONT_SIZE_BODY: int = 85        # 본문 (기존 46px)
SSUL_FONT_SIZE_COMMENT: int = 70     # 댓글 (기존 40px)
SSUL_FONT_SIZE_TITLE: int = 52       # 타이틀
SSUL_FONT_SIZE_META: int = 32        # 메타 정보

# 레이아웃
SSUL_LINE_HEIGHT_SCALE: float = 1.4  # 줄 간격 배수
SSUL_SENTENCE_GAP_SCALE: float = 0.4 # 문장 간격 배수
SSUL_TEXT_Y_START: int = 500         # 본문 시작 y (중앙 집중)
SSUL_MAX_TEXT_WIDTH: int = 950       # 최대 텍스트 폭

# 페이지 넘김
SSUL_MAX_LINES_PER_PAGE: int = 3     # 3줄 초과 시 Clear

# 색상
SSUL_PREV_TEXT_COLOR: str = "#666666"  # 이전 문장 (흐림)
SSUL_NEW_TEXT_COLOR: str = "#000000"   # 새 문장 (진함)

# 댓글 스타일
SSUL_COMMENT_BG_ENABLE: bool = True    # 댓글 배경 박스
SSUL_COMMENT_BG_COLOR: str = "#F5F5F5" # 연한 회색
SSUL_COMMENT_BORDER_COLOR: str = "#DDDDDD"
SSUL_COMMENT_BORDER_RADIUS: int = 15   # 모서리 둥글기

# 효과음
SSUL_SFX_OFFSET: float = -0.15         # 효과음 타이밍 오프셋 (초)

# 기타
SSUL_TEMPLATE_PATH: Path = ASSETS_DIR / "backgrounds" / "base_template.png"
SSUL_AUDIO_DIR: Path = ASSETS_DIR / "audio"
SSUL_TTS_VOICE: str = "ko-KR-SunHiNeural"
SSUL_TTS_RATE: str = "+25%"
SSUL_META_RANDOMIZE: bool = True
```

---

## 6. 전체 렌더링 플로우 (최종)

```python
def render_ssul_video(post, script, output_path: Path | None = None) -> Path:
    """썰 렌더러 v3 — 초대형 폰트 + 중앙 정렬 + 3줄 페이지 넘김."""
    
    # Step 1: 설정 로드
    font_body = _load_font(font_dir, "NotoSansKR-Medium.ttf", settings.SSUL_FONT_SIZE_BODY)
    font_comment = _load_font(font_dir, "NotoSansKR-Regular.ttf", settings.SSUL_FONT_SIZE_COMMENT)
    # ...
    
    # Step 2: 문장 구조화
    sentences = []
    sentences.append({"text": script.hook, "section": "hook"})
    for body_text in script.body:
        is_quote = any(q in body_text for q in ('"', "'", "\u201c", "\u201d"))
        section = "comment" if is_quote else "body"
        sentences.append({"text": body_text, "section": section})
    sentences.append({"text": script.closer, "section": "closer"})
    
    # Step 3: 문장별 줄바꿈 미리 수행
    for sent in sentences:
        is_comment = sent["section"] == "comment"
        font = font_comment if is_comment else font_body
        
        lines = _wrap_text_pixel(sent["text"], font, settings.SSUL_MAX_TEXT_WIDTH)
        sent["lines"] = lines
    
    # Step 4: TTS 생성 (묵음 제거 포함)
    durations = _run_async(_generate_all_chunks(sentences, tmp_dir, voice, rate))
    timings = [sum(durations[:i]) for i in range(len(durations))]
    
    # Step 5: 효과음 선택
    sfx_choices = [_get_sfx_for_sentence(s["section"], s["text"]) for s in sentences]
    
    # Step 6: TTS 오디오 병합
    chunk_paths = [tmp_dir / f"chunk_{i:03d}.mp3" for i in range(len(sentences))]
    merged_tts = tmp_dir / "merged_tts.mp3"
    _merge_tts_chunks(chunk_paths, merged_tts)
    
    # Step 7: 프레임 생성 (3줄 페이지 넘김)
    title = (post.title or "")[:40]
    meta_text = _generate_meta_text()
    
    text_history = []
    frame_paths = []
    
    for i, sent in enumerate(sentences):
        # 이전 문장 흐리게
        for prev in text_history:
            prev["is_new"] = False
        
        new_entry = {
            "lines": sent["lines"],
            "section": sent["section"],
            "is_new": True,
        }
        
        # 줄 수 체크
        current_total_lines = sum(len(e["lines"]) for e in text_history)
        new_line_count = len(new_entry["lines"])
        
        # 3줄 초과 시 Clear
        if current_total_lines + new_line_count > settings.SSUL_MAX_LINES_PER_PAGE:
            text_history = []
        
        text_history.append(new_entry)
        
        # 프레임 생성 (중앙 정렬)
        frame_path = tmp_dir / f"frame_{i:03d}.png"
        create_ssul_frame(
            text_history, title, meta_text,
            template_path, frame_path, font_dir
        )
        frame_paths.append(frame_path)
    
    # Step 8: FFmpeg concat + 효과음 믹싱
    concat_file = tmp_dir / "concat_list.txt"
    with open(concat_file, "w", encoding="utf-8") as f:
        for fp, dur in zip(frame_paths, durations):
            f.write(f"file '{fp.resolve()}'\n")
            f.write(f"duration {dur:.4f}\n")
        if frame_paths:
            f.write(f"file '{frame_paths[-1].resolve()}'\n")
    
    extra_inputs, sfx_filter = _build_sfx_filter(sentences, timings, sfx_choices, audio_dir)
    codec = _resolve_codec()
    enc_args = _get_encoder_args(codec)
    
    video_filter = "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2[vout]"
    filter_complex = f"{video_filter};{sfx_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_file),
        "-i", str(merged_tts),
        *extra_inputs,
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        *enc_args,
        "-c:a", "aac", "-b:a", "192k", "-r", "30",
        str(output_path),
    ]
    
    logger.info("[ssul] FFmpeg 시작: %s", output_path.name)
    subprocess.run(cmd, capture_output=True, check=True)
    
    return output_path
```

---

## 7. 테스트 체크리스트

수정 후 반드시 확인:

### 필수 (P0)
- [ ] **모든 텍스트가 가로 중앙 정렬** (각 줄마다 x 좌표 동적 계산)
- [ ] **폰트 크기 85px** (화면 대비 시원하게 큼)
- [ ] **3줄 초과 시 화면 완전 Clear** (이전 텍스트 모두 제거)
- [ ] **새 문장 검정(#000), 이전 문장 회색(#666)**

### 중요 (P1)
- [ ] **댓글에 연한 회색 배경 박스** (F5F5F5, 둥근 모서리)
- [ ] **효과음이 텍스트와 동시 재생** (±0.05초 이내)
- [ ] **TTS 앞부분 묵음 제거** (silenceremove 필터)

### 선택 (P2)
- [ ] 타이틀/메타 정보 정상 표시
- [ ] 최종 영상 1080x1920 해상도
- [ ] 노란 테두리 정상 (잘림 없음)

---

## 8. 우선순위 및 예상 작업 시간

| 순위 | 작업 | 파일 | 시간 |
|------|------|------|------|
| **P0** | 중앙 정렬 (각 줄 x 계산) | `ssul_renderer.py` → `create_ssul_frame()` | 20분 |
| **P0** | 폰트 크기 85px | `ssul_renderer.py` → 상수 변경 | 2분 |
| **P0** | 3줄 Clear 로직 | `ssul_renderer.py` → `render_ssul_video()` | 15분 |
| **P0** | 이전 텍스트 흐림 | `ssul_renderer.py` → `create_ssul_frame()` | 5분 |
| **P1** | TTS 묵음 제거 | `ssul_renderer.py` → `_tts_chunk_async()` | 10분 |
| **P1** | 댓글 배경 박스 | `ssul_renderer.py` → `create_ssul_frame()` | 8분 |
| **P1** | SFX 타이밍 조정 | `ssul_renderer.py` → `_build_sfx_filter()` | 3분 |
| **P2** | settings.py 추가 | `config/settings.py` | 5분 |

**총 예상 시간: 68분**

---

## 9. 핵심 변경 요약

### Before (현재)
- 왼쪽 정렬 + 작은 폰트(46px)
- 5문장 누적 → FIFO 스크롤
- 효과음 늦음
- 이전 텍스트 흐림 안 됨

### After (v3)
- **중앙 정렬 + 초대형 폰트(85px)**
- **3줄 단위 완전 Clear (페이지 넘김)**
- **효과음 동시 재생 (묵음 제거)**
- **이전 텍스트 #666666 흐림 처리**
- **댓글 배경 박스**

### 기대 효과
1. **가독성 혁신** — 글자가 화면을 꽉 채워 모바일에서 눈에 확 들어옴
2. **집중도 향상** — 최대 3줄만 표시되어 시선 분산 없음
3. **깔끔한 전개** — 페이지 넘김 방식으로 명쾌한 스토리 전개
4. **타이밍 정확도** — 효과음과 텍스트가 딱 맞아떨어짐

---

## 10. 주의사항

### PIL 버전 호환성
```python
# ❌ PIL 구버전에서 에러 발생
draw.text((x, y), text, anchor="mm")

# ✅ 직접 계산 (모든 버전 호환)
line_width = font.getlength(line)
center_x = (CANVAS_W - line_width) // 2
draw.text((center_x, y), text)
```

### 단일 문장 예외 처리
```python
# 3줄 초과 문장도 무조건 표시 (잘리는 것보다 나음)
if new_line_count > MAX_LINES_PER_PAGE:
    # 경고 로그
    logger.warning("문장 %d: %d줄 (최대 %d줄 초과)", i, new_line_count, MAX_LINES_PER_PAGE)
    # 그래도 표시
    text_history = [new_entry]
```

### FFmpeg 타임아웃
```python
# 긴 영상 처리 시 타임아웃 방지
subprocess.run(cmd, capture_output=True, check=True, timeout=600)  # 10분
```

---

## 최종 체크

이 작업지시서는 **영상 분석 결과**와 **동료 설계서(next_spec3.md)**를 100% 반영했습니다.

핵심은:
1. 🎯 **중앙 정렬** (각 줄마다 동적 x 계산)
2. 🔠 **초대형 폰트** (85px)
3. 📄 **3줄 페이지 넘김** (완전 Clear)

이 세 가지만 제대로 구현하면 즉시 품질 혁신됩니다.
