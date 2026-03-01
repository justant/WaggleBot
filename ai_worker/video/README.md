# ai_worker/video — LTX-2 비디오 생성 모듈

WaggleBot 파이프라인에서 **씬(Scene)별 AI 비디오 클립**을 생성하는 모듈이다.
ComfyUI 서버에 LTX-2 19B FP8 워크플로우를 제출하고, 결과 mp4 클립을 수신하여
최종 렌더링(FFmpeg)에 전달한다.

---

## 목차

1. [모듈 파일 구조](#1-모듈-파일-구조)
2. [전체 파이프라인에서의 위치](#2-전체-파이프라인에서의-위치)
3. [Phase 4.5 — video_mode 할당](#3-phase-45--video_mode-할당)
4. [Phase 6 — 비디오 프롬프트 생성](#4-phase-6--비디오-프롬프트-생성)
5. [Phase 7 — 비디오 클립 생성](#5-phase-7--비디오-클립-생성)
6. [ComfyUI 통신 상세](#6-comfyui-통신-상세)
7. [이미지 적합성 평가 (image_filter)](#7-이미지-적합성-평가-image_filter)
8. [비디오 유틸리티 (video_utils)](#8-비디오-유틸리티-video_utils)
9. [4단계 폴백 재시도 전략](#9-4단계-폴백-재시도-전략)
10. [실패 씬 병합 로직](#10-실패-씬-병합-로직)
11. [ComfyUI 워크플로우 구조](#11-comfyui-워크플로우-구조)
12. [Docker 인프라](#12-docker-인프라)
13. [설정 레퍼런스](#13-설정-레퍼런스)
14. [VRAM 관리와 2막 전략](#14-vram-관리와-2막-전략)
15. [의존성 규칙](#15-의존성-규칙)

---

## 1. 모듈 파일 구조

```
ai_worker/video/
├── __init__.py          # 패키지 공개 API 정의
├── manager.py           # VideoManager — 클립 생성 오케스트레이션
├── comfy_client.py      # ComfyUIClient — REST/WebSocket 통신
├── prompt_engine.py     # VideoPromptEngine — 한국어→영어 프롬프트 변환
├── image_filter.py      # evaluate_image() — I2V 적합성 판별
└── video_utils.py       # FFmpeg 후처리 (리사이즈, 루프, 정규화)
```

### 공개 API (`__init__.py`)

```python
from ai_worker.video.manager import VideoManager
from ai_worker.video.comfy_client import ComfyUIClient
from ai_worker.video.prompt_engine import VideoPromptEngine
from ai_worker.video.image_filter import evaluate_image, ImageSuitability
from ai_worker.video.video_utils import (
    resize_clip_to_layout,
    loop_or_trim_clip,
    normalize_clip_format,
    validate_frame_count,
    validate_resolution,
)
```

---

## 2. 전체 파이프라인에서의 위치

비디오 모듈은 `content_processor.py`의 8-Phase 파이프라인 중 **Phase 4.5, 6, 7**을 담당한다.

```
[Phase 1] analyze_resources   → ResourceProfile
[Phase 2] chunk_with_llm      → raw script dict
[Phase 3] validate_and_fix    → validated script dict
[Phase 4] SceneDirector       → list[SceneDecision]
[Phase 4.5] assign_video_modes → SceneDecision에 video_mode 설정 ★ video 모듈
[Phase 5] TTS 사전 생성        → scene.text_lines = [{"text": ..., "audio": ...}]
[Phase 6] video_prompt 생성    → scene.video_prompt 설정 ★ video 모듈
[Phase 7] video_clip 생성      → scene.video_clip_path 설정 ★ video 모듈
           ↓
    FFmpeg 렌더링 (renderer/video.py, layout.py)
```

모든 Phase는 `VIDEO_GEN_ENABLED=true`일 때만 실행된다.
`config/settings.py`에서 환경변수 `VIDEO_GEN_ENABLED`로 제어한다.

### SceneDecision 비디오 필드

`ai_worker/pipeline/scene_director.py`의 `SceneDecision` 데이터클래스에는
비디오 관련 필드 5개가 존재한다:

| 필드 | 타입 | 설명 |
|------|------|------|
| `video_mode` | `str \| None` | `"t2v"` (Text-to-Video), `"i2v"` (Image-to-Video), `None` |
| `video_prompt` | `str \| None` | LTX-2용 영어 프롬프트 (Phase 6에서 설정) |
| `video_clip_path` | `str \| None` | 생성된 mp4 클립 경로 (Phase 7에서 설정) |
| `video_init_image` | `str \| None` | I2V 모드의 초기 프레임 이미지 경로 |
| `video_generation_failed` | `bool` | 4단계 재시도 모두 실패 시 `True` |

---

## 3. Phase 4.5 — video_mode 할당

**파일**: `ai_worker/pipeline/scene_director.py` → `assign_video_modes()`
**호출**: `content_processor.py` 72~89행

각 씬의 타입에 따라 비디오 생성 모드를 결정한다.

### 할당 규칙

| 씬 타입 | 할당 로직 |
|---------|----------|
| `text_only` | 항상 `"t2v"` (이미지 없으므로) |
| `img_text`, `img_only` | 이미지를 다운로드 → `evaluate_image()` 평가 → score >= 0.6이면 `"i2v"`, 아니면 `"t2v"` |
| `intro`, `outro` | 항상 `"t2v"` |

### 이미지 다운로드 흐름

1. `scene.image_url`에서 이미지 다운로드
2. DCInside URL인 경우 쿠키 워밍업 + Referer 헤더 설정 (403 차단 우회)
3. 캐시 디렉터리에 저장: `media/tmp/vid_img_cache_{post_id}/vid_img_{hash}.jpg`
4. 캐시된 파일이 있으면 재다운로드 생략

---

## 4. Phase 6 — 비디오 프롬프트 생성

**파일**: `ai_worker/video/prompt_engine.py` → `VideoPromptEngine`
**호출**: `content_processor.py` 117~143행

한국어 씬 텍스트를 LTX-2 공식 프롬프팅 가이드에 맞는 영어 프롬프트로 변환한다.
LLM(Ollama)을 사용하므로 GPU가 필요하지 않다 (CPU 호출).

### T2V 프롬프트 생성

`_T2V_PROMPT_SYSTEM_V2` 시스템 프롬프트를 사용한다.

**프롬프트 구조**:
- 단일 흐르는 단락 (single flowing paragraph)
- 현재 시제 (present tense)
- 3~6문장, 200단어 이내
- 6요소: Shot(구도), Scene(배경), Action(동작), Character(인물), Camera(카메라), Audio(사운드)
- 한국 중심 설정: 한국인, 한국 거리/사무실/카페

**인물 묘사 티어**:
1. 한국인 미디엄 샷 (허리 위 또는 전신)
2. 사물/환경 포커스 (빈 방, 노트북, 커피잔)
3. 귀여운 강아지/고양이 (최후 수단)

**LLM 호출**: `call_ollama_raw(prompt=..., max_tokens=180, temperature=0.4)`

### I2V 프롬프트 생성

`_I2V_SYSTEM` 시스템 프롬프트를 사용한다.

이미 장면이 이미지로 존재하므로 **동작(motion)만 기술**한다:
- 미세 움직임: 머리카락 흔들림, 눈 깜빡임, 옷감 움직임
- 카메라: 고정, 미세 돌리 인, 느린 팬
- 사운드: 앰비언트, 음성 톤, 배경 소리
- 2~4문장, 1단락

**LLM 호출**: `call_ollama_raw(prompt=..., max_tokens=120, temperature=0.3)`

### 프롬프트 단순화 (재시도용)

`simplify_prompt()` — 원본 프롬프트를 2~3문장으로 축소한다.
카메라 + 주요 동작만 남기고, 오디오/세부 조명/보조 캐릭터를 제거한다.
4단계 재시도 전략의 2~4차 시도에서 사용된다.

### Mood 기반 스타일링

`config/video_styles.json`에서 mood별 스타일 힌트를 로드한다.

**지원 mood 9종**: `humor`, `touching`, `anger`, `sadness`, `horror`, `info`, `controversy`, `daily`, `shock`

각 mood에 포함된 필드:
- `style_hint`: 상세한 시각적 스타일 지시 (조명, 색감, 카메라 워크)
- `camera_hints`: 권장 카메라 무브먼트 목록
- `color_palette`: 대표 컬러 4색
- `atmosphere`: 전체 분위기 + 페이싱 키워드

프롬프트 생성 시 `atmosphere` 필드가 `{style_hint}` 플레이스홀더에 주입된다.

### 네거티브 프롬프트

`NEGATIVE_PROMPT` 상수가 모든 T2V/I2V 호출에 전달된다:
```
worst quality, inconsistent motion, blurry, jittery, distorted,
watermarks, anime, cartoon, 3d render, CGI,
cyberpunk, sci-fi, futuristic, neon lights, abstract,
deformed hands, extra fingers, bad anatomy,
ugly, duplicate, morbid, mutilated,
western faces, caucasian, european setting
```

### 일괄 생성

`generate_batch(scenes, mood, title, body_summary)`:
- 모든 씬을 순회하며 `video_mode`가 `"t2v"` 또는 `"i2v"`인 씬만 프롬프트를 생성
- 생성된 프롬프트는 `scene.video_prompt`에 저장
- 실패 시 `scene.video_prompt = None`

---

## 5. Phase 7 — 비디오 클립 생성

**파일**: `ai_worker/video/manager.py` → `VideoManager`
**호출**: `content_processor.py` 145~218행

### 전체 흐름

```
content_processor.py (Phase 7)
  │
  ├── torch.cuda.empty_cache() + gc.collect()  (1막 LLM 메모리 해제)
  ├── VRAM 부족 시 emergency_cleanup()
  │
  ├── ComfyUIClient 초기화 (base_url = get_comfyui_url())
  ├── VideoManager 초기화 (comfy_client, prompt_engine, config)
  │
  └── manager.generate_all_clips(scenes, mood, post_id, title, body_summary)
       │
       ├── comfy.health_check()   — 실패 시 전체 스킵
       │
       ├── for each scene (순차):
       │    └── _generate_single_clip(scene, i, post_id)
       │         ├── attempt 1: Full (1280x720, 97프레임, 20스텝, CFG=3.5)
       │         ├── attempt 2: 프롬프트 단순화 (동일 설정)
       │         ├── attempt 3: 해상도 다운그레이드 (768x512, 65프레임, 15스텝)
       │         └── attempt 4: Distilled 폴백 (768x512, 65프레임, 8스텝, CFG=1.0)
       │
       └── _merge_failed_scenes(scenes, results)
            └── 실패 씬 삭제 + text_lines를 인접 성공 씬에 병합
```

### VideoManager 초기화

```python
# content_processor.py Phase 7에서 생성
comfy = ComfyUIClient(base_url=get_comfyui_url())
video_config = {
    "VIDEO_RESOLUTION": (1280, 720),
    "VIDEO_RESOLUTION_FALLBACK": (768, 512),
    "VIDEO_NUM_FRAMES": 97,          # ~4초 @24fps
    "VIDEO_NUM_FRAMES_FALLBACK": 65, # ~2.7초 @24fps
    "VIDEO_GEN_TIMEOUT": 300,
    "VIDEO_MAX_CLIPS_PER_POST": 8,
    "VIDEO_MAX_RETRY": 4,
}
manager = VideoManager(comfy_client=comfy, prompt_engine=prompt_engine, config=video_config)
```

### generate_all_clips()

1. **Health Check**: `comfy.health_check()` — ComfyUI `/system_stats` 엔드포인트 확인
2. **순차 생성**: VRAM 안전을 위해 씬을 순차적으로 처리 (병렬 생성 없음)
3. **최대 클립 수 제한**: `VIDEO_MAX_CLIPS_PER_POST` (기본 8) 초과 시 나머지 스킵
4. **실패 씬 병합**: 모든 씬 처리 후 실패 씬의 대본을 인접 씬에 병합

### VideoGenerationResult

```python
@dataclass
class VideoGenerationResult:
    scene_index: int
    success: bool
    clip_path: Path | None = None
    attempts: int = 0
    failure_reason: str | None = None
    merged_into: int | None = None      # 병합된 대상 씬 인덱스
```

---

## 6. ComfyUI 통신 상세

**파일**: `ai_worker/video/comfy_client.py` → `ComfyUIClient`

### 클라이언트 초기화

```python
client = ComfyUIClient(
    base_url="http://comfyui:8188",     # docker-compose.yml의 서비스명
    output_dir=Path("media/tmp/videos") # 공유 볼륨 경로
)
```

- `client_id`: UUID v4 (WebSocket 구독용)
- `_workflow_dir`: 프로젝트 루트의 `ai_worker/video/workflows/` 디렉터리

### 공개 메서드

| 메서드 | 설명 | 워크플로우 파일 |
|--------|------|----------------|
| `health_check()` | GET `/system_stats` (5초 타임아웃) | - |
| `generate_t2v()` | Text-to-Video | `t2v_ltx2.json` 또는 `t2v_ltx2_distilled.json` |
| `generate_t2v_with_upscale()` | 2-Stage 업스케일 T2V (640x360→1280x720) | `t2v_ltx2_upscale.json` |
| `generate_i2v()` | Image-to-Video | `i2v_ltx2.json` |

### 워크플로우 실행 절차 (`_queue_and_wait`)

```
1. _load_workflow("t2v_ltx2.json")
   └── ai_worker/video/workflows/ 디렉터리에서 JSON 로드

2. _patch_workflow(workflow, params)
   └── 노드 inputs의 키/값을 params로 교체
       - 키 매칭: inputs의 키가 params 키와 일치하면 값 교체
       - 값 매칭: inputs의 값(문자열)이 params 키와 일치하면 교체
         예: "text": "positive_prompt" → "text": "실제 프롬프트 내용"

3. POST /prompt
   └── {"prompt": workflow, "client_id": uuid}
   └── 응답: {"prompt_id": "..."}

4. WebSocket 대기 (ws://{host}/ws?clientId={uuid})
   ├── "executing" 이벤트: 실행 중 노드 로깅
   ├── "progress" 이벤트: 진행률 로깅 (value/max)
   ├── "executed" 이벤트: node=None → 실행 완료
   └── "execution_error": RuntimeError 발생

   WebSocket 끊김 시 → _poll_until_done() 폴백
     └── GET /history/{prompt_id} 반복 폴링 (3초 간격)
     └── 10회 연속 에러 시 RuntimeError

5. GET /history/{prompt_id}
   └── outputs에서 결과 파일 경로 추출
       - "gifs" 키: VHS_VideoCombine 노드 출력 (mp4/gif)
       - "images" 키: 이미지 노드 출력 (.mp4 확장자 확인)

6. 파일 경로 반환
   └── output_dir / subfolder / filename
```

### I2V 이미지 처리

1. `_resize_image()`: 원본 > target이면 LANCZOS 리사이즈 (비율 유지)
2. `_upload_image()`: POST `/upload/image` (multipart/form-data)
3. 워크플로우에 `init_image` 파라미터로 서버 내 파일명 전달

---

## 7. 이미지 적합성 평가 (image_filter)

**파일**: `ai_worker/video/image_filter.py`

크롤링된 이미지가 I2V 입력으로 적합한지 0.0~1.0 점수로 평가한다.

### 평가 기준 (가중치 합계 = 1.0)

| 기준 | 가중치 | 만점 조건 | 감점 조건 |
|------|--------|----------|----------|
| 해상도 | 0.20 | min(w,h) >= 512 | 256~511 → 0.10, <256 → 0.00 |
| 종횡비 | 0.20 | max/min <= 2.0 | 2.0~3.0 → 0.10, >3.0 → 0.00 |
| 텍스트 밀도 | 0.30 | 텍스트 캡처본 아님 | bimodal 히스토그램 > 70% → 0.00 |
| 색상 다양성 | 0.15 | RGB std > 30 | 15~30 → 0.07, <15 → 0.00 |
| 엣지 밀도 | 0.15 | 엣지 픽셀 > 5% | 2~5% → 0.07, <2% → 0.00 |

### 결과 데이터클래스

```python
@dataclass
class ImageSuitability:
    score: float      # 0.0 ~ 1.0
    reason: str       # "suitable" 또는 콤마 구분 부적합 사유
    category: str     # "photo", "screenshot", "meme", "text_capture", "diagram", "unknown"
    width: int
    height: int
```

### 분류 규칙

- `text_heavy_image` → `"text_capture"` (텍스트 캡처본)
- `very_low_color_diversity` + `flat_image` → `"meme"` (단색 밈)
- `very_low_resolution` → `"screenshot"` (저해상도 스크린샷)
- 정사각형 + 200px 미만 → `"diagram"` (다이어그램)
- 그 외 → `"photo"` (I2V 후보)

### 임계값

`VIDEO_I2V_THRESHOLD = 0.6` — 이 점수 이상이면 I2V, 미만이면 T2V로 전환.

---

## 8. 비디오 유틸리티 (video_utils)

**파일**: `ai_worker/video/video_utils.py`

생성된 비디오 클립을 렌더링 레이아웃에 맞게 후처리한다.
모든 함수는 FFmpeg subprocess를 사용한다.

### validate_frame_count(n) → int

LTX-2의 **1+8k 규칙**에 맞는 가장 가까운 유효값으로 보정한다.
유효한 프레임 수: 9, 17, 25, 33, 41, 49, 57, 65, 73, 81, 89, 97, ...

### validate_resolution(width, height) → tuple[int, int]

해상도를 **8의 배수**로 보정한다. 최소 64px.

### resize_clip_to_layout(input_path, output_path, width=900, height=900, fps=24)

- 비디오를 `layout.json`의 `video_area` 크기에 맞게 center-crop + resize
- GPU 환경이면 `h264_nvenc`, 아니면 `libx264` 자동 감지
- 오디오 제거 (`-an`)

### loop_or_trim_clip(input_path, output_path, target_duration)

클립 길이를 TTS 오디오 길이에 맞춘다:
- 클립 < target: `stream_loop`으로 반복 재생
- 클립 > target: `-t`로 트림
- 차이 ≤ 0.2초: 그대로 복사

### normalize_clip_format(input_path, output_path, width=1080, height=1920, fps=24)

최종 출력 포맷으로 정규화:
- 코덱: h264 (yuv420p)
- 해상도: 1080x1920 (9:16 세로)
- FPS: 24
- 오디오: 없음 (TTS와 별도 합성)

### _resolve_intermediate_codec()

GPU 코덱 자동 감지:
1. `h264_nvenc`로 테스트 인코딩 시도 (nullsrc, 0.1초)
2. 성공 → `h264_nvenc` + `-preset p1`
3. 실패 → `libx264` + `-preset ultrafast -crf 23`

---

## 9. 4단계 폴백 재시도 전략

**파일**: `ai_worker/video/manager.py` → `_generate_single_clip()`

하나의 씬에 대해 최대 4회 시도하며, 각 단계별로 설정을 완화한다.

| 단계 | 해상도 | 프레임 | 스텝 | CFG | 모델 | 프롬프트 |
|------|--------|--------|------|-----|------|---------|
| 1차 | 1280x720 | 97 (~4초) | 20 | 3.5 | Full (19B FP8) | 원본 |
| 2차 | 1280x720 | 97 (~4초) | 20 | 3.5 | Full (19B FP8) | 단순화 |
| 3차 | 768x512 | 65 (~2.7초) | 15 | 3.5 | Full (19B FP8) | 단순화 |
| 4차 | 768x512 | 65 (~2.7초) | 8 | 1.0 | Distilled (19B FP8) | 단순화 |

**OOM 복구**: `"out of memory"` 에러 감지 시 `torch.cuda.empty_cache()` + `gc.collect()` 즉시 실행.

4회 모두 실패하면 `VideoGenerationResult(success=False)`를 반환하고,
해당 씬은 삭제 후 대본이 인접 씬에 병합된다.

---

## 10. 실패 씬 병합 로직

**파일**: `ai_worker/video/manager.py` → `_merge_failed_scenes()`

비디오 생성에 실패한 씬을 삭제하되, 대본(text_lines)은 보존한다.

### 병합 규칙

1. 실패 씬의 `text_lines`를 **직전 성공 씬**에 append
2. 직전 씬이 없으면 (첫 씬이 실패) **직후 성공 씬**에 prepend
3. 연속 실패 시 가장 가까운 성공 씬에 모두 병합
4. 병합된 text_lines는 원본 순서를 유지

### 병합 예시

```
씬 0 (성공) | 씬 1 (실패) | 씬 2 (실패) | 씬 3 (성공)
                  │              │
                  └──── 씬 0에 append ────┘
결과: 씬 0 (text_lines 확장) | 씬 3 (그대로)
```

삭제는 뒤에서부터 수행하여 인덱스 밀림을 방지한다.

---

## 11. ComfyUI 워크플로우 구조

### 워크플로우 파일 목록

```
ai_worker/video/workflows/
├── t2v_ltx2.json              # T2V 풀 모델 (기본)
├── t2v_ltx2_distilled.json    # T2V Distilled 모델 (4차 폴백)
├── i2v_ltx2.json              # I2V (이미지→비디오)
└── t2v_ltx2_upscale.json      # 2-Stage 업스케일 (테스트용)
```

### 노드 체인 (t2v_ltx2_distilled.json 기준)

```
CheckpointLoaderSimple
  └── ltx-2-19b-distilled-fp8.safetensors
       │
       ├── MODEL ──────────────────────────┐
       └── VAE ────────────────────────────┤
                                           │
LTXVGemmaCLIPModelLoader                   │
  └── gemma-3-12b-it-qat-q4_0-unquantized │
       │                                   │
       └── CLIP                            │
            │                              │
            ├── CLIPTextEncode (positive)   │
            │    └── text = positive_prompt │
            │                              │
            └── CLIPTextEncode (negative)   │
                 └── text = negative_prompt │
                                           │
LTXVConditioning ──────────────────────────┤
  └── positive/negative conditioning       │
       + frame_rate                        │
                                           │
EmptyLTXVLatentVideo                       │
  └── width, height, length, batch_size=1  │
                                           │
ManualSigmas ──────────────────────────────┤
  └── total_sigmas = steps                 │
                                           │
SamplerCustomAdvanced ─────────────────────┤
  └── noise_seed, cfg, model, sigmas       │
       │                                   │
       └── LATENT                          │
            │                              │
            └── VAEDecode ─────────────────┘
                 │
                 └── IMAGE
                      │
                      └── VHS_VideoCombine
                           └── frame_rate, format=video/h264-mp4
                                │
                                └── output.mp4
```

### 패치 가능한 파라미터

`_patch_workflow()`가 워크플로우 JSON의 노드 inputs에서 다음 키/값을 교체한다:

| 파라미터 키 | 대상 노드 | 설명 |
|------------|----------|------|
| `positive_prompt` | CLIPTextEncode | 비디오 프롬프트 |
| `negative_prompt` | CLIPTextEncode | 네거티브 프롬프트 |
| `width`, `height` | EmptyLTXVLatentVideo | 출력 해상도 |
| `length`, `frames_number` | EmptyLTXVLatentVideo | 프레임 수 |
| `frame_rate` | LTXVConditioning, VHS_VideoCombine | FPS |
| `steps` | ManualSigmas | 샘플링 스텝 수 |
| `cfg` | SamplerCustomAdvanced | CFG 스케일 |
| `noise_seed` | SamplerCustomAdvanced | 랜덤 시드 |
| `init_image` | LoadImage (I2V만) | 업로드된 이미지 파일명 |
| `strength` | LTXVImgToVideoInplace (I2V만) | 변형 강도 |

---

## 12. Docker 인프라

### Dockerfile.comfyui

```dockerfile
FROM nvidia/cuda:12.6.3-runtime-ubuntu22.04

# Python 3.11 + git, wget, ffmpeg
# ComfyUI 클론 + requirements 설치
# 커스텀 노드:
#   - ComfyUI-VideoHelperSuite (VHS_VideoCombine 노드)
#   - ComfyUI-LTXVideo (LTXVConditioning, LTXVScheduler 등)

CMD ["python3", "main.py", "--listen", "0.0.0.0", "--lowvram", "--reserve-vram", "2"]
```

- `--lowvram`: 19B FP8 모델을 시스템 RAM으로 오프로드 (weight streaming)
- `--reserve-vram 2`: ComfyUI 자체용 2GB VRAM 예약 (OOM 방지)
- VRAM에는 ~450MB 버퍼만 상주, 나머지는 RAM에서 PCIe 스트리밍

### docker-compose.yml 볼륨 매핑

```yaml
comfyui:
  build:
    dockerfile: Dockerfile.comfyui
  ports:
    - "8188:8188"
  volumes:
    - ./checkpoints/ltx-2:/comfyui/models/checkpoints
    - ./checkpoints/text_encoders:/comfyui/models/text_encoders
    - ./checkpoints/latent_upscale_models:/comfyui/models/latent_upscale_models
    - ./checkpoints/loras:/comfyui/models/loras
    - ./ai_worker/video/workflows:/comfyui/custom_workflows
    - ./media/tmp/videos:/comfyui/output        # ← ai_worker와 공유
  healthcheck:
    test: python -c "urllib.request.urlopen('http://localhost:8188/system_stats')"
    interval: 30s
    start_period: 120s
```

### 모델 파일 구조

```
checkpoints/
├── ltx-2/
│   ├── ltx-2-19b-dev-fp8.safetensors           # 풀 모델 (FP8)
│   └── ltx-2-19b-distilled-fp8.safetensors      # Distilled 모델 (FP8)
├── text_encoders/
│   └── gemma-3-12b-it-qat-q4_0-unquantized/    # Gemma 3 텍스트 인코더
├── latent_upscale_models/                        # 업스케일 모델 (선택)
└── loras/                                        # LoRA 모델 (선택)
```

### 출력 파일 공유

ComfyUI 출력 디렉터리 `/comfyui/output`와 ai_worker의 `media/tmp/videos`가
동일 Docker 볼륨으로 마운트되어 있다. ComfyUI가 생성한 mp4 파일을
ai_worker가 직접 파일시스템 경로로 접근할 수 있다.

---

## 13. 설정 레퍼런스

**파일**: `config/settings.py` (327~366행)

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `COMFYUI_URL` | `http://comfyui:8188` | ComfyUI 서버 URL |
| `COMFYUI_VRAM_MODE` | `lowvram` | Weight streaming 모드 |
| `COMFYUI_RESERVE_VRAM` | `2` | VRAM 예약량 (GB) |
| `VIDEO_GEN_ENABLED` | `false` | 비디오 생성 활성화 여부 |
| `VIDEO_GEN_TIMEOUT` | `300` | 단일 클립 생성 타임아웃 (초) |
| `VIDEO_MODEL_FULL` | `ltx-2-19b-dev-fp8.safetensors` | 풀 모델 파일명 |
| `VIDEO_MODEL_DISTILLED` | `ltx-2-19b-distilled-fp8.safetensors` | Distilled 모델 파일명 |
| `VIDEO_RESOLUTION` | `(1280, 720)` | 기본 출력 해상도 |
| `VIDEO_RESOLUTION_FALLBACK` | `(768, 512)` | 다운그레이드 해상도 |
| `VIDEO_NUM_FRAMES` | `97` | 기본 프레임 수 (~4초 @24fps) |
| `VIDEO_NUM_FRAMES_FALLBACK` | `65` | 다운그레이드 프레임 수 (~2.7초) |
| `VIDEO_FPS` | `24` | 프레임 레이트 |
| `VIDEO_STEPS` | `20` | 풀 모델 샘플링 스텝 |
| `VIDEO_STEPS_DISTILLED` | `8` | Distilled 모델 샘플링 스텝 |
| `VIDEO_CFG` | `3.5` | 풀 모델 CFG 스케일 |
| `VIDEO_CFG_DISTILLED` | `1.0` | Distilled 모델 CFG 스케일 |
| `VIDEO_I2V_THRESHOLD` | `0.6` | I2V 적합성 임계값 |
| `VIDEO_I2V_DENOISE` | `0.75` | I2V strength (변형 강도) |
| `VIDEO_MAX_CLIPS_PER_POST` | `8` | 글당 최대 클립 수 |
| `VIDEO_MAX_RETRY` | `4` | 씬당 최대 재시도 횟수 |
| `VIDEO_OUTPUT_DIR` | `media/tmp/videos` | 출력 디렉터리 |

---

## 14. VRAM 관리와 2막 전략

RTX 3090 24GB에서 모든 AI 모델을 운영하기 위해 **2막 구조**를 사용한다.

### 1막: LLM (대본/프롬프트 생성)

- qwen2.5:14b 8-bit (~14GB)
- Phase 1~6 실행 (Phase 6에서 비디오 프롬프트 생성 포함)
- LLM은 Ollama 서버에서 실행 → CPU 호출이므로 VRAM 직접 사용 없음

### 2막: 미디어 (TTS + 비디오)

- Fish Speech (~5GB) + LTX-2 (~12GB)
- Phase 5 (TTS) + Phase 7 (비디오 클립)
- Phase 7 진입 전 `torch.cuda.empty_cache()` + `gc.collect()` 실행

### VRAM 안전장치

`content_processor.py` Phase 7 진입부 (158~168행):

```python
_gm = get_gpu_manager()
_video_vram = _gm.MODEL_VRAM_REQUIREMENTS.get(ModelType.VIDEO, 12.0)
_available = _gm.get_available_vram()
if _available < _video_vram * 0.5:
    _gm.emergency_cleanup()   # 모든 모델 강제 해제
```

`gpu_manager.py` 설정:
- `ModelType.VIDEO = 12.0 GB`
- `MAX_COEXIST_VRAM_GB = 20.0 GB` (동시 모델 합계 상한)

---

## 15. 의존성 규칙

이 모듈은 아래 규칙을 엄격하게 따른다:

1. **`ai_worker.tts` 모듈을 절대 import하지 않는다** — TTS와 비디오는 독립 파이프라인
2. LLM 호출은 `ai_worker.llm.client.call_ollama_raw()`만 사용 (prompt_engine 경유)
3. 설정은 `config/settings.py` 경유 — 로직 내 `os.getenv()` 금지
4. 파일 경로는 `pathlib.Path` 사용 — `os.path` 금지
5. 로깅은 `logging.getLogger(__name__)` — `print()` 금지
6. GPU 사용 시 `gpu_manager.managed_inference()` 컨텍스트 매니저 사용 (content_processor에서 관리)
7. FFmpeg 코덱: `h264_nvenc` 전용 (RTX 3090 필수)
