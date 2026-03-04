# ai_worker — AI 파이프라인 워커

커뮤니티 게시글을 **LLM 대본 → TTS 음성 → LTX-2 비디오 → FFmpeg 렌더링**으로 변환하는
WaggleBot 핵심 처리 모듈. APPROVED 상태 게시글을 폴링하여 8-Phase 파이프라인을 실행하고,
PREVIEW_RENDERED → RENDERED → UPLOADED까지 자동 처리한다.

---

## 목차

1. [디렉터리 구조](#1-디렉터리-구조)
2. [core/ 상세](#2-core-상세)
3. [8-Phase 파이프라인](#3-8-phase-파이프라인)
4. [하위 모듈 요약](#4-하위-모듈-요약)
5. [모듈 간 의존성 맵](#5-모듈-간-의존성-맵)
6. [VRAM 2막 전략](#6-vram-2막-전략)
7. [비동기 워커 아키텍처](#7-비동기-워커-아키텍처)
8. [에러 처리 전략](#8-에러-처리-전략)
9. [설정 참조](#9-설정-참조)
10. [Docker 서비스 구성](#10-docker-서비스-구성)

---

## 1. 디렉터리 구조

```
ai_worker/
├── __init__.py
│
├── core/                    # 워커 진입점 + 프로세서 + GPU 관리
│   ├── __init__.py
│   ├── main.py              #   3-워커 asyncio 루프 (LLM+TTS / Render / Upload)
│   ├── processor.py         #   RobustProcessor — 재시도 + Phase 분기
│   ├── gpu_manager.py       #   GPUMemoryManager — VRAM 2막 전환
│   ├── shutdown.py          #   Graceful Shutdown 이벤트 싱글톤
│   └── settings.yaml        #   폴링 간격, CUDA 동시성, 재시도, GPU 메모리
│
├── script/                  # LLM 클라이언트 + 대본 파싱/정규화
│   ├── __init__.py
│   ├── client.py            #   generate_script(), call_ollama_raw()
│   ├── parser.py            #   parse_script_json() — JSON 3단계 파싱 파이프라인
│   ├── normalizer.py        #   ensure_comments(), split_comment_lines()
│   ├── chunker.py           #   chunk_with_llm() — Phase 2 LLM 의미 단위 청킹
│   ├── logger.py            #   LLMCallTimer, log_llm_call() → llm_logs 테이블
│   └── settings.yaml        #   Ollama 파라미터, 품질 기준, 프롬프트 버전
│
├── scene/                   # 씬 분석·배분·검증
│   ├── __init__.py
│   ├── analyzer.py          #   Phase 1: analyze_resources() → ResourceProfile
│   ├── director.py          #   Phase 4+4.5: SceneDirector + assign_video_modes()
│   ├── validator.py         #   Phase 3: validate_and_fix() + smart_split_korean()
│   ├── strategy.py          #   SceneMix 데이터클래스 (씬 구성 전략)
│   └── settings.yaml        #   전략 임계값, 스택 크기, 강조 키워드
│
├── tts/                     # TTS 음성 합성 (Fish Speech)
│   ├── __init__.py
│   ├── fish_client.py       #   synthesize(), wait_for_fish_speech(), _post_process_audio()
│   ├── normalizer.py        #   normalize_for_tts() — 5단계 한국어 전처리 파이프라인
│   ├── number_reader.py     #   sino_number(), native_number() — 숫자→한국어 변환
│   └── settings.yaml        #   음성, 타임아웃, 후처리, 품질 검증
│
├── video/                   # LTX-2 비디오 생성 (ComfyUI 통신)
│   ├── __init__.py
│   ├── manager.py           #   VideoManager — 4단계 폴백 + 실패 씬 병합
│   ├── comfy_client.py      #   ComfyUIClient — REST API + polling
│   ├── prompt_engine.py     #   VideoPromptEngine — 한국어→영어 프롬프트
│   ├── image_filter.py      #   evaluate_image() — I2V 적합성 5기준 평가
│   ├── video_utils.py       #   FFmpeg 후처리 (리사이즈, 루프, 정규화)
│   ├── workflows/           #   ComfyUI JSON 워크플로우 (5개)
│   └── settings.yaml        #   해상도, 프레임, 샘플링, I2V, ComfyUI, 타임아웃
│
├── renderer/                # FFmpeg 렌더링 (프레임 + 자막 + 인코딩 + 썸네일)
│   ├── __init__.py
│   ├── composer.py          #   compose_video(), compose_thumbnail() — 렌더링 진입점
│   ├── layout.py            #   오케스트레이터 — _render_pipeline() + public API
│   ├── _frames.py           #   PIL 프레임 렌더러 (intro/image_text/text_only/outro)
│   ├── _tts.py              #   TTS 청크 생성·병합 (FFmpeg concat)
│   ├── _encode.py           #   FFmpeg 인코딩·세그먼트 (h264_nvenc)
│   ├── subtitle.py          #   ASS 동적 자막 (4 mood 프리셋)
│   ├── thumbnail.py         #   YouTube 썸네일 1280×720 (4 스타일)
│   └── settings.yaml        #   코덱, 품질, FPS, 오디오, 썸네일
│
├── pipeline/                # Phase 1~7 오케스트레이션
│   ├── __init__.py
│   └── content_processor.py #   process_content() — 통합 진입점
│
└── llm/                     # (deprecated — ai_worker.script으로 이전)
    └── __init__.py          #   하위호환 re-export (DeprecationWarning)
```

---

## 2. core/ 상세

### main.py (~288행) — 진입점

3개의 asyncio 워커가 `asyncio.gather`로 동시 실행된다.

```
┌──────────────────────────────────────────────────────────┐
│  _main_loop()                                            │
│  ├── _llm_tts_worker(render_queue)   ← APPROVED 폴링    │
│  │     └── processor.llm_tts_stage() → (script, audio)   │
│  │     └── render_queue.put()                            │
│  ├── _render_worker(render_queue)    ← 큐 소비           │
│  │     └── processor.render_stage()  → 프리뷰 영상       │
│  └── _upload_loop()                  ← RENDERED 폴링     │
│        └── upload_once()             → YouTube 업로드     │
└──────────────────────────────────────────────────────────┘
```

| 함수 | 역할 |
|------|------|
| `main()` | 로깅 초기화 → DB 초기화 → 고착 포스트 복구 → 워커 시작 |
| `_startup()` | Fish Speech 기동 대기 + 웜업 |
| `_llm_tts_worker()` | APPROVED 폴링 → CUDA 세마포어 획득 → LLM+TTS → 큐 적재 |
| `_render_worker()` | 큐 소비 → `run_in_executor` CPU 렌더링 |
| `_upload_loop()` | RENDERED 폴링 → `upload_post()` 호출 |
| `upload_once()` | 단일 포스트 업로드 (`auto_upload=true` 시) |
| `_recover_stuck_posts()` | PROCESSING 고착 포스트 → APPROVED 복구 |
| `_drain_render_queue()` | 셧다운 시 미처리 큐 → APPROVED 복원 |
| `_mark_post_failed()` | 예외 시 FAILED 안전 마킹 |

**CUDA 세마포어**: `asyncio.Semaphore(CUDA_CONCURRENCY)` — LLM+TTS GPU 작업을 직렬화.

### processor.py (~871행) — RobustProcessor

재시도 메커니즘과 Phase별 분기를 통합하는 핵심 프로세서.

#### 데이터 구조

```python
class FailureType(Enum):
    LLM_ERROR = "llm_error"          # 재시도 불가
    TTS_ERROR = "tts_error"          # 재시도 가능
    RENDER_ERROR = "render_error"    # 재시도 가능
    NETWORK_ERROR = "network_error"  # 재시도 가능
    RESOURCE_ERROR = "resource_error" # 재시도 가능
    UNKNOWN_ERROR = "unknown_error"  # 재시도 가능

@dataclass
class RetryPolicy:
    max_attempts: int = MAX_RETRY_COUNT
    backoff_factor: float = 2.0
    initial_delay: float = 5.0
```

#### RobustProcessor 메서드

| 메서드 | 접근 | 역할 |
|--------|------|------|
| `process_with_retry(post, session)` | public | 3-Step 통합 처리 (LLM→TTS→렌더) + 재시도 |
| `llm_tts_stage(post_id)` | public | GPU 단계: LLM 대본 + TTS 합성 → (ScriptData, audio_path) |
| `render_stage(post_id, script, audio_path)` | public | CPU 단계: 렌더링 + 썸네일 → PREVIEW_RENDERED |
| `_generate_video_clips(scenes, script, ...)` | async private | Phase 4.5~7 비디오 생성 오케스트레이션 |
| `_generate_video_clips_sync(...)` | private | 위 함수의 동기 래퍼 (render_stage 스레드용) |
| `_safe_generate_summary(post, session)` | private | LLM 대본 생성 (기존 JSON 재사용 / 피드백 주입) |
| `_safe_generate_tts(text, post_id, ...)` | private | TTS 합성 (MD5 캐시 확인 → 재합성 스킵) |
| `_safe_render_video(post, audio, summary)` | private | 프리뷰 렌더링 (480×854 CPU) |
| `_classify_error(e)` | private | 에러 → FailureType 분류 |
| `_calculate_backoff_delay(attempt)` | private | Exponential Backoff 계산 |
| `_log_failure(post_id, type, msg, attempt)` | private | `media/logs/failures.log` 기록 |
| `_save_content(post, session, script, ...)` | private | Content DB 레코드 저장 |
| `_mark_as_failed(post, session, ...)` | private | FAILED 상태 마킹 |
| `process(post, session)` | 모듈 함수 | 하위 호환 래퍼 → `process_with_retry()` |

#### render_style 분기

| render_style | 렌더러 | 해상도 | 코덱 |
|-------------|--------|--------|------|
| `"layout"` (기본) | `render_layout_video_from_scenes()` | 1080×1920 | h264_nvenc / libx264 |
| 기타 | `render_preview()` | 480×854 | libx264 (CPU) |

### gpu_manager.py (~480행) — GPUMemoryManager

RTX 3090 24GB VRAM을 2막 구조로 운영하기 위한 메모리 관리자.

#### 데이터 구조

```python
class ModelType(Enum):
    LLM = "llm"      # ~14GB (qwen2.5:14b 8-bit)
    TTS = "tts"       # ~5GB  (Fish Speech 1.5)
    VIDEO = "video"   # ~12GB (LTX-2 GGUF Q4)
    OTHER = "other"   # ~2GB

@dataclass
class MemoryStats:
    total_gb, allocated_gb, reserved_gb, free_gb, usage_percent

@dataclass
class ModelInfo:
    model_type, name, estimated_vram_gb, actual_vram_gb, loaded
```

#### GPUMemoryManager 메서드

| 메서드 | 역할 |
|--------|------|
| `managed_inference(model_type, model_name)` | **컨텍스트 매니저** — VRAM 확보 → 추론 → 정리 |
| `get_memory_stats()` | PyTorch CUDA 메모리 통계 |
| `get_available_vram()` | `torch.cuda.mem_get_info` — 프로세스 내 여유 VRAM |
| `get_system_available_vram()` | `nvidia-smi` — 시스템 전체 여유 VRAM |
| `can_load_model(required, margin=2.0)` | 모델 로드 가능 여부 (안전 마진 포함) |
| `can_coexist(new_model_type)` | 동시 상주 가능 여부 (MAX_COEXIST_VRAM_GB 기준) |
| `cleanup_memory()` | `empty_cache()` + `gc.collect()` |
| `emergency_cleanup()` | 모든 모델 언로드 + IPC 수집 + 강제 정리 |
| `_free_memory_for_model(target, required)` | 동시 상주 불가 시 다른 모델 언로드 |
| `monitor_memory()` | 메모리 + 로드된 모델 상태 dict |
| `log_memory_status()` | 메모리 상태 로그 출력 |

**싱글톤**: `get_gpu_manager()` — 전역 인스턴스 반환.

### shutdown.py (~34행) — Graceful Shutdown

```python
get_shutdown_event() → asyncio.Event      # 셧다운 이벤트 (싱글톤)
is_shutting_down() → bool                 # 셧다운 요청 여부
request_shutdown() → None                 # 이벤트 설정 (SIGTERM/SIGINT 핸들러)
```

모든 워커 루프가 `is_shutting_down()`을 확인하여 현재 작업 완료 후 종료한다.

---

## 3. 8-Phase 파이프라인

```
Phase 1   analyze_resources    → ResourceProfile (이미지:텍스트 비율)
  │         ↳ scene/analyzer.py
Phase 2   chunk_with_llm       → raw script dict (LLM 의미 단위 청킹)
  │         ↳ script/chunker.py + script/client.py
Phase 3   validate_and_fix     → validated script dict (max_chars 검증)
  │         ↳ scene/validator.py
Phase 4   SceneDirector        → list[SceneDecision] (씬 배분 + 감정 태그 + BGM)
  │         ↳ scene/director.py
Phase 4.5 assign_video_modes   → video_mode = t2v | i2v | static
  │         ↳ scene/director.py + video/image_filter.py
Phase 5   TTS 생성              → scene.text_lines = [{"text", "audio"}]
  │         ↳ tts/fish_client.py
Phase 6   video_prompt 생성     → scene.video_prompt (한국어→영어)
  │         ↳ video/prompt_engine.py + script/client.py
Phase 7   video_clip 생성       → scene.video_clip_path (ComfyUI LTX-2)
  │         ↳ video/manager.py + video/comfy_client.py
Phase 8   FFmpeg 렌더링         → 최종 9:16 영상 + 썸네일
            ↳ renderer/layout.py + renderer/thumbnail.py
```

> Phase 4.5~7은 `VIDEO_GEN_ENABLED=true`일 때만 실행.

### 상태 전이

```
COLLECTED → EDITING → APPROVED → PROCESSING → PREVIEW_RENDERED → RENDERED → UPLOADED
                                                                    ↕ DECLINED / FAILED
```

---

## 4. 하위 모듈 요약

### script/ — LLM 클라이언트 + 대본 파싱

Ollama HTTP 통신, 대본 생성, JSON 파싱·복구, 댓글 정규화, 호출 로깅.

| 파일 | 핵심 기능 |
|------|----------|
| `client.py` | `generate_script()` → ScriptData, `call_ollama_raw()` → 원시 텍스트 |
| `parser.py` | `parse_script_json()` — 3단계 JSON 파싱 (loads → repair → regex) |
| `normalizer.py` | `ensure_comments()` — LLM 댓글 누락 시 자동 주입, `split_comment_lines()` |
| `chunker.py` | `chunk_with_llm()` — Phase 2 LLM 의미 단위 청킹 |
| `logger.py` | `LLMCallTimer`, `log_llm_call()` → llm_logs 테이블 기록 |

**JSON 파싱 3단계 폴백**: `json.loads()` → `_repair_json()` → `_extract_fields_regex()`

---

### scene/ — 씬 분석·배분·검증

Phase 1(자원 분석), 3(텍스트 검증), 4+4.5(씬 배분) 실행 주체.

| 파일 | Phase | 핵심 기능 |
|------|-------|----------|
| `analyzer.py` | 1 | `analyze_resources()` → `ResourceProfile` (이미지:텍스트 비율) |
| `validator.py` | 3 | `validate_and_fix()` + `smart_split_korean()` (5단계 자연 분할) |
| `director.py` | 4, 4.5 | `SceneDirector.direct()` + `assign_video_modes()` (I2V/T2V) |
| `strategy.py` | — | `SceneMix` 데이터클래스 (향후 LLM 씬 구성 전략용) |

**SceneDecision**: 씬의 모든 정보를 담는 데이터클래스 — type, text_lines, image_url, mood, video_mode, video_prompt, video_clip_path 등 15+ 필드.

---

### pipeline/ — Phase 오케스트레이터

Phase 1~7 실행 순서와 VRAM 전환만 담당. 비즈니스 로직은 각 도메인 모듈에 위치.

| 파일 | 역할 |
|------|------|
| `content_processor.py` | `process_content()` — Phase 1~7 통합 진입점 + VRAM 2막 전환 |

---

### tts/ — TTS 음성 합성

Fish Speech 1.5 zero-shot 클로닝 기반 TTS. 한국어 텍스트 정규화 파이프라인 내장.

| 파일 | 핵심 기능 |
|------|----------|
| `fish_client.py` | `synthesize()` — HTTP 호출 + 동시성 제어 + FFmpeg 후처리 |
| `normalizer.py` | `normalize_for_tts()` — 5단계 전처리 (축약어→조사→자모→숫자→발음) |
| `number_reader.py` | `sino_number()`, `native_number()` — 숫자→한국어 읽기 변환 |

**한국어 전처리 파이프라인**: 축약어 치환 → 조사 교정 → 자모 제거 → 숫자 읽기 → 발음 교정 → 후처리(무음 단축 + 1.2배속)

**동시성 제어**: `threading.Lock` — 단일 GPU 모델 직렬화.

---

### renderer/ — FFmpeg 렌더링

Phase 8 실행 주체. `layout.py` 오케스트레이터가 3개 내부 모듈(`_frames`, `_tts`, `_encode`)을 조합.

| 파일 | 핵심 기능 |
|------|----------|
| `composer.py` | `compose_video()`, `compose_thumbnail()` — 렌더링 진입점 |
| `layout.py` | 오케스트레이터 — `_render_pipeline()` (TTS + 프레임 + 인코딩 통합) |
| `_frames.py` | PIL 프레임 렌더러 (intro/image_text/text_only/image_only/outro) |
| `_tts.py` | TTS 청크 생성·병합 (`_generate_tts_chunks`, `_merge_chunks`) |
| `_encode.py` | FFmpeg 인코딩 (`_render_video_segment`, `_render_static_segment`, `_resolve_codec`) |
| `subtitle.py` | ASS 동적 자막 (4 mood 프리셋 + 글자 수 비례 타이밍) |
| `thumbnail.py` | YouTube 썸네일 1280×720 (4 스타일: dramatic/question/funny/news) |

**렌더링 파이프라인**: 문장 구조화 → 베이스 프레임 → 이미지 다운로드 → TTS 생성 → 청크 병합 → 줄바꿈 → PIL 프레임 → 비디오/정적 세그먼트 → concat → FFmpeg 인코딩

**내부 모듈 규칙**: `_frames.py`, `_tts.py`, `_encode.py`는 언더스코어 접두사로 내부 전용. 외부에서는 `layout.py` 또는 `composer.py`의 public API만 사용.

---

### video/ — LTX-2 비디오 생성

ComfyUI 서버에 LTX-2 워크플로우를 제출하여 T2V/I2V 비디오 클립을 생성.

| 파일 | 핵심 기능 |
|------|----------|
| `manager.py` | VideoManager — 씬 순차 생성 + 4단계 폴백 + 실패 씬 병합 |
| `comfy_client.py` | ComfyUIClient — REST API (health, queue, poll) + 이미지 업로드 |
| `prompt_engine.py` | VideoPromptEngine — T2V/I2V 프롬프트 + mood 스타일링 + 단순화 |
| `image_filter.py` | `evaluate_image()` — 5기준 가중치 평가 (해상도/종횡비/텍스트/색상/엣지) |
| `video_utils.py` | FFmpeg 후처리 (NVENC 런타임 감지, 리사이즈, 루프/트림, 정규화) |
| `workflows/` | 5개 JSON (T2V full/distilled, I2V full/distilled, T2V upscale) |

**4단계 폴백 재시도**: Full(1280×720, 97f, 20step) → 프롬프트 단순화 → 해상도 다운(768×512, 65f) → Distilled(8step, CFG=1.0). 전부 실패 → 씬 삭제 + text_lines 인접 씬 병합.

---

### llm/ — deprecated

`ai_worker.script`으로 이전 완료. `__init__.py`가 `DeprecationWarning`과 함께 re-export를 제공하여 기존 import 경로를 유지한다. 모든 외부 참조 수정 후 삭제 예정.

---

## 5. 모듈 간 의존성 맵

```
                         config/settings.py
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         ai_worker/script ai_worker/tts  ai_worker/video
              │               │               │
              │               │               ├── video/prompt_engine
              │               │               │    └── script/client (call_ollama_raw)
              │               │               │    └── script/logger (LLMCallTimer)
              │               │               ├── video/image_filter
              │               │               └── video/video_utils
              │               │
              │               ├── tts/normalizer
              │               │    └── tts/number_reader
              │               └── tts/fish_client
              │
              ├── script/client
              │    └── script/parser
              │    └── script/normalizer
              ├── script/chunker
              │    └── script/logger
              │
              ▼               ▼               ▼
         ai_worker/scene
              ├── scene/analyzer ─────────── (standalone)
              ├── scene/validator ────────── (standalone)
              ├── scene/director ─────────── video/image_filter
              └── scene/strategy ─────────── (standalone)
              │
              ▼
         ai_worker/pipeline
              └── content_processor ──────── scene/analyzer, scene/validator
                                              scene/director, script/chunker
                                              tts/fish_client
                                              video/prompt_engine, video/manager
              │
              ▼
         ai_worker/renderer
              ├── composer.py ────────────── renderer/layout, renderer/thumbnail
              ├── layout.py ─────────────── _frames, _tts, _encode
              │                              tts/fish_client (TTS 생성)
              │                              video/video_utils (리사이즈)
              ├── _frames.py ────────────── layout (_load_font, lazy import)
              ├── _tts.py ───────────────── (standalone)
              ├── _encode.py ────────────── (standalone)
              ├── subtitle.py ───────────── (standalone)
              └── thumbnail.py ──────────── (standalone)
              │
              ▼
         ai_worker/core
              ├── processor.py ──────────── script/, tts/, renderer/, pipeline/, video/
              ├── gpu_manager.py ────────── (standalone, PyTorch)
              ├── main.py ───────────────── core/processor, tts/fish_client, core/shutdown
              └── shutdown.py ───────────── (standalone)
```

### 엄격한 의존성 규칙

| 규칙 | 설명 |
|------|------|
| `video/` ↛ `tts/` | 비디오 모듈은 TTS 모듈을 절대 import하지 않음 |
| `tts/` ↛ `video/` | TTS 모듈은 비디오 모듈을 절대 import하지 않음 |
| `_frames.py` → `layout.py` | lazy import만 허용 (함수 내부에서 `_load_font` import) |
| Ollama HTTP | `script/client.py`의 `call_ollama_raw()` / `generate_script()`만 사용 |
| 설정 | `config/settings.py` 경유 — 로직 내 `os.getenv()` 금지 |
| 파일 경로 | `pathlib.Path` 필수 — `os.path` 금지 |
| 로깅 | `logging.getLogger(__name__)` — `print()` 금지 |
| DB | `with SessionLocal() as db:` 블록 필수 |
| GPU | `gpu_manager.managed_inference()` 컨텍스트 매니저 사용 |

---

## 6. VRAM 2막 전략

RTX 3090 24GB에서 모든 AI 모델을 운영하기 위한 2막 구조.

```
┌─── 1막: LLM (Phase 1~6) ──────────────────────────────────────┐
│  Ollama qwen2.5:14b 8-bit (~14GB)                              │
│  ├── Phase 2: LLM 청킹 (대본 생성)                             │
│  └── Phase 6: 비디오 프롬프트 (한→영 변환)                      │
│  ※ Ollama 서버 프로세스 — ai_worker VRAM 직접 점유 아님         │
└────────────────────────────────────────────────────────────────┘
          │
          ▼  _clear_vram_for_video()
          ▼  Ollama keep_alive=0 + Fish Speech unload + empty_cache + gc
          │
┌─── 2막: 미디어 (Phase 5, 7, 8) ───────────────────────────────┐
│  Fish Speech 1.5 (~5GB) + LTX-2 GGUF Q4 (~12.7GB)             │
│  ├── Phase 5: TTS 합성                                         │
│  ├── Phase 7: 비디오 클립 생성 (ComfyUI --lowvram)              │
│  └── Phase 8: FFmpeg 렌더링 (GPU h264_nvenc or CPU libx264)     │
└────────────────────────────────────────────────────────────────┘
```

| 제약 | 값 |
|------|-----|
| 동시 모델 합계 상한 | 20GB (24GB - 4GB 안전마진) |
| LLM VRAM | ~14GB (qwen2.5:14b 8-bit) |
| TTS VRAM | ~5GB (Fish Speech 1.5) |
| VIDEO VRAM | ~12GB (LTX-2 GGUF Q4 + ComfyUI --lowvram) |

---

## 7. 비동기 워커 아키텍처

```
core/main.py
  │
  ├── _llm_tts_worker (async)
  │     ├── APPROVED 폴링 (AI_POLL_INTERVAL)
  │     ├── CUDA 세마포어 획득 (Semaphore(CUDA_CONCURRENCY))
  │     ├── processor.llm_tts_stage(post_id)
  │     │     ├── LLM 대본 생성 (GPU 1막)
  │     │     └── TTS 합성 (GPU 2막)
  │     └── render_queue.put((post_id, script, audio))
  │
  ├── _render_worker (async)
  │     ├── render_queue.get()
  │     ├── loop.run_in_executor()  ← CPU 렌더링 (스레드 풀)
  │     │     └── processor.render_stage(post_id, script, audio)
  │     │           ├── Phase 4.5~7 비디오 생성
  │     │           ├── Phase 8 렌더링
  │     │           ├── 썸네일 생성
  │     │           └── → PREVIEW_RENDERED
  │     └── render_queue.task_done()
  │
  └── _upload_loop (async)
        ├── RENDERED 폴링 (AI_POLL_INTERVAL)
        └── upload_once()
              └── → UPLOADED
```

**병렬성**: Post A가 CPU 렌더링 중일 때 Post B가 GPU LLM+TTS를 처리할 수 있다.

**Graceful Shutdown**:
1. SIGTERM/SIGINT → `request_shutdown()`
2. 모든 워커가 `is_shutting_down()` 확인 후 현재 작업 완료
3. `_drain_render_queue()` — 미처리 큐 항목 → APPROVED 복원

---

## 8. 에러 처리 전략

### processor.py 재시도

| FailureType | 재시도 가능 | 동작 |
|-------------|-----------|------|
| `LLM_ERROR` | **불가** | 즉시 중단 → FAILED |
| `TTS_ERROR` | 가능 | Exponential Backoff 재시도 |
| `RENDER_ERROR` | 가능 | Exponential Backoff 재시도 |
| `NETWORK_ERROR` | 가능 | Exponential Backoff 재시도 |
| `RESOURCE_ERROR` | 가능 | VRAM 정리 후 재시도 |
| `UNKNOWN_ERROR` | 가능 | Exponential Backoff 재시도 |

**Backoff 공식**: `delay = 5.0 × 2^(attempt-1)` → 5초, 10초, 20초...

### 모듈별 에러 전략

| 모듈 | 전략 |
|------|------|
| **script** | Ollama HTTP 2회 재시도 + JSON 3단계 폴백 + 댓글 자동 주입 |
| **scene** | 텍스트 자동 분할 보정 + I2V 부적합 → T2V 전환 |
| **tts** | HTTP 3회 재시도 + 품질 검증 3회 + FFmpeg 후처리 실패 무시 |
| **video** | 4단계 폴백 + CUBLAS 2회 연속 → 씬 스킵 + 실패 씬 병합 |
| **renderer** | 이미지 2회 재시도 + NVENC→libx264 폴백 + TTS 2회 재시도 |

---

## 9. 설정 참조

### config/settings.py 주요 항목

| 카테고리 | 설정 | 기본값 |
|---------|------|--------|
| **워커** | `AI_POLL_INTERVAL` | 10 (초) |
| | `CUDA_CONCURRENCY` | 1 |
| | `MAX_RETRY_COUNT` | 3 |
| **LLM** | `OLLAMA_HOST` | `http://localhost:11434` |
| | `OLLAMA_MODEL` | `qwen2.5:14b` |
| **TTS** | `FISH_SPEECH_URL` | `http://fish-speech:8080` |
| | `VOICE_DEFAULT` | `"default"` |
| **비디오** | `VIDEO_GEN_ENABLED` | `false` |
| | `VIDEO_RESOLUTION` | `(1280, 720)` |
| | `VIDEO_WORKFLOW_MODE` | `full` |
| | `VIDEO_I2V_THRESHOLD` | `0.6` |
| **렌더링** | `MEDIA_DIR` | `media/` |
| | `ASSETS_DIR` | `assets/` |

### 도메인별 settings.yaml

각 하위 모듈에 `settings.yaml` 파일이 위치하며, `config/settings.py`의 `get_domain_setting()`으로 조회한다.

```python
from config.settings import get_domain_setting

# 예시: core/settings.yaml → retry.max_attempts
max_retry = get_domain_setting("core", "retry", "max_attempts", default=3)

# 예시: video/settings.yaml → sampling.fps
fps = get_domain_setting("video", "sampling", "fps", default=24)
```

| 파일 | 주요 설정 |
|------|----------|
| `core/settings.yaml` | 폴링 간격, CUDA 동시성, 재시도 정책, GPU 메모리 |
| `script/settings.yaml` | Ollama 파라미터 (num_predict, temperature), 품질 기준 |
| `scene/settings.yaml` | 전략 임계값, 스택 크기, body 항목 범위, 강조 키워드 |
| `tts/settings.yaml` | 음성, 타임아웃, 후처리 (무음 제거, 배속), 품질 검증 |
| `video/settings.yaml` | 해상도, 프레임, 샘플링 (steps, CFG), I2V, ComfyUI, 제한 |
| `renderer/settings.yaml` | 코덱 (h264_nvenc), 품질 (CQ 23), FPS, 오디오, 썸네일 |

### 기타 설정 파일

| 파일 | 용도 |
|------|------|
| `config/layout.json` | 렌더러 레이아웃 Single Source of Truth |
| `config/scene_policy.json` | mood별 씬 정책 (BGM, 감정 태그, 에셋 경로) |
| `config/video_styles.json` | mood별 비주얼 스타일 (분위기, 카메라, 색상) |
| `config/pipeline.json` | 파이프라인 런타임 설정 (tts_engine, tts_voice, llm_model) |

---

## 10. Docker 서비스 구성

```yaml
services:
  ai_worker:      # 이 모듈 — 8-Phase 파이프라인
  db:             # MariaDB 11 (3306)
  fish-speech:    # TTS 서버 (8080) — ~5GB VRAM
  comfyui:        # LTX-2 비디오 생성 (8188) — ~12.7GB VRAM (--lowvram)
  dashboard:      # Streamlit UI (8501)
  crawler:        # 크롤링 루프
  monitoring:     # 헬스체크/알림
```

### 공유 볼륨

| 볼륨 | ai_worker 경로 | ComfyUI 경로 | 용도 |
|------|---------------|-------------|------|
| 비디오 출력 | `media/tmp/videos` | `/comfyui/output` | 생성된 mp4 클립 공유 |
| 워크플로우 | `ai_worker/video/workflows` | `/comfyui/custom_workflows` | JSON 워크플로우 |

### 외부 서비스 의존

| 서비스 | 프로토콜 | 사용 Phase |
|--------|---------|-----------|
| Ollama | HTTP `POST /api/generate` | Phase 2, 6 |
| Fish Speech | HTTP `POST /v1/tts` | Phase 5 |
| ComfyUI | HTTP `POST /prompt` + polling | Phase 7 |
| MariaDB | SQLAlchemy | 전 Phase (상태 관리) |
