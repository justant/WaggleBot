# WaggleBot — 양산형 쇼츠 스타일 렌더러 작업지시서

## 목표
기존 `ai_worker/video.py`의 Ken Burns + 이미지 슬라이드쇼 방식을 대체하거나 병렬 지원하는  
**"누적 텍스트 + 효과음" 쇼츠 렌더러**를 구현한다.

---

## 리소스 실측 스펙 (직접 측정값)

### base_template.png
```
파일 경로: assets/backgrounds/base_template.png
이미지 크기: 1116 x 2000 px
※ YouTube Shorts 표준(1080x1920)이 아님 → 렌더링 시 1080x1920으로 리사이즈 필요

레이아웃 실측:
  헤더 (보라색 바):  y=0   ~ y=176  (높이 176px)
  노란 테두리 상단:  y=176 ~ y=251
  카드 영역 상단:    y=251
  카드 LEFT:         x=59
  카드 RIGHT:        x=1056  (폭 997px)
  카드 BOTTOM:       y=1925
  노란 테두리 두께:  좌우 약 59px

텍스트 안전 영역 (카드 내부 패딩 40px 기준):
  x: 99  ~ 1016  (가로 917px)
  y: 291 ~ 1885  (세로 1594px, 스크롤 대상)
```

### 효과음 파일 (assets/audio/)
```
pop.mp3     — 0.768s  : 자막 문장 등장 시 (기본)
click.mp3   — 0.183s  : 후킹 문장 첫 글자 등장 시
ding.mp3    — 1.306s  : 반전/핵심 문장 강조
error.mp3   — 1.097s  : 부정적/고구마 내용 문장
shutter.mp3 — 1.280s  : 댓글 인용 문장 등장 시
swoosh.mp3  — 0.183s  : 화면 전환 (섹션 간)
```

---

## 구현 대상 파일

| 파일 | 작업 |
|------|------|
| `ai_worker/ssul_renderer.py` | **신규 생성** — 핵심 렌더러 |
| `ai_worker/video.py` | `render_video()` 에 `style` 파라미터 추가 (`"ssul"` 분기) |
| `ai_worker/processor.py` | `Content.summary_text`의 `style` 필드에 따라 렌더러 선택 |
| `config/settings.py` | 새 설정값 추가 |
| `assets/audio/` | 효과음 파일 위치 (이미 준비됨) |

---

## 핵심 구현 로직

### 1. 전체 파이프라인

```
Content.summary_text (JSON) 파싱
  → 섹션별 문장 분리: hook(1문장) / body(N문장) / closer(1문장)
  → 문장별 TTS 청크 생성: chunk_0.mp3, chunk_1.mp3 ...
  → 문장별 PIL 이미지 생성: frame_0.png, frame_1.png ...  ← 누적 텍스트 방식
  → FFmpeg concat: 각 이미지를 해당 청크 오디오 길이만큼 표시
  → 효과음 믹싱: FFmpeg amix 필터
  → 최종 출력: 1080x1920 mp4
```

### 2. PIL 이미지 생성 — `create_ssul_frame()`

```python
def create_ssul_frame(
    text_history: list[dict],   # [{"text": str, "section": str, "is_new": bool}, ...]
    title: str,
    meta_text: str,             # "익명의 유저  |  22:29  |  조회수 48만"
    template_path: Path,
    output_path: Path,
    font_dir: Path,
) -> Path:
```

**좌표계 (1116x2000 기준, 최종 1080x1920 리사이즈):**

```
헤더 제목 영역:    건드리지 않음 (템플릿에 포함됨)
게시글 타이틀:     x=99, y=265, font_size=46, bold, color=#1A1A1A
구분선:            (99, 330) → (1016, 330), color=#DDDDDD, width=2
메타 정보:         x=99, y=340, font_size=28, color=#888888
본문 시작 y:       390
```

**본문 텍스트 렌더링 규칙:**
- 한 줄 최대 글자 수: `20자` (textwrap.wrap width=20)
- 본문 폰트: `NotoSansKR-Medium.ttf`, size=46
- 댓글 인용 폰트: `NotoSansKR-Regular.ttf`, size=40, color=`#555555`, 들여쓰기 30px
- 줄 높이(line_height): `font_size * 1.45` (= 약 67px)
- 문장 간 여백: `line_height * 0.6` (= 약 40px)
- **새로 추가된 문장**: color=`#000000` (진하게)
- **이전 누적 문장**: color=`#444444` (흐리게 — 읽힌 느낌)

**화면 오버플로우 처리:**
```
current_y > 1820 이 되면 text_history 앞부분(오래된 문장)을 pop(0) 후 재계산
→ 텍스트가 아래에서 위로 밀려 올라가는 자연스러운 스크롤 효과
```

### 3. TTS 청크 생성 — 문장 단위

```python
# edge_tts 사용 예
async def tts_chunk(text: str, idx: int, output_dir: Path) -> float:
    """반환값: 오디오 길이(초)"""
    communicate = edge_tts.Communicate(
        text,
        voice="ko-KR-SunHiNeural",   # 설정값으로 오버라이드 가능
        rate="+25%",                   # 쇼츠 호흡에 맞춘 빠른 속도
    )
    out_path = output_dir / f"chunk_{idx:03d}.mp3"
    await communicate.save(str(out_path))
    return get_audio_duration(out_path)   # ffprobe로 측정
```

**문장 분리 로직:**
```python
# summary_text JSON 기준
sentences = []
sentences.append({"text": script["hook"],   "section": "hook"})
for s in script["body"]:
    sentences.append({"text": s,             "section": "body"})
sentences.append({"text": script["closer"], "section": "closer"})
```

### 4. FFmpeg concat 명령어

```python
# concat_list.txt 생성
# file 'frame_000.png'
# duration 3.52       ← chunk_000.mp3 길이
# file 'frame_001.png'
# duration 4.10
# ...

cmd = [
    "ffmpeg", "-y",
    "-f", "concat", "-safe", "0", "-i", "concat_list.txt",
    "-i", "merged_tts.mp3",         # TTS 청크들을 concat한 오디오
    "-filter_complex", build_sfx_filter(sentence_timings),  # 효과음 믹싱
    "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
    "-c:v", settings.VIDEO_CODEC,   # h264_nvenc or libx264
    "-c:a", "aac", "-b:a", "192k",
    "-r", "30",
    str(output_path)
]
```

### 5. 효과음 믹싱 필터 — `build_sfx_filter()`

효과음은 TTS 오디오와 별도 트랙으로 amix. 각 문장의 시작 타임스탬프(`t_start`)를 기준으로 배치.

```python
def get_sfx_for_sentence(section: str, text: str) -> tuple[str, float]:
    """(sfx_filename, volume) 반환"""
    if section == "hook":
        return ("click.mp3", 0.6)
    if "'" in text or '"' in text:        # 댓글 인용
        return ("shutter.mp3", 0.5)
    if any(w in text for w in ["ㄷㄷ", "충격", "반전", "실화"]):
        return ("ding.mp3", 0.4)
    return ("pop.mp3", 0.45)              # 기본값
```

**섹션 전환(hook→body, body→closer) 시에는 `swoosh.mp3` 추가 (volume=0.35)**

FFmpeg amix 필터 구조:
```
[1:a]               ← TTS concat 오디오 (메인)
[2:a]adelay=...     ← pop.mp3 (문장1 시작)
[3:a]adelay=...     ← shutter.mp3 (문장2 시작)
...
amix=inputs=N:normalize=0  ← 정규화 off (볼륨 비율 직접 제어)
```

---

## settings.py 추가 항목

```python
# 썰 렌더러
SSUL_TEMPLATE_PATH: Path = BASE_DIR / "assets/backgrounds/base_template.png"
SSUL_AUDIO_DIR: Path = BASE_DIR / "assets/audio"
SSUL_TTS_VOICE: str = "ko-KR-SunHiNeural"
SSUL_TTS_RATE: str = "+25%"
SSUL_FONT_BODY: Path = BASE_DIR / "assets/fonts/NotoSansKR-Medium.ttf"
SSUL_FONT_TITLE: Path = BASE_DIR / "assets/fonts/NotoSansKR-Bold.ttf"
SSUL_META_RANDOMIZE: bool = True   # 조회수/시간 랜덤 생성 여부
```

---

## 메타 정보 랜덤화

```python
import random

def generate_meta_text() -> str:
    if not settings.SSUL_META_RANDOMIZE:
        return "익명의 유저  |  22:29  |  조회수 48만"
    views = random.choice(["12만", "28만", "41만", "55만", "63만", "87만"])
    hour  = random.randint(8, 23)
    minute = random.choice(["03", "17", "29", "44", "51"])
    return f"익명의 유저  |  {hour:02d}:{minute}  |  조회수 {views}"
```

---

## processor.py 연동

```python
# Content.summary_text JSON에 render_style 필드로 렌더러 선택
render_style = script.get("render_style", "layout")   # 기본값: ssul

if render_style == "ssul":
    from ai_worker.ssul_renderer import render_ssul_video
    output_path = render_ssul_video(content, tmp_dir)
else:
    output_path = render_video(content, tmp_dir)     # 기존 Ken Burns 방식
```

---

## 주의사항 및 제약

### VRAM
- PIL 이미지 생성, TTS는 GPU 불필요 → `gpu_manager` 컨텍스트 없이 실행
- FFmpeg 인코딩만 `h264_nvenc` 사용 → 기존 `settings.VIDEO_CODEC` 그대로 사용

### 이미지 리사이즈
- 템플릿 원본은 **1116x2000** (YouTube Shorts 표준 아님)
- FFmpeg `scale=1080:1920` 필터로 출력 시 리사이즈
- PIL에서 좌표 계산은 **1116x2000 기준**으로 하고, FFmpeg가 최종 변환

### 폰트
- `assets/fonts/NotoSansKR-Bold.ttf`, `NotoSansKR-Medium.ttf`, `NotoSansKR-Regular.ttf` 존재 확인 필수
- 없으면 `fc-list` 또는 시스템 폰트 경로 fallback 처리

### 임시 파일
- 모든 청크 이미지/오디오는 `media/tmp/{content_id}/` 에 생성
- 렌더링 완료 후 삭제 (`shutil.rmtree`)

### 에러 핸들링
- TTS 청크 실패 시 해당 문장 재시도 1회 후 스킵 (공백 오디오 0.5s 삽입)
- FFmpeg 실패 시 `PostStatus.FAILED` 로 상태 변경 + 로그 기록

---

## 원본 설계서 대비 보완된 사항

1. **템플릿 실측**: 1116x2000 (설계서의 1080x1920 아님) → FFmpeg 리사이즈로 해결
2. **효과음 길이 측정**: `ding.mp3`(1.3s), `error.mp3`(1.1s)는 길기 때문에 문장 시작보다 0.1s 앞당겨 배치해야 TTS와 맞음
3. **좌표계 명확화**: 설계서의 "y=220 부근" 등 대략적 수치를 실측값으로 대체
4. **error.mp3 활용**: 설계서에 없던 활용 방안 추가 (부정적 내용 문장)
5. **섹션 전환 swoosh**: 설계서 누락 — hook→body, body→closer 전환 시 추가
6. **`render_style` 필드**: 기존 Ken Burns 렌더러와 공존 가능하도록 분기 설계
7. **오버플로우 재계산**: 설계서의 "pop(0)으로 지우고 재귀" 방식 대신 while 루프로 안전하게 처리
