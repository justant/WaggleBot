# ai_worker/renderer — 렌더러 모듈

9:16 쇼츠 영상의 프레임 생성, 자막 합성, FFmpeg 인코딩, 썸네일 생성을 담당하는 모듈.
Phase 8(FFmpeg 렌더링)의 실행 주체이며, 프리뷰/고화질 두 가지 렌더링 경로를 제공한다.

---

## 파일 구조

```
ai_worker/renderer/
├── __init__.py    # 퍼블릭 API re-export
├── layout.py      # 하이브리드 레이아웃 렌더러 v2 (메인 영상 생성)
├── video.py       # 레거시/프리뷰 렌더러 (슬라이드쇼 + ASS 자막)
├── subtitle.py    # ASS 동적 자막 생성
├── thumbnail.py   # YouTube 썸네일 생성 (1280×720)
└── README.md      # (이 파일)
```

---

## 퍼블릭 API

`__init__.py`에서 re-export하므로 `from ai_worker.renderer import ...`로 사용 가능.

| 심볼 | 출처 | 용도 |
|------|------|------|
| `render_layout_video()` | layout.py | ScriptData 기반 레이아웃 영상 렌더링 |
| `render_layout_video_from_scenes()` | layout.py | SceneDecision 목록 기반 렌더링 (메인 파이프라인) |
| `render_preview()` | video.py | 프리뷰 전용 저화질 렌더링 (480×854, CPU) |
| `generate_thumbnail()` | thumbnail.py | YouTube 썸네일 생성 (1280×720) |
| `get_thumbnail_path()` | thumbnail.py | 썸네일 저장 경로 반환 |

---

## 렌더링 경로 비교

| 항목 | layout.py (메인) | video.py (프리뷰) |
|------|-----------------|-------------------|
| 해상도 | 1080×1920 | 480×854 |
| 코덱 | h264_nvenc (GPU) ← libx264 폴백 | libx264 (항상 CPU) |
| 입력 | SceneDecision 목록 or ScriptData | Post + audio + summary_text |
| 프레임 생성 | PIL 정적 + LTX-Video 하이브리드 | 이미지 슬라이드쇼 (Ken Burns) |
| 자막 | 프레임에 직접 렌더링 (PIL) | ASS 파일 (FFmpeg subtitles) |
| BGM | scene_policy.json 기반 mood별 | assets/bgm/ 랜덤 |
| 용도 | 최종 업로드 영상 | 운영자 확인용 저화질 |

---

## layout.py — 상세 구조

### 의존성

```python
from config.settings import ASSETS_DIR, MEDIA_DIR
from ai_worker.video.video_utils import _nvenc_available, resize_clip_to_layout, loop_or_trim_clip
from ai_worker.tts.fish_client import synthesize, _warmup_model  # 런타임 import
from config.settings import load_pipeline_config, VOICE_DEFAULT   # 런타임 import
```

### 렌더링 11-Step 파이프라인

```
Step 1   문장 구조화     → sentences[{"text", "section", "audio", ...}]
Step 2   베이스 프레임    → _create_base_frame() — base_layout.png + 헤더 제목
Step 3   씬 배분 계획    → _plan_sequence() (render_layout_video 전용)
         또는 씬 변환    → _scenes_to_plan_and_sentences() (from_scenes 전용)
Step 4   이미지 다운로드  → _load_image() → img_cache dict
Step 5   TTS 생성/캐시   → _generate_tts_chunks() → chunk_*.wav
Step 6   TTS 청크 병합   → _merge_chunks() → merged_tts.wav
Step 7   줄바꿈 사전계산  → _wrap_korean() per sentence
Step 8   PIL 프레임 생성  → intro/img_text/text_only/img_only/outro 렌더러
Step 8.5 하이브리드 세그먼트 → _render_video_segment() / _render_static_segment()
Step 9   세그먼트 concat  → FFmpeg concat demuxer
Step 10  SFX 필터 구성   → _build_layout_sfx_filter() (현재 비활성화)
Step 11  FFmpeg 인코딩   → video + TTS + BGM → 최종 mp4
```

### 퍼블릭 함수

```python
def render_layout_video(
    post,                              # Post 객체 (id, title, images)
    script,                            # ScriptData (hook/body/closer)
    output_path: Path | None = None,   # 기본: media/video/{site}/post_{id}_SD.mp4
) -> Path:
```
- ScriptData 기반 직접 렌더링 (Step 1~3~11)
- `_plan_sequence()`로 이미지:텍스트 비율 기반 씬 배분

```python
def render_layout_video_from_scenes(
    post,
    scenes: list,                      # list[SceneDecision]
    output_path: Path | None = None,
    save_tts_cache: Path | None = None,
    tts_audio_cache: Path | None = None,
) -> Path:
```
- SceneDecision 목록 직접 렌더링 (Step 1~11, Step 3 스킵)
- `_scenes_to_plan_and_sentences()`로 내부 형식 변환
- intro 씬의 `bgm_path`를 추출하여 BGM 적용
- `save_tts_cache` / `tts_audio_cache`로 TTS 캐시 저장/로드

### 씬 렌더러 (내부)

| 함수 | 씬 타입 | 설명 |
|------|---------|------|
| `_render_intro_frame()` | intro | 베이스 프레임 그대로 (제목은 헤더에 고정) |
| `_render_img_text_frame()` | img_text | 이미지(900×900) + 하단 텍스트 |
| `_render_text_only_frame()` | text_only | 동적 Y좌표 누적 스태킹, comment 타입 색상 분기 |
| `_render_img_only_frame()` | img_only | 이미지 전체 화면 cover, 텍스트 없음 |
| `_render_outro_frame()` | outro | 대형 이미지 + 선택적 오버레이 텍스트 |

### 비디오 세그먼트 렌더러 (내부)

| 함수 | 역할 |
|------|------|
| `_render_video_segment()` | LTX-Video 클립을 base_frame 위에 합성 → mp4 세그먼트 |
| `_render_video_text_overlay()` | 비디오 위 텍스트를 투명 PNG 오버레이로 렌더링 |
| `_render_static_segment()` | 정적 PNG → duration 길이 mp4 변환 |
| `_get_scene_for_entry()` | plan entry → SceneDecision 매칭 (scene_idx → 텍스트 폴백) |

### 배분 알고리즘 (`_plan_sequence`)

```
ratio = 이미지수 / 본문문장수

ratio ≥ 0.8  → img_heavy  : 거의 모든 문장에 이미지 사용
ratio ≥ 0.3  → balanced   : 이미지 균등 간격 분배
ratio < 0.3  → text_heavy : text_only 위주, 앞에서 일부만 img_text
```

임계값은 `config/layout.json`의 `layout_algorithm.img_heavy_threshold` / `img_mixed_threshold`로 조정.

### SceneDecision → 내부 형식 변환 (`_scenes_to_plan_and_sentences`)

```
SceneDecision 목록
  ├─ intro  → sentences[{"text", "section":"hook", "audio"}] + plan[{"type":"intro"}]
  ├─ img_text → sentences[{"text", "section":"body", "audio", "block_type", "author"}]
  ├─ text_only → 각 text_line마다 별도 sentences/plan 엔트리 (누적 스태킹 호환)
  ├─ img_only → sentences (텍스트 있을 때만) + plan
  └─ outro  → sentences[{"text", "section":"closer", "audio"}]

text_lines 요소: str | {"text": str, "audio": str|None} 양쪽 모두 처리
```

### 주요 내부 함수 목록

| 함수 | 역할 |
|------|------|
| `_load_layout()` | config/layout.json 싱글톤 로드 |
| `_load_font()` | assets/fonts → fc-list 시스템 폰트 → PIL 기본 폴백 |
| `_apply_vf_weight()` | Variable Font 굵기 축 자동 설정 (Bold/Medium/Light) |
| `_wrap_korean()` | 픽셀 기반 한글 줄바꿈 (단어 → 글자 단위 강제 분리) |
| `_draw_centered_text()` | 중앙 정렬 텍스트 렌더링 (stroke 지원) |
| `_truncate()` | max_chars 초과 시 `..` 말줄임 |
| `_load_image()` | URL/로컬 이미지 로드 (DCInside 쿠키 워밍업, 재시도) |
| `_fit_cover()` | Cover 모드 — 비율 유지 + 중앙 크롭 |
| `_paste_rounded()` | 둥근 모서리 마스크 합성 |
| `_create_base_frame()` | base_layout.png + 헤더 제목 → 모든 프레임의 공통 베이스 |
| `_resolve_codec()` | h264_nvenc 우선, 불가 시 libx264 폴백 |
| `_get_encoder_args()` | 코덱별 FFmpeg 인코딩 인자 |
| `_get_audio_duration()` | ffprobe 오디오 길이 측정 |
| `_tts_chunk_async()` | 단일 문장 TTS 생성 (사전 오디오 재사용 or Fish Speech 호출) |
| `_generate_tts_chunks()` | plan 순서 TTS 일괄 생성 + intro 숨고르기 삽입 |
| `_merge_chunks()` | TTS 청크 FFmpeg concat 병합 |
| `_build_layout_sfx_filter()` | 씬 타입별 효과음 amix 필터 (현재 비활성화) |
| `_unpack_line()` | text_lines 요소에서 (text, audio_path) 추출 |
| `_escape_ffmpeg_text()` | FFmpeg drawtext용 이스케이프 |
| `_render_pipeline()` | Steps 2, 4~11 통합 실행 — 핵심 렌더링 엔진 |
| `_run_async()` | 코루틴 동기 실행 (이벤트 루프 감지) |

### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `CANVAS_W` | 1080 | 캔버스 너비 (layout.json 미로드 시 fallback) |
| `CANVAS_H` | 1920 | 캔버스 높이 |
| `HEADER_H` | 160 | 헤더 높이 |
| `HEADER_COLOR` | `#4A44FF` | 헤더 배경색 |
| `_INTRO_PAUSE_SEC` | 0.5 | 제목 TTS 후 본문 시작 전 숨고르기 (초) |

---

## video.py — 상세 구조

### 의존성

```python
from config.settings import ASSETS_DIR, MEDIA_DIR
from ai_worker.llm.client import ScriptData           # 런타임 import
from ai_worker.renderer.subtitle import write_ass_file, get_comment_timings  # 런타임 import
```

### 퍼블릭 함수

```python
def render_preview(
    post,                    # Post 객체 (id, site_code, origin_id, images)
    audio_path: Path,        # TTS 합성 오디오
    summary_text: str,       # Content.summary_text (JSON 문자열)
    cfg: dict[str, str],     # {"bgm_volume", "subtitle_font", ...}
) -> Path:
```
- 프리뷰 전용 — 항상 480×854, libx264(CPU), GPU 점유 없음
- ScriptData 파싱 → ASS 자막 생성 → 댓글 타이밍 추출
- 실패 시 drawtext 폴백 (마크다운 제거 + 한국어 줄바꿈)
- 출력: `media/video/{site_code}/post_{origin_id}_SD.mp4`

### 이미지 슬라이드쇼 (`_build_slideshow`)

```
이미지 다운로드 (DCInside 쿠키 세션 지원)
  → cover 모드 리사이즈 (MD5 캐시)
  → Ken Burns zoompan (짝수=줌인, 홀수=줌아웃)
  → xfade 장면 전환 체이닝
```

**전환 전략 (`_assign_transitions`):**
- 첫 전환 (hook → body): `circleopen` — 주의 집중
- 마지막 전환 (body → closer): `fadeblack` — 마무리
- 중간 전환: `slideleft` / `dissolve` / `fade` 순환

### 최종 합성 (`_compose_final`)

```
영상(슬라이드쇼 or 배경) + TTS 오디오
  + 자막 (ASS 우선 / drawtext 폴백)
  + BGM (sidechaincompress auto-ducking)
  + 댓글 흔들림 (geq 2px 사인파, 실패 시 제거 후 재시도)
  + 댓글 효과음 (현재 비활성화)
  → 최종 mp4
```

### 주요 내부 함수 목록

| 함수 | 역할 |
|------|------|
| `_check_nvenc()` | h264_nvenc 인코더 가용 여부 확인 (1회 캐시) |
| `_build_slideshow()` | 이미지 → Ken Burns + xfade 슬라이드쇼 mp4 |
| `_assign_transitions()` | N-1개 장면 전환 효과 결정 |
| `_build_background_loop()` | assets/backgrounds/ 랜덤 배경 루프 (없으면 검은 화면) |
| `_compose_final()` | 영상 + TTS + 자막 + BGM + SFX 합성 |
| `_build_shake_filter()` | 댓글 구간 0.3초 geq 흔들림 필터 |
| `_build_sfx_parts()` | 댓글 효과음 FFmpeg 입력/필터 (현재 비활성화) |
| `_get_encoder_args()` | 코덱별 인코딩 인자 |
| `_probe_duration()` | ffprobe 오디오 길이 측정 |
| `_download_image_with_retry()` | 이미지 다운로드 (재시도, DCInside 세션, 플레이스홀더 감지) |
| `_resize_cover()` | cover 모드 crop + resize |
| `_resolve_font_path()` | assets/fonts → 시스템 폰트 탐색 |
| `_strip_markdown()` | LLM 마크다운 서식 제거 |
| `_escape_drawtext()` | FFmpeg drawtext 이스케이프 |
| `_wrap_korean()` | 고정 폭 한국어 줄바꿈 (textwrap 기반) |
| `_get_dc_session()` | DCInside 이미지 다운로드 세션 (쿠키 워밍업) |
| `_is_dc_url()` | DCInside CDN URL 여부 확인 |

### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `_VIDEO_DIR` | `media/video/` | 영상 저장 디렉터리 |
| `_TEMP_DIR` | `media/tmp/` | 임시 파일 디렉터리 |
| `_IMG_CACHE_DIR` | `media/tmp/img_cache/` | 이미지 리사이즈 캐시 |
| `_nvenc_available` | `bool | None` | NVENC 가용 여부 (런타임 캐시) |

---

## subtitle.py — 상세 구조

### 의존성

- 표준 라이브러리만 사용 (logging, re, pathlib)

### 퍼블릭 함수

```python
def build_ass(
    hook: str, body: list, closer: str,
    duration: float, mood: str, fontname: str,
    width: int, height: int,
) -> str:
```
- ASS 자막 파일 전체 내용(문자열) 생성
- 글자 수 비례 타이밍 (`_proportional_timings`)
- 3막 구조: Hook → Body → Closer

```python
def write_ass_file(
    hook: str, body: list[str], closer: str,
    duration: float, mood: str, fontname: str,
    output_path: Path,
    width: int = 1080, height: int = 1920,
) -> Path:
```
- `build_ass()` 호출 → UTF-8-BOM 인코딩으로 파일 저장

```python
def get_comment_timings(
    hook: str, body: list, closer: str,
    duration: float,
) -> list[tuple[float, float]]:
```
- 댓글 인용 문장의 `(start, end)` 타이밍 목록 반환
- `video.py`에서 shake 효과 및 효과음 타이밍에 사용

### 스타일 프리셋 (4종 mood + CommentBubble)

| mood | Hook 색상 | Default 색상 | Comment 스타일 | 특징 |
|------|----------|-------------|---------------|------|
| `funny` (기본) | 노란 | 흰 | 청록 이탤릭 | outline+shadow |
| `shocking` | 흰+빨간 아웃라인 | 흰 | 청록 이탤릭 | 강조 아웃라인 |
| `serious` | 노란+반투명 배경 | 흰+반투명 배경 | 청록+배경 | 뉴스 자막 |
| `heartwarming` | 노란 | 흰 | 노란+배경 | 부드러운 강조 |
| `CommentBubble` | — | — | — | 상단 노란 배경 말풍선 (alignment=8) |

### 3막 자막 구조

```
1막 Hook:    {\fad(300,200)} + 강한 페이드인 — Hook 스타일
2막 Body:    댓글 판별 → CommentBubble(상단) or Default(하단)
             따옴표 내용 노란색 인라인 오버라이드 (_highlight_quotes)
3막 Closer:  {\fad(250,400)} + 긴 페이드아웃 — Closer 스타일
```

### 타이밍 알고리즘 (`_proportional_timings`)

```
글자 수 비례 구간 배분
  seg_dur = (chars / total_chars) * total_duration
  gap = 0.12초 (앞 자막 → 다음 자막 전 퇴장, 자연스러운 전환)
```

### 주요 내부 함수 목록

| 함수 | 역할 |
|------|------|
| `_style_line()` | ASS [V4+ Styles] 한 줄 생성 |
| `_build_styles()` | mood별 Hook/Default/Comment/Closer/CommentBubble 5종 스타일 블록 |
| `_time_str()` | 초 → ASS 시간 형식 `H:MM:SS.cs` |
| `_proportional_timings()` | 글자 수 비례 타이밍 계산 |
| `_esc_ass()` | ASS Dialogue 이스케이프 (`{}`→전각, `\n`→`\\N`) |
| `_highlight_quotes()` | 따옴표 내부 노란색 인라인 오버라이드 |
| `_is_comment_sentence()` | 댓글 인용 판별 (따옴표 + 키워드) |
| `_flatten_body()` | v2 dict body → list[str] 변환 |

### 색상 상수 (ASS 형식 `&HAABBGGRR`)

| 상수 | 값 | 용도 |
|------|-----|------|
| `_WHITE` | `&H00FFFFFF` | 기본 텍스트 |
| `_BLACK` | `&H00000000` | 아웃라인 |
| `_RED` | `&H000000FF` | shocking 아웃라인 |
| `_YELLOW` | `&H0000FFFF` | Hook/Closer 강조 |
| `_CYAN` | `&H00FFFF00` | 댓글 텍스트 |
| `_SEMI_BLACK` | `&H80000000` | 50% 투명 배경 |
| `_YELLOW_SEMI` | `&H6000FFFF` | CommentBubble 배경 |

---

## thumbnail.py — 상세 구조

### 의존성

```python
from config.settings import ASSETS_DIR, MEDIA_DIR
from PIL import Image, ImageDraw, ImageFilter, ImageFont
```

### 퍼블릭 함수

```python
def generate_thumbnail(
    hook_text: str,                 # 썸네일 표시 텍스트
    images: list[str],              # 배경 이미지 URL 목록 (첫 번째 사용)
    output_path: Path,              # 저장 경로 (.jpg)
    style: str = "dramatic",        # 'dramatic' | 'question' | 'funny' | 'news'
    font_path: Optional[Path] = None,
) -> Path:
```

```python
def get_thumbnail_path(site_code: str, origin_id: str) -> Path:
```
- `media/thumbnails/{site_code}/post_{origin_id}.jpg` 반환

### 스타일 프리셋 (4종)

| 스타일 | 배경 | 텍스트 색 | 아이콘 | 특징 |
|--------|------|----------|--------|------|
| `dramatic` | 이미지 + 빨간 그라데이션 | 흰 | ⚠ (노란) | 45° 텍스트 회전 |
| `question` | 이미지 + 파란 그라데이션 | 노란 | ? (흰) | 질문형 |
| `funny` | 이미지 + 밝은 톤 | 주황 | :D (노란) | 밝은 톤 |
| `news` | 단색 그라데이션 (진청색) | 흰 | 없음 | 속보 바 + 날짜 |

### 렌더링 파이프라인

```
1. 배경 생성
   ├─ 이미지 있음 → 다운로드 → center-crop 1280×720 → 그라데이션 오버레이
   └─ 이미지 없음/news → 단색 그라데이션 배경

2. 텍스트 렌더링 (스타일별 분기)
   ├─ news     → _draw_news_text() — 빨간 속보 바 + "속보" 레이블 + 날짜
   ├─ angle≠0  → _draw_rotated_text() — RGBA 레이어 회전 합성 (dramatic)
   └─ 그 외    → _draw_normal_text() — 중앙 정렬 + 외곽선

3. 아이콘 렌더링 → 우상단 배치 (글리프 미지원 시 생략)

4. JPEG 저장 (quality=90)
```

### 주요 내부 함수 목록

| 함수 | 역할 |
|------|------|
| `_download_image()` | 배경 이미지 다운로드 (사이트별 Referer) |
| `_fill_crop()` | center-crop + 리사이즈 |
| `_gradient_overlay()` | 위→투명, 아래→색상 RGBA 그라데이션 |
| `_gradient_background()` | 뉴스 스타일 단색 그라데이션 |
| `_make_background()` | 스타일별 배경 생성 |
| `_load_font()` | NanumGothic 폰트 로드 (assets → 시스템 → PIL 기본) |
| `_font_path_str()` | ImageFont → 파일 경로 추출 |
| `_wrap_text()` | 16자 기준 한국어 줄바꿈 |
| `_draw_outlined_text()` | 외곽선(stroke) 텍스트 렌더링 |
| `_draw_text_layer()` | RGBA 레이어에 중앙 배치 텍스트 |
| `_draw_normal_text()` | 캔버스 직접 텍스트 (question, funny) |
| `_draw_rotated_text()` | 회전 텍스트 합성 (dramatic) |
| `_draw_news_text()` | 속보 바 + 레이블 + 날짜 (news) |
| `_draw_icon()` | 아이콘/이모지 우상단 렌더링 |

### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `_THUMB_W` | 1280 | 썸네일 너비 |
| `_THUMB_H` | 720 | 썸네일 높이 |
| `_BASE_FONT_SIZE` | 72 | 기본 폰트 크기 |
| `_DEFAULT_STYLE` | `"dramatic"` | 기본 스타일 |

---

## 호출 흐름도

### 메인 파이프라인 (Phase 8)

```
content_processor.process_content()
  → render_layout_video_from_scenes(post, scenes)
      ├─ _scenes_to_plan_and_sentences(scenes) → sentences, plan, images
      ├─ BGM 추출 (intro 씬의 bgm_path)
      └─ _render_pipeline(...)
           ├─ _create_base_frame()
           ├─ _load_image() × N (이미지 다운로드)
           ├─ _generate_tts_chunks() 또는 TTS 캐시 로드
           ├─ _wrap_korean() × N (줄바꿈 사전계산)
           ├─ _render_{type}_frame() × N (PIL 프레임)
           ├─ 비디오 씬 → _render_video_segment() / _render_static_segment()
           ├─ FFmpeg concat
           └─ FFmpeg encode (video + TTS + BGM → mp4)
```

### 프리뷰 렌더링

```
processor.py → render_preview(post, audio_path, summary_text, cfg)
  ├─ ScriptData.from_json(summary_text)
  ├─ _build_slideshow(images) → Ken Burns + xfade 슬라이드쇼
  │   또는 _build_background_loop() (이미지 없을 때)
  ├─ write_ass_file() → .ass 자막
  │   또는 drawtext 폴백 (_strip_markdown + _wrap_korean)
  ├─ get_comment_timings() → shake/SFX 타이밍
  └─ _compose_final() → video + TTS + 자막 + BGM → mp4
```

### 썸네일 생성

```
processor.py → generate_thumbnail(hook, images, path, style)
  ├─ _make_background(url, style)
  │   ├─ 이미지 → _fill_crop + _gradient_overlay
  │   └─ 없음 → _gradient_background
  ├─ _draw_{news|rotated|normal}_text()
  ├─ _draw_icon()
  └─ JPEG 저장
```

---

## 외부 사용처

### 파이프라인 코어

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `ai_worker/processor.py` | `render_layout_video_from_scenes`, `render_preview`, `generate_thumbnail`, `get_thumbnail_path` | 메인 파이프라인 Phase 8 실행, 프리뷰 생성, 썸네일 생성 |

### 대시보드

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `dashboard/workers/hd_render.py` | `render_layout_video_from_scenes` | 편집실 고화질 재렌더링 |

### 테스트

| 파일 | import 대상 |
|------|-------------|
| `test/test_full_pipeline_e2e.py` | `render_layout_video_from_scenes`, `_scenes_to_plan_and_sentences`, `_get_scene_for_entry` |
| `test/e2e_video_test.py` | `render_layout_video_from_scenes` |
| `test/test_layout.py` | `_load_layout`, `_load_font`, `_wrap_korean`, `_draw_centered_text`, `_fit_cover`, `_paste_rounded`, `_create_base_frame`, `_render_text_only_frame`, `_render_img_text_frame`, `_render_intro_frame` |
| `test/test_video_rendering.py` | `_escape_ffmpeg_text`, `_get_scene_for_entry`, `_render_static_segment`, `_render_video_segment` |
| `test/test_image_rendering.py` | `_download_image_with_retry`, `_is_dc_url`, `_build_slideshow`, `_load_image`, `_scenes_to_plan_and_sentences` |
| `test/test_scene_policy_visual.py` | `_create_base_frame`, `_load_font`, `_load_layout`, `_render_img_only_frame`, `_render_img_text_frame`, `_render_intro_frame`, `_render_outro_frame`, `_render_text_only_frame`, `_wrap_korean` |
| `test/test_render_screenshots.py` | `_create_base_frame`, `_draw_centered_text`, `_load_font`, `_render_img_text_frame`, `_render_text_only_frame`, `_wrap_korean` |
| `test/test_scene_idx_mapping.py` | `_get_scene_for_entry`, `_scenes_to_plan_and_sentences` |

---

## 설정 참조

### config/layout.json (Single Source of Truth)

| 키 | 용도 |
|-----|------|
| `canvas.width/height` | 캔버스 크기 (1080×1920) |
| `global.base_layout` | 배경 템플릿 PNG 경로 |
| `global.header_title` | 헤더 제목 위치/크기/색상 |
| `scenes.intro` | intro 씬 설정 |
| `scenes.img_text.elements.image_area` | 이미지 영역 좌표/크기 |
| `scenes.img_text.elements.text_area` | 텍스트 영역 좌표/크기/폰트 |
| `scenes.text_only.elements.text_area` | 텍스트 영역 + y_coords, max_slots, slot_gap |
| `scenes.img_only.elements.image_area` | 전체 화면 이미지 영역 |
| `scenes.outro.elements` | outro 이미지 + 오버레이 텍스트 |
| `scenes.video_text.elements` | 비디오 영역 + 텍스트 오버레이 |
| `layout_algorithm` | 배분 임계값 (img_heavy/mixed_threshold), SFX 매핑 |
| `constraints` | post_title, hook_text, body_sentence 등 글자수 제한 |

### config/settings.py

| 설정 | 용도 |
|------|------|
| `ASSETS_DIR` | 에셋 루트 디렉터리 (fonts, bgm, backgrounds, sfx) |
| `MEDIA_DIR` | 미디어 출력 디렉터리 (video, tmp, thumbnails) |
| `load_pipeline_config()` | pipeline.json 로드 (tts_voice 등) |
| `VOICE_DEFAULT` | 기본 TTS 음성 키 |

---

## 에러 처리 전략

| 계층 | 전략 |
|------|------|
| 이미지 다운로드 | 2회 재시도 (1s backoff), 플레이스홀더 감지 (<200B), DCInside 쿠키 세션 |
| 이미지 없음 (layout) | img_text → text_only 폴백, 배경 없음 → 검은 화면 |
| TTS 실패 | 2회 재시도 (5s 대기), 실패 시 0초 duration 반환 |
| 비디오 세그먼트 실패 | 정적 PNG 프레임으로 폴백 |
| ASS 자막 실패 | drawtext 폴백 (video.py) |
| geq 흔들림 필터 실패 | 흔들림 제거 후 재시도 |
| NVENC 불가 | libx264 CPU 인코더 폴백 |
| FFmpeg 최종 실패 | `CalledProcessError` raise → 파이프라인 상위에서 처리 |
| 폰트 없음 | assets → fc-list 시스템 → PIL 기본 폰트 3단계 폴백 |

---

## 디렉터리 경로

| 경로 | 용도 |
|------|------|
| `assets/fonts/` | NotoSansKR-Bold/Medium/Regular.ttf |
| `assets/backgrounds/` | base_layout.png, 배경 영상 (*.mp4, *.webm) |
| `assets/bgm/` | BGM 파일 (mood별 하위 폴더) |
| `assets/sfx/` | 효과음 (click, shutter, pop, ding, comment_ding) |
| `media/video/{site_code}/` | 렌더링된 영상 저장 |
| `media/tmp/` | 임시 작업 디렉터리 (자동 정리) |
| `media/tmp/img_cache/` | 이미지 리사이즈 캐시 |
| `media/thumbnails/{site_code}/` | 생성된 썸네일 저장 |
