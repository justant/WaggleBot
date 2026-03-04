# ai_worker/renderer — 렌더러 모듈

9:16 쇼츠 영상의 프레임 생성, 자막 합성, FFmpeg 인코딩, 썸네일 생성을 담당하는 모듈.
Phase 8(FFmpeg 렌더링)의 실행 주체이다.

---

## 파일 구조

```
ai_worker/renderer/
├── __init__.py    # 퍼블릭 API re-export (3줄)
├── layout.py      # 하이브리드 레이아웃 렌더러 — 오케스트레이터 (796줄)
├── _frames.py     # PIL 프레임 렌더링 — 13+ 함수 (543줄)
├── _tts.py        # TTS 청크 생성/병합 — 5 함수 (137줄)
├── _encode.py     # FFmpeg 인코딩 — 6 함수 (175줄)
├── composer.py    # 렌더링 진입점 — compose_video/compose_thumbnail (68줄)
├── subtitle.py    # ASS 동적 자막 생성 (315줄)
├── thumbnail.py   # YouTube 썸네일 생성 1280×720 (447줄)
├── settings.yaml  # 도메인별 설정
└── README.md      # (이 파일)
```

---

## 퍼블릭 API

`__init__.py`에서 re-export:

```python
from ai_worker.renderer.composer import compose_video, compose_thumbnail
from ai_worker.renderer.layout import render_layout_video, render_layout_video_from_scenes
from ai_worker.renderer.thumbnail import generate_thumbnail, get_thumbnail_path
```

| 심볼 | 출처 | 용도 |
|------|------|------|
| `compose_video()` | composer.py | 렌더링 통합 진입점 |
| `compose_thumbnail()` | composer.py | 썸네일 생성 통합 진입점 |
| `render_layout_video()` | layout.py | ScriptData 기반 레이아웃 영상 |
| `render_layout_video_from_scenes()` | layout.py | SceneDecision 목록 기반 렌더링 (메인) |
| `generate_thumbnail()` | thumbnail.py | YouTube 썸네일 생성 |
| `get_thumbnail_path()` | thumbnail.py | 썸네일 저장 경로 반환 |

---

## 모듈 아키텍처

`layout.py`가 오케스트레이터 역할을 하며, 실제 렌더링 로직은 3개 서브모듈에 분산.
layout.py는 서브모듈의 함수를 re-import하여 기존 import 경로 호환성을 유지한다.

```
layout.py (오케스트레이터)
  ├── _frames.py  → PIL 프레임 생성 (intro/img_text/text_only/img_only/outro)
  ├── _tts.py     → TTS 청크 생성/병합 (Fish Speech 연동)
  └── _encode.py  → FFmpeg 세그먼트/최종 인코딩 (h264_nvenc)
```

---

## 모듈 상세

### layout.py (796줄) — 오케스트레이터

11-Step 렌더링 파이프라인을 조율하는 핵심 모듈.

**퍼블릭 함수:**

```python
def render_layout_video(post, script, output_path=None) -> Path: ...
def render_layout_video_from_scenes(post, scenes, output_path=None,
                                     save_tts_cache=None, tts_audio_cache=None) -> Path: ...
```

**11-Step 파이프라인:**

```
Step 1   문장 구조화     → sentences[{"text", "section", "audio", ...}]
Step 2   베이스 프레임    → _create_base_frame() (base_layout.png + 헤더)
Step 3   씬 변환         → _scenes_to_plan_and_sentences()
Step 4   이미지 다운로드  → _load_image() × N
Step 5   TTS 생성/캐시   → _generate_tts_chunks()        [_tts.py]
Step 6   TTS 청크 병합   → _merge_chunks()                [_tts.py]
Step 7   줄바꿈 사전계산  → _wrap_korean()                 [_frames.py]
Step 8   PIL 프레임 생성  → _render_{type}_frame() × N    [_frames.py]
Step 8.5 세그먼트 렌더링  → video/static 세그먼트          [_encode.py]
Step 9   세그먼트 concat  → FFmpeg concat demuxer         [_encode.py]
Step 10  SFX 필터 구성   → (현재 비활성화)
Step 11  FFmpeg 최종     → video + TTS + BGM → mp4       [_encode.py]
```

**주요 내부 함수:**

| 함수 | 역할 |
|------|------|
| `_load_layout()` | config/layout.json 싱글톤 로드 |
| `_scenes_to_plan_and_sentences()` | SceneDecision → 내부 형식 변환 |
| `_plan_sequence()` | 이미지:텍스트 비율 기반 씬 배분 |
| `_render_pipeline()` | Step 2~11 통합 실행 핵심 엔진 |
| `_run_async()` | 코루틴 동기 실행 (이벤트 루프 감지) |

### _frames.py (543줄) — PIL 프레임 렌더링

layout.py에서 분리된 프레임 생성 모듈. 5개 씬 타입별 렌더러와 공통 유틸리티를 포함.

**씬 렌더러:**

| 함수 | 씬 타입 | 설명 |
|------|---------|------|
| `_render_intro_frame()` | intro | 베이스 프레임 (제목 헤더) |
| `_render_image_text_frame()` | img_text | 이미지(900×900) + 하단 텍스트 |
| `_render_text_only_frame()` | text_only | Y좌표 누적 스태킹, comment 색상 분기 |
| `_render_img_only_frame()` | img_only | 이미지 전체 화면 cover |
| `_render_outro_frame()` | outro | 대형 이미지 + 오버레이 텍스트 |

**공통 유틸리티:**

| 함수 | 역할 |
|------|------|
| `_create_base_frame()` | base_layout.png + 헤더 → 공통 베이스 |
| `_load_font()` | assets/fonts → fc-list → PIL 기본 3단계 폴백 |
| `_apply_vf_weight()` | Variable Font 굵기 축 설정 |
| `_wrap_korean()` | 픽셀 기반 한글 줄바꿈 (단어→글자 강제 분리) |
| `_draw_centered_text()` | 중앙 정렬 + stroke 텍스트 |
| `_truncate()` | max_chars 초과 시 `..` 말줄임 |
| `_load_image()` | URL/로컬 이미지 로드 (DCInside 쿠키, 재시도) |
| `_fit_cover()` | Cover 모드 — 비율 유지 + 중앙 크롭 |
| `_paste_rounded()` | 둥근 모서리 마스크 합성 |

**상수:**

| 상수 | 값 | 용도 |
|------|-----|------|
| `CANVAS_W` | 1080 | 캔버스 너비 |
| `CANVAS_H` | 1920 | 캔버스 높이 |
| `HEADER_H` | 160 | 헤더 높이 |
| `HEADER_COLOR` | `#4A44FF` | 헤더 배경색 |

### _tts.py (137줄) — TTS 청크 생성/병합

TTS 오디오 청크의 생성, 캐싱, 병합을 담당.

| 함수 | 역할 |
|------|------|
| `_tts_chunk_async()` | 단일 문장 TTS 생성 (사전 오디오 재사용 or Fish Speech) |
| `_generate_tts_chunks()` | plan 순서 TTS 일괄 생성 + intro 숨고르기 삽입 |
| `_merge_chunks()` | TTS 청크 FFmpeg concat 병합 |
| `_unpack_line()` | text_lines 요소에서 (text, audio_path) 추출 |
| `_get_audio_duration()` | ffprobe 오디오 길이 측정 |

**상수:**

| 상수 | 값 | 용도 |
|------|-----|------|
| `_INTRO_PAUSE_SEC` | 0.5 | 제목 후 본문 시작 전 숨고르기 |

### _encode.py (175줄) — FFmpeg 인코딩

비디오/정적 세그먼트 생성과 최종 mp4 인코딩을 담당.

| 함수 | 역할 |
|------|------|
| `_render_video_segment()` | LTX-Video 클립을 base_frame 위에 합성 → mp4 |
| `_render_video_text_overlay()` | 비디오 위 텍스트를 투명 PNG 오버레이 |
| `_render_static_segment()` | 정적 PNG → duration 길이 mp4 변환 |
| `_resolve_codec()` | h264_nvenc 우선, 불가 시 libx264 폴백 |
| `_get_encoder_args()` | 코덱별 FFmpeg 인코딩 인자 |
| `_escape_ffmpeg_text()` | FFmpeg drawtext용 이스케이프 |

### composer.py (68줄) — 렌더링 진입점

외부에서 호출하는 통합 진입점.

```python
def compose_video(post, scenes, output_path=None, **kwargs) -> Path: ...
def compose_thumbnail(hook_text, images, output_path, style="dramatic") -> Path: ...
```

### subtitle.py (315줄) — ASS 자막 생성

| 함수 | 역할 |
|------|------|
| `build_ass(hook, body, closer, duration, mood, fontname, width, height)` | ASS 자막 문자열 생성 |
| `write_ass_file(...)` | ASS 파일 UTF-8-BOM 저장 |
| `get_comment_timings(hook, body, closer, duration)` | 댓글 구간 (start, end) 타이밍 |

**스타일 프리셋 (4종 mood):**

| mood | 특징 |
|------|------|
| `funny` (기본) | outline + shadow |
| `shocking` | 빨간 아웃라인 강조 |
| `serious` | 반투명 배경 (뉴스 스타일) |
| `heartwarming` | 부드러운 노란 강조 |

**3막 자막 구조:**
```
1막 Hook    → 강한 페이드인 (Hook 스타일)
2막 Body    → 댓글 → CommentBubble(상단), 본문 → Default(하단)
3막 Closer  → 긴 페이드아웃 (Closer 스타일)
```

### thumbnail.py (447줄) — 썸네일 생성

| 함수 | 역할 |
|------|------|
| `generate_thumbnail(hook_text, images, output_path, style)` | 1280×720 JPEG 생성 |
| `get_thumbnail_path(site_code, origin_id)` | 저장 경로 반환 |

**스타일 프리셋 (4종):**

| 스타일 | 배경 | 특징 |
|--------|------|------|
| `dramatic` | 이미지 + 빨간 그라데이션 | 45° 텍스트 회전 |
| `question` | 이미지 + 파란 그라데이션 | 질문형 |
| `funny` | 이미지 + 밝은 톤 | 주황 텍스트 |
| `news` | 단색 그라데이션 | 속보 바 + 날짜 |

---

## 호출 흐름도

### 메인 파이프라인 (Phase 8)

```
content_processor.process_content()
  → render_layout_video_from_scenes(post, scenes)
      ├─ _scenes_to_plan_and_sentences(scenes)
      ├─ BGM 추출 (intro 씬의 bgm_path)
      └─ _render_pipeline(...)
           ├─ _create_base_frame()                [_frames.py]
           ├─ _load_image() × N                   [_frames.py]
           ├─ _generate_tts_chunks()              [_tts.py]
           ├─ _wrap_korean() × N                  [_frames.py]
           ├─ _render_{type}_frame() × N          [_frames.py]
           ├─ _render_video/static_segment() × N  [_encode.py]
           ├─ FFmpeg concat                       [_encode.py]
           └─ FFmpeg encode → 최종 mp4            [_encode.py]
```

---

## 외부 사용처

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `ai_worker/core/processor.py` | `render_layout_video_from_scenes`, `generate_thumbnail`, `get_thumbnail_path` | Phase 8 실행, 썸네일 |
| `ai_worker/pipeline/content_processor.py` | `render_layout_video_from_scenes` | Phase 8 메인 |
| `dashboard/workers/hd_render.py` | `render_layout_video_from_scenes` | 편집실 재렌더링 |

---

## 설정 참조

### config/layout.json (Single Source of Truth)

| 키 | 용도 |
|-----|------|
| `canvas.width/height` | 캔버스 크기 (1080×1920) |
| `global.base_layout` | 배경 템플릿 PNG 경로 |
| `global.header_title` | 헤더 제목 위치/크기/색상 |
| `scenes.intro/img_text/text_only/img_only/outro` | 씬별 레이아웃 설정 |
| `layout_algorithm` | 배분 임계값, SFX 매핑 |
| `constraints` | 글자수 제한 |

---

## 에러 처리 전략

| 계층 | 전략 |
|------|------|
| 이미지 다운로드 | 2회 재시도, DCInside 쿠키 세션, 플레이스홀더 감지 |
| 이미지 없음 | img_text → text_only 폴백 |
| TTS 실패 | 2회 재시도, 실패 시 0초 duration |
| 비디오 세그먼트 실패 | 정적 PNG 프레임 폴백 |
| NVENC 불가 | libx264 CPU 폴백 |
| 폰트 없음 | assets → fc-list → PIL 기본 3단계 폴백 |
| FFmpeg 최종 실패 | `CalledProcessError` raise |
