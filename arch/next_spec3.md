# WaggleBot — 썰 렌더러 개선 작업지시서 v2

## 동료 피드백 분석 및 실측 결과

### 발견된 문제점

1. **텍스트 가로 오버플로우** ✅ 부분 해결됨
   - 픽셀 기반 래핑으로 개선되었으나, **댓글 인용문 들여쓰기가 미반영**
   - 실측: "베스트 댓글 인용: '마음부터...'" → 1075px (최대 917px 초과)
   - 원인: `_wrap_text_pixel()`에서 `max_width` 계산 시 `COMMENT_INDENT` 빼지 않음

2. **페이지 넘김 로직 과도** ⚠️ 로직 재설계 필요
   - 동료 의견: "3줄 넘어가면 클리어" → **실제론 더 많은 컨텍스트 유지 필요**
   - 현재: `TEXT_Y_OVERFLOW_MAX = 1820` → 본문 영역(y=390~1925) 대비 너무 빡빡
   - 제안: **하이브리드 방식** — 5~6문장까지 누적, 그 이후 오래된 것부터 스크롤 아웃

3. **효과음 싱크** ⚠️ 추가 테스트 필요
   - `GLOBAL_SFX_OFFSET = -0.05` 적용되었으나 여전히 늦게 들릴 수 있음
   - TTS 앞부분 묵음(padding) 때문일 가능성 → FFmpeg `silenceremove` 필터 적용

4. **댓글 들여쓰기 미적용** ❌ 버그
   - 코드상 `COMMENT_INDENT = 30` 정의되었으나 `create_ssul_frame()`에서만 사용
   - `_wrap_text_pixel()`에 전달 안 됨 → 댓글이 일반 텍스트와 같은 x 좌표

5. **이전 문장 색상 너무 연함** ⚠️ UX 개선 필요
   - `#444444` → 배경이 밝아서 거의 안 보임
   - 제안: `#666666` 또는 `#777777` (약간 더 진하게)

---

## 핵심 개선 사항

### 1. 텍스트 래핑 로직 수정 — 들여쓰기 반영

**현재 문제:**
```python
# _wrap_text_pixel() 호출 시
lines = _wrap_text_pixel(sent["text"], font, MAX_TEXT_WIDTH - indent)
```
이 코드는 `render_ssul_video()`에만 있고, `_wrap_text_pixel()` 함수 내부는 `max_width`를 그대로 받음.

**해결:**
```python
def _wrap_text_pixel(
    text: str, 
    font: ImageFont.FreeTypeFont, 
    max_width: int,
    indent: int = 0  # 새로 추가
) -> list[str]:
    """들여쓰기를 고려한 픽셀 기반 래핑."""
    # 실제 사용 가능 폭은 max_width - indent
    available_width = max_width - indent
    # ... 기존 로직에서 max_width 대신 available_width 사용
```

**호출 예시:**
```python
is_comment = sent["section"] == "comment"
indent = COMMENT_INDENT if is_comment else 0
lines = _wrap_text_pixel(sent["text"], font, MAX_TEXT_WIDTH, indent)
```

---

### 2. 하이브리드 오버플로우 전략 — 스마트 스크롤

**동료 피드백 재해석:**
"3줄 넘어가면 클리어"는 **"화면이 너무 복잡해지기 전에 정리해야 한다"**는 의도.  
하지만 **모든 컨텍스트를 지우면 스토리 흐름 단절**.

**제안: 5-2-5 전략**
```
- 최대 5문장까지 누적 표시
- 6번째 문장 등장 시: 가장 오래된 2문장 제거 (FIFO)
- 항상 최근 5문장만 화면에 유지
```

**구현:**
```python
MAX_VISIBLE_SENTENCES = 5
SCROLL_OUT_COUNT = 2  # 한 번에 제거할 문장 개수

# 문장 추가 전 체크
if len(text_history) >= MAX_VISIBLE_SENTENCES:
    # 오래된 문장 N개 제거
    text_history = text_history[SCROLL_OUT_COUNT:]

text_history.append(new_entry)
```

**장점:**
- 컨텍스트 유지 (5문장 = 약 15~20초 분량)
- 화면 복잡도 제한
- 긴 게시글도 자연스럽게 흐름

**Y좌표 계산 불필요** — 문장 개수로만 제어하므로 높이 예측 오류 없음.

---

### 3. TTS 묵음 제거 — 효과음 싱크 개선

**문제:**
Edge-TTS가 생성한 MP3 앞부분에 0.1~0.3초 묵음 패딩이 있을 수 있음.  
→ FFmpeg concat 시 타이밍 누적 오차 발생.

**해결: silenceremove 필터 적용**

```python
async def _tts_chunk_async(...):
    # 기존 TTS 생성
    await communicate.save(str(out_path))
    
    # 앞부분 묵음 제거 (0.1초 이하 무음 자동 잘라냄)
    trimmed_path = out_path.with_suffix('.trimmed.mp3')
    subprocess.run([
        "ffmpeg", "-y", "-i", str(out_path),
        "-af", "silenceremove=start_periods=1:start_threshold=-50dB:start_duration=0.1",
        "-c:a", "libmp3lame", str(trimmed_path)
    ], capture_output=True, check=True)
    
    # 원본 교체
    trimmed_path.replace(out_path)
    
    return _get_audio_duration(out_path)
```

**추가 조정:**
```python
# GLOBAL_SFX_OFFSET 미세 조정
GLOBAL_SFX_OFFSET = -0.08  # -0.05에서 살짝 더 당김
```

---

### 4. 이전 문장 색상 개선

```python
# 현재
color = "#000000" if is_new else "#444444"

# 개선
color = "#000000" if is_new else "#666666"  # 더 진한 회색
```

**이유:**
- 템플릿 배경이 크림/노란색(#FAF6DB)이라 대비가 약함
- #666666 정도는 되어야 읽기 편함
- 새 문장(#000000)과의 차이도 명확히 유지

---

### 5. 댓글 시각적 차별화 강화 (선택 사항)

현재 댓글은 들여쓰기 + 작은 폰트만 다름. 추가 개선:

```python
def create_ssul_frame(...):
    # ...
    for entry in text_history:
        # ...
        if is_comment:
            # 배경 박스 그리기 (연한 회색)
            box_y_start = current_y - 5
            box_y_end = current_y + len(lines) * lh + 5
            draw.rectangle(
                [(TEXT_X + 10, box_y_start), (TEXT_X_RIGHT - 10, box_y_end)],
                fill="#F0F0F0",  # 아주 연한 회색 배경
                outline="#DDDDDD",
                width=1
            )
        
        # 텍스트는 박스 위에 그리기
        for line in lines:
            # ...
```

**효과:** 댓글 인용이 말풍선처럼 보여 시각적 재미 증가.

---

## 수정 대상 파일 및 우선순위

| 우선순위 | 파일 | 작업 |
|---------|------|------|
| **P0** | `ai_worker/ssul_renderer.py` | `_wrap_text_pixel()` 인자 추가, 5-2-5 스크롤 로직, TTS 묵음 제거 |
| **P0** | `ai_worker/ssul_renderer.py` | 이전 문장 색상 #666666 변경 |
| **P1** | `ai_worker/ssul_renderer.py` | `GLOBAL_SFX_OFFSET = -0.08` 미세 조정 |
| **P2** | `ai_worker/ssul_renderer.py` | 댓글 배경 박스 추가 (선택) |
| **P3** | `config/settings.py` | 새 설정값 추가 (MAX_VISIBLE_SENTENCES 등) |

---

## 설정값 추가 (settings.py)

```python
# 썰 렌더러 — 텍스트 오버플로우 제어
SSUL_MAX_VISIBLE_SENTENCES: int = 5      # 화면에 보이는 최대 문장 수
SSUL_SCROLL_OUT_COUNT: int = 2           # 오버플로우 시 제거할 문장 수
SSUL_PREV_TEXT_COLOR: str = "#666666"    # 이전 문장 색상
SSUL_NEW_TEXT_COLOR: str = "#000000"     # 새 문장 색상
SSUL_COMMENT_BG_ENABLE: bool = True      # 댓글 배경 박스 활성화
SSUL_SFX_OFFSET: float = -0.08           # 효과음 타이밍 오프셋 (초)
```

---

## 예상 효과

### Before (현재)
```
[문장1]
[문장2]
[문장3]  ← 여기서 Clear 발생 (컨텍스트 단절)
--- 화면 지워짐 ---
[문장4]
[문장5]
```

### After (개선)
```
[문장1] (흐림)
[문장2] (흐림)
[문장3] (흐림)
[문장4] (흐림)
[문장5] (진함) ← 새 문장
[문장6] (진함) ← 새 문장 추가 시 문장1,2 제거
---
[문장3] (흐림)
[문장4] (흐림)
[문장5] (흐림)
[문장6] (흐림)
[문장7] (진함)
```

**장점:**
- 스토리 흐름 유지
- 화면 복잡도 일정
- 댓글 인용 가독성 개선
- 효과음 싱크 정확도 향상

---

## 테스트 체크리스트

완료 후 다음 사항 확인:

- [ ] 긴 댓글 인용문이 화면 내에 정상 표시 (들여쓰기 포함)
- [ ] 5문장 이상 누적 시 오래된 것부터 FIFO 제거
- [ ] 새 문장(#000000)과 이전 문장(#666666) 대비 명확
- [ ] 효과음이 텍스트 등장과 동시에 (±0.05초 이내) 재생
- [ ] 댓글 인용 시 배경 박스 표시 (설정 활성화 시)
- [ ] 전체 영상 길이 20~60초 범위 내 (TTS 속도 적절)
- [ ] 1080x1920 해상도 정확히 출력
- [ ] 노란 테두리가 잘리지 않음 (FFmpeg 리사이즈 정상)

---

## 추가 권장 사항

### 1. 문장 분할 개선

현재 LLM이 반환한 `script.body` 배열을 그대로 사용하는데,  
문장이 너무 길면(3줄 이상) 자동으로 분할하는 로직 추가:

```python
def split_long_sentence(text: str, max_lines: int = 2) -> list[str]:
    """문장이 max_lines 이상이면 마침표/쉼표 기준으로 분할."""
    # 임시 래핑으로 줄 수 계산
    temp_lines = _wrap_text_pixel(text, font, MAX_TEXT_WIDTH)
    
    if len(temp_lines) <= max_lines:
        return [text]
    
    # 마침표나 쉼표 기준 분할
    separators = ['. ', ', ', '~ ', '! ']
    for sep in separators:
        if sep in text:
            parts = text.split(sep, 1)
            return split_long_sentence(parts[0] + sep.rstrip(), max_lines) + \
                   split_long_sentence(parts[1], max_lines)
    
    return [text]  # 분할 불가하면 그대로 반환
```

### 2. 프로파일링 로그

렌더링 각 단계의 시간을 측정해서 병목 파악:

```python
import time

logger.info("[ssul] TTS 생성 시작")
t0 = time.time()
durations = _run_async(_generate_all_chunks(...))
logger.info("[ssul] TTS 완료 (%.2fs)", time.time() - t0)

logger.info("[ssul] 이미지 생성 시작")
t1 = time.time()
# ... 프레임 생성 루프
logger.info("[ssul] 이미지 완료 (%.2fs)", time.time() - t1)
```

### 3. 에러 복구 강화

TTS 청크 실패 시 빈 오디오 생성 대신 **이전 청크 복제** 또는 **묵음 + 자막만 표시**:

```python
if dur == 0.0:
    logger.warning("[ssul] TTS 청크 %d 실패 — 자막만 표시", idx)
    # 0.5초 묵음 생성은 유지하되, 프레임에는 텍스트 표시
```

---

## 마무리

동료 피드백의 핵심은 **"너무 빨리 화면이 지워진다"**였고,  
실측 결과 **"댓글 들여쓰기 미반영"**, **"이전 텍스트 가독성 부족"** 문제도 발견됨.

**5-2-5 하이브리드 전략**으로 컨텍스트 유지하면서도 화면 복잡도를 관리하고,  
TTS 묵음 제거로 효과음 싱크를 개선하면 실제 양산형 쇼츠 영상과 거의 동일한 품질 달성 가능.
