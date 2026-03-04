# ai_worker — AI 파이프라인 워커

커뮤니티 게시글을 **LLM 대본 → TTS 음성 → LTX-2 비디오 → FFmpeg 렌더링**으로 변환하는
WaggleBot 핵심 처리 모듈. APPROVED 상태 게시글을 폴링하여 8-Phase 파이프라인을 실행하고,
PREVIEW_RENDERED → RENDERED → UPLOADED까지 자동 처리한다.

---

## 목차

1. [디렉터리 구조](#1-디렉터리-구조)
2. [루트 파일 상세](#2-루트-파일-상세)
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
├── __init__.py           # 패키지 마커 (비어 있음)
├── main.py               # 진입점 — 3-워커 asyncio 루프 (LLM+TTS / Render / Upload)
├── processor.py          # RobustProcessor — 재시도 메커니즘 + Phase 분기
├── gpu_manager.py        # GPUMemoryManager — VRAM 모니터링 + 모델 로드/언로드
├── shutdown.py           # Graceful Shutdown 이벤트 싱글톤
│
├── llm/                  # LLM 클라이언트 (Ollama HTTP + 대본 생성 + JSON 파싱)
│   ├── __init__.py
│   ├── client.py         #   generate_script(), call_ollama_raw(), summarize()
│   ├── logger.py         #   LLMCallTimer, log_llm_call()
│   └── README.md
│
├── pipeline/             # 8-Phase 콘텐츠 파이프라인 (Phase 1~7 오케스트레이션)
│   ├── __init__.py
│   ├── resource_analyzer.py  # Phase 1: 이미지:텍스트 비율 → 전략 결정
│   ├── llm_chunker.py        # Phase 2: LLM 의미 단위 청킹
│   ├── text_validator.py     # Phase 3: max_chars 검증 + 한국어 자연 분할
│   ├── scene_director.py     # Phase 4+4.5: 씬 배분 + 비디오 모드 할당
│   ├── content_processor.py  # Phase 1~7 통합 진입점
│   └── README.md
│
├── tts/                  # TTS 음성 합성 (Fish Speech + Edge-TTS)
│   ├── __init__.py       #   엔진 레지스트리 + FishSpeechTTS 어댑터
│   ├── base.py           #   BaseTTS 추상 클래스
│   ├── edge_tts.py       #   Edge-TTS 구현체 (클라우드 폴백)
│   ├── fish_client.py    #   Fish Speech HTTP + 한국어 정규화/발음 교정
│   └── README.md
│
├── renderer/             # FFmpeg 렌더링 (프레임 생성 + 자막 + 인코딩 + 썸네일)
│   ├── __init__.py
│   ├── layout.py         #   하이브리드 레이아웃 렌더러 v2 (11-Step, 메인)
│   ├── video.py          #   레거시/프리뷰 렌더러 (480×854, CPU)
│   ├── subtitle.py       #   ASS 동적 자막 생성 (4 mood 프리셋)
│   ├── thumbnail.py      #   YouTube 썸네일 (1280×720, 4 스타일)
│   └── README.md
│
└── video/                # LTX-2 비디오 생성 (ComfyUI 통신)
    ├── __init__.py
    ├── manager.py        #   VideoManager — 4단계 폴백 + 실패 씬 병합
    ├── comfy_client.py   #   ComfyUIClient — REST API + polling
    ├── prompt_engine.py  #   VideoPromptEngine — 한국어→영어 프롬프트
    ├── image_filter.py   #   evaluate_image() — I2V 적합성 5기준 평가
    ├── video_utils.py    #   FFmpeg 후처리 (리사이즈, 루프, 정규화)
    ├── workflows/        #   ComfyUI JSON 워크플로우 (5개)
    └── README.md
```

---

## 2. 루트 파일 상세

### main.py (~289행) — 진입점

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

### processor.py (~929행) — RobustProcessor

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

#### llm_tts_stage — use_content_processor 분기

| 모드 | 대본 생성 | 비고 |
|------|----------|------|
| `use_content_processor=true` | `chunk_with_llm()` → ScriptData 수동 조립 | Phase 2 LLM JSON 모드 |
| 기타 (기본) | `generate_script()` | 레거시 경로 |

### gpu_manager.py (~481행) — GPUMemoryManager

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

#### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `MODEL_VRAM_REQUIREMENTS` | LLM=14, TTS=5, VIDEO=12, OTHER=2 | 모델별 예상 VRAM (GB) |
| `MAX_COEXIST_VRAM_GB` | 20.0 | 동시 상주 합계 상한 (24GB 중 4GB 마진) |

### shutdown.py (~35행) — Graceful Shutdown

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
  │         ↳ resource_analyzer.py
Phase 2   chunk_with_llm       → raw script dict (LLM 의미 단위 청킹)
  │         ↳ llm_chunker.py + llm/client.py
Phase 3   validate_and_fix     → validated script dict (max_chars 검증)
  │         ↳ text_validator.py
Phase 4   SceneDirector        → list[SceneDecision] (씬 배분 + 감정 태그 + BGM)
  │         ↳ scene_director.py
Phase 4.5 assign_video_modes   → video_mode = t2v | i2v | static
  │         ↳ scene_director.py + video/image_filter.py
Phase 5   TTS 생성              → scene.text_lines = [{"text", "audio"}]
  │         ↳ tts/fish_client.py
Phase 6   video_prompt 생성     → scene.video_prompt (한국어→영어)
  │         ↳ video/prompt_engine.py + llm/client.py
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

### llm/ — LLM 클라이언트

Ollama HTTP 통신, 대본 생성, JSON 파싱, 호출 로깅. 모든 LLM 호출의 단일 진입점.

| 파일 | 핵심 기능 |
|------|----------|
| `client.py` | `generate_script()` → ScriptData, `call_ollama_raw()` → 원시 텍스트 |
| `logger.py` | `LLMCallTimer`, `log_llm_call()` → llm_logs 테이블 기록 |

**JSON 파싱 3단계 폴백**: `json.loads()` → `_repair_json()` → `_extract_fields_regex()`

→ 상세: [llm/README.md](llm/README.md)

---

### pipeline/ — 8-Phase 오케스트레이터

Phase 1~4를 직접 구현하고, Phase 5~7은 외부 모듈을 호출한다.

| 파일 | Phase | 핵심 기능 |
|------|-------|----------|
| `resource_analyzer.py` | 1 | 이미지:텍스트 비율 → `img_heavy` / `balanced` / `text_heavy` |
| `llm_chunker.py` | 2 | Ollama JSON 모드 → `{hook, body[], closer}` |
| `text_validator.py` | 3 | max_chars 검증 + 한국어 5단계 자연 분할 |
| `scene_director.py` | 4, 4.5 | SceneDecision 배분 + I2V 적합성 → video_mode 할당 |
| `content_processor.py` | 1~7 | 통합 진입점 (`process_content()`) |

**SceneDecision**: 씬의 모든 정보를 담는 데이터클래스 — type, text_lines, image_url, mood, video_mode, video_prompt, video_clip_path 등 15+ 필드.

→ 상세: [pipeline/README.md](pipeline/README.md)

---

### tts/ — TTS 음성 합성

Fish Speech 1.5 zero-shot 클로닝(메인) + Edge-TTS(폴백).

| 파일 | 핵심 기능 |
|------|----------|
| `__init__.py` | 엔진 레지스트리 — `get_tts_engine("fish-speech")` |
| `base.py` | `BaseTTS` 추상 인터페이스 |
| `edge_tts.py` | Microsoft Edge 클라우드 TTS |
| `fish_client.py` | Fish Speech HTTP + 5단계 한국어 정규화 + 발음 교정 |

**한국어 전처리 파이프라인**: 축약어 치환 → 조사 교정 → 자모 제거 → 숫자 읽기 → 발음 교정 → 후처리(무음 단축 + 1.2배속)

**동시성 제어**: `threading.Lock` — 단일 GPU 모델 직렬화.

→ 상세: [tts/README.md](tts/README.md)

---

### renderer/ — FFmpeg 렌더링

Phase 8 실행 주체. 메인(1080×1920 GPU) + 프리뷰(480×854 CPU) 두 경로 제공.

| 파일 | 핵심 기능 |
|------|----------|
| `layout.py` | 하이브리드 레이아웃 렌더러 v2 (11-Step, PIL + LTX-Video + FFmpeg) |
| `video.py` | 레거시/프리뷰 (이미지 슬라이드쇼 + ASS 자막 + Ken Burns) |
| `subtitle.py` | ASS 동적 자막 (4 mood 프리셋 + 글자 수 비례 타이밍) |
| `thumbnail.py` | YouTube 썸네일 1280×720 (4 스타일: dramatic/question/funny/news) |

**11-Step 파이프라인** (layout.py): 문장 구조화 → 베이스 프레임 → 씬 배분 → 이미지 다운로드 → TTS 생성 → 청크 병합 → 줄바꿈 → PIL 프레임 → 비디오/정적 세그먼트 → concat → FFmpeg 인코딩

→ 상세: [renderer/README.md](renderer/README.md)

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

→ 상세: [video/README.md](video/README.md)

---

## 5. 모듈 간 의존성 맵

```
                         config/settings.py
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
         ai_worker/llm   ai_worker/tts   ai_worker/video
              │               │               │
              │               │               ├── video/prompt_engine
              │               │               │    └── llm/client (call_ollama_raw)
              │               │               │    └── llm/logger (LLMCallTimer, log_llm_call)
              │               │               ├── video/image_filter
              │               │               └── video/video_utils
              │               │
              ▼               ▼               ▼
         ai_worker/pipeline
              ├── resource_analyzer ────────── (standalone)
              ├── llm_chunker ─────────────── llm/logger
              ├── text_validator ───────────── (standalone)
              ├── scene_director ───────────── video/image_filter
              └── content_processor ────────── tts/fish_client
                                               video/prompt_engine
                                               video/manager + comfy_client
              │
              ▼
         ai_worker/renderer
              ├── layout.py ───────────────── tts/fish_client (TTS 생성)
              │                                video/video_utils (리사이즈, 루프)
              ├── video.py ────────────────── llm/client (ScriptData)
              │                                renderer/subtitle
              ├── subtitle.py ─────────────── (standalone)
              └── thumbnail.py ────────────── (standalone)
              │
              ▼
         ai_worker/processor.py ───────────── llm/, tts/, renderer/, pipeline/, video/
         ai_worker/gpu_manager.py ─────────── (standalone, PyTorch)
         ai_worker/main.py ────────────────── processor, tts/fish_client, shutdown
```

### 엄격한 의존성 규칙

| 규칙 | 설명 |
|------|------|
| `video/` ↛ `tts/` | 비디오 모듈은 TTS 모듈을 절대 import하지 않음 |
| `tts/` ↛ `video/` | TTS 모듈은 비디오 모듈을 절대 import하지 않음 |
| Ollama HTTP | `llm/client.py`의 `call_ollama_raw()` / `generate_script()`만 사용 |
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
          ▼  torch.cuda.empty_cache() + gc.collect()
          ▼  emergency_cleanup() (VRAM 부족 시)
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
main.py
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
| **llm** | Ollama HTTP 2회 재시도 + JSON 3단계 폴백 + 댓글 자동 주입 |
| **pipeline** | 텍스트 자동 분할 보정 + I2V 부적합 → T2V 전환 |
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

### 설정 파일

| 파일 | 용도 |
|------|------|
| `config/settings.py` | 설정 허브 (모든 환경변수 로드) |
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
