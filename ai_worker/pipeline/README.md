# ai_worker/pipeline — 8-Phase 콘텐츠 파이프라인

커뮤니티 게시글을 LLM 대본 → TTS → 비디오 프롬프트 → 비디오 클립으로 변환하는 핵심 파이프라인.
Phase 1~4는 이 모듈에서 직접 구현하고, Phase 5~7은 외부 모듈을 오케스트레이션한다.

---

## 파일 구조

```
ai_worker/pipeline/
├── __init__.py            # 퍼블릭 API re-export
├── resource_analyzer.py   # Phase 1: 자원 분석
├── llm_chunker.py         # Phase 2: LLM 의미 단위 청킹
├── text_validator.py      # Phase 3: 텍스트 물리적 검증/보정
├── scene_director.py      # Phase 4 + 4.5: 씬 배분 + 비디오 모드 할당
├── content_processor.py   # Phase 1~7 통합 오케스트레이터
└── README.md              # (이 파일)
```

---

## Phase 흐름도

```
Phase 1  analyze_resources   → ResourceProfile (이미지:텍스트 비율 분석)
  │
Phase 2  chunk_with_llm      → raw script dict (LLM 의미 단위 청킹)
  │
Phase 3  validate_and_fix    → validated script dict (max_chars 검증/보정)
  │
Phase 4  SceneDirector       → list[SceneDecision] (씬 배분 + 감정 태그 + BGM)
  │
Phase 4.5 assign_video_modes → SceneDecision에 video_mode 설정 (t2v/i2v/static)
  │
Phase 5  TTS 합성            → scene.text_lines = [{"text": ..., "audio": ...}]
  │                             (ai_worker/tts/ 모듈 호출)
Phase 6  video_prompt 생성   → scene.video_prompt (한국어→영어 변환)
  │                             (ai_worker/video/prompt_engine 호출)
Phase 7  video_clip 생성     → scene.video_clip_path (ComfyUI LTX-2)
                                (ai_worker/video/manager 호출)
```

> Phase 4.5~7은 `VIDEO_GEN_ENABLED=true`일 때만 실행.

---

## 퍼블릭 API

`__init__.py`에서 re-export하므로 `from ai_worker.pipeline import ...`로 사용 가능.

| 심볼 | 출처 | 용도 |
|------|------|------|
| `process_content()` | content_processor.py | Phase 1~7 통합 진입점 |
| `analyze_resources()` | resource_analyzer.py | Phase 1 자원 분석 |
| `ResourceProfile` | resource_analyzer.py | 자원 분석 결과 데이터클래스 |
| `chunk_with_llm()` | llm_chunker.py | Phase 2 LLM 청킹 |
| `validate_and_fix()` | text_validator.py | Phase 3 텍스트 검증 |
| `SceneDirector` | scene_director.py | Phase 4 씬 배분기 |
| `SceneDecision` | scene_director.py | 씬 결정 데이터클래스 |

---

## resource_analyzer.py — Phase 1: 자원 분석

이미지:텍스트 비율을 분석하여 LLM 청킹 전략을 결정한다.

### ResourceProfile

```python
@dataclass
class ResourceProfile:
    image_count: int           # 이미지 수
    text_length: int           # 원문 글자 수
    estimated_sentences: int   # 예상 문장 수 (text_length // 25)
    ratio: float               # image_count / estimated_sentences
    strategy: Strategy         # "img_heavy" | "balanced" | "text_heavy"
```

### analyze_resources

```python
def analyze_resources(post, images: list[str]) -> ResourceProfile:
```

| 전략 | ratio 조건 | 설명 |
|------|-----------|------|
| `img_heavy` | ratio >= 0.7 | 거의 모든 문장에 이미지 |
| `balanced` | 0.3 <= ratio < 0.7 | 중요 문장에만 이미지 |
| `text_heavy` | ratio < 0.3 | 텍스트 위주, 이미지 절약 |

### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `_KO_CHARS_PER_SENTENCE` | 25 | 한국어 평균 문장당 글자 수 |

---

## llm_chunker.py — Phase 2: LLM 의미 단위 청킹

ResourceProfile의 전략에 맞는 프롬프트를 생성하고 Ollama JSON 모드로 대본을 분절한다.

### 함수

```python
def create_chunking_prompt(
    post_content: str,
    profile: ResourceProfile,
    *,
    extended: bool = False,    # True → title_suggestion/tags/mood 필드 추가
) -> str:
```
- `_STRATEGY_GUIDE`에서 전략별 가이드 주입
- `get_llm_constraints_prompt()`로 글자 수 제약 삽입
- 원문 최대 2000자, body 항목 6~23개, lines 21자 이내 규칙

```python
async def chunk_with_llm(
    post_content: str,
    profile: ResourceProfile,
    *,
    post_id: int | None = None,
    extended: bool = False,
) -> dict:
```
- `asyncio.to_thread()`로 Ollama JSON 모드 호출 (`format: "json"`)
- `num_predict=1500`, `temperature=0.7`
- 필수 키 검증: `hook`, `body`, `closer`
- `LLMCallTimer` + `log_llm_call()`로 항상 로그 기록 (finally)

### 출력 형식

```json
{
  "hook": "첫 3초 후킹 문장",
  "body": [
    {"line_count": 2, "lines": ["줄1", "줄2"]},
    {"line_count": 1, "lines": ["단일 줄"], "type": "comment"}
  ],
  "closer": "마무리 멘트",
  "title_suggestion": "...",   // extended=True 시
  "tags": ["태그1", "태그2"],  // extended=True 시
  "mood": "humor"              // extended=True 시
}
```

### 전략별 가이드 (`_STRATEGY_GUIDE`)

| 전략 | 가이드 |
|------|--------|
| `img_heavy` | 각 문장 짧고 임팩트 있게. 이미지마다 한 문장. |
| `balanced` | 핵심 문장과 보조 문장을 구분해서 작성. |
| `text_heavy` | 텍스트만으로 몰입되도록 자세히 작성. |

### 내부 함수

| 함수 | 역할 |
|------|------|
| `_call_ollama_json(prompt, model)` | Ollama `/api/generate` JSON 모드 POST (별도 세션, client.py와 독립) |

### 의존성

```python
from config.settings import OLLAMA_MODEL, MAX_BODY_CHARS, get_llm_constraints_prompt, get_ollama_host
from ai_worker.llm.logger import LLMCallTimer, log_llm_call
```

---

## text_validator.py — Phase 3: 텍스트 물리적 검증

LLM 출력의 글자 수 제약을 검증하고 초과 시 한국어 자연 단위로 분할 보정한다.

### 함수

```python
def smart_split_korean(text: str, max_chars: int = MAX_BODY_CHARS) -> list[str]:
```

5단계 분할 우선순위:

| 우선순위 | 분할 기준 | 탐색 범위 |
|---------|---------|----------|
| 1 | 문장부호 (`. ` `? ` `! `) | max_chars의 60% 이후 |
| 2 | 쉼표 (`, `) | max_chars의 60% 이후 |
| 3 | 접속사 (근데, 그래서, 하지만 등) | max_chars의 50% 이후 |
| 4 | 어절 (공백) | 아무 위치 |
| 5 | 강제 분할 | max_chars 위치 |

```python
def validate_and_fix(llm_output: dict) -> dict:
```
- **hook**: `MAX_HOOK_CHARS` 초과 시 첫 청크로 축약
- **closer**: `MAX_BODY_CHARS` 초과 시 첫 청크로 축약
- **body**: 각 lines 요소가 `MAX_BODY_CHARS` 초과 시 `smart_split_korean()` 분할
- 부가 필드 보존 (`type`, `author` 등)

### 접속사 목록 (`_CONNECTORS`)

```python
["근데 ", "그래서 ", "그런데 ", "하지만 ", "그리고 ", "그래도 ", "그러면 "]
```

### 의존성

```python
from config.settings import MAX_BODY_CHARS, MAX_HOOK_CHARS
```

---

## scene_director.py — Phase 4 + 4.5: 씬 배분

validated script + ResourceProfile + images를 받아 SceneDecision 목록을 생성한다.

### SceneDecision

```python
@dataclass
class SceneDecision:
    # ── 기본 필드 ──
    type: SceneType               # "intro" | "img_text" | "text_only" | "img_only" | "outro"
    text_lines: list              # str 또는 {"text": str, "audio": str|None}
    image_url: str | None         # img_text / outro 에서 사용
    text_only_stack: int = 1      # text_only 스택 줄 수
    emotion_tag: str = ""         # Fish Speech 감정 태그
    voice_override: str | None    # 댓글 씬: comment_voices에서 선택
    mood: str = "daily"           # 콘텐츠 mood 키
    tts_emotion: str = ""         # TTS 감정 톤 키
    bgm_path: str | None          # BGM 파일 경로 (intro에만)
    block_type: str = "body"      # "body" | "comment" (렌더링 UI 분기)
    author: str | None            # comment 작성자 닉네임
    pre_split_lines: list | None  # 편집실 원본 줄바꿈
    # ── LTX-Video 필드 ──
    video_clip_path: str | None       # 생성된 비디오 클립 경로
    video_prompt: str | None          # LTX-Video용 영어 프롬프트
    video_mode: str | None            # "t2v" | "i2v" | "static"
    video_init_image: str | None      # I2V 초기 프레임 경로
    video_generation_failed: bool     # 비디오 생성 최종 실패 여부
```

### SceneDirector

```python
class SceneDirector:
    def __init__(
        self,
        profile: ResourceProfile,
        images: list[str],
        script: dict,
        comment_voices: list[str] | None = None,
        mood: str = "daily",
    ) -> None: ...

    def direct(self) -> list[SceneDecision]: ...
```

`direct()` 씬 배분 흐름:

```
1. scene_policy.json 로드 (mood별 프리셋)
2. BGM 선택 (intro에만)
3. Intro 씬: 이미지 있으면 img_text, 없으면 mood 폴더 에셋
4. Body 씬: distribute_images()로 배분
5. Outro 씬: mood 폴더 에셋 + fixed_texts 랜덤
```

### distribute_images

```python
def distribute_images(
    body_items: list[tuple[str, str|None, str, str|None, list[str]|None]],
    images: list[str],
    max_images: int,
    tts_emotion: str = "",
    mood: str = "daily",
) -> list[SceneDecision]:
```

씬 유형 균형 배분 + video_mode 사전 할당:

| 조건 | 비율 | 배분 |
|------|------|------|
| 이미지 있음 | 1:1:1 | 비디오(t2v) : 정적텍스트(static) : 이미지+텍스트(static) |
| 이미지 없음 | 1:1 | 비디오(t2v) : 정적텍스트(static) |

- 이미지 위치는 균등 간격(interval) 배분
- 비디오/정적은 교대 배치

### assign_video_modes (Phase 4.5)

```python
def assign_video_modes(
    scenes: list,
    image_cache_dir: Path,
    i2v_threshold: float = 0.6,
) -> list:
```

`distribute_images()`에서 사전 할당되지 않은 씬(intro/outro 등)의 video_mode를 결정:

| 씬 유형 | 규칙 |
|---------|------|
| text_only | → `"t2v"` |
| img_text / img_only (이미지 있음) | image_filter 평가 → score >= threshold → `"i2v"`, 아니면 `"t2v"` |
| img_text (extreme_aspect_ratio) | → `"t2v"` 강제 (LTX-2 CUBLAS 에러 방지) |
| intro / outro | → `"t2v"` |

### 이미지 다운로드 헬퍼

| 함수 | 역할 |
|------|------|
| `_download_and_cache_image(url, cache_dir)` | URL → 로컬 캐시 저장 (2회 재시도, DCInside 쿠키 대응) |
| `_get_dc_session()` | DCInside CDN용 requests 세션 (쿠키 워밍업) |
| `_is_dc_url(url)` | DCInside CDN URL 여부 판별 |
| `pick_random_file(dir_path, extensions)` | 폴더에서 랜덤 파일 선택 (BGM/이미지 에셋용) |

### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `_HIGHLIGHT_KEYWORDS` | `["반전", "충격", ...]` | 반전/충격 키워드 감지 (단독 강조 처리) |
| `_STACK_BY_STRATEGY` | `{img_heavy: 1, balanced: 2, text_heavy: 3}` | 전략별 text_only 스택 크기 |

### 의존성

```python
from ai_worker.pipeline.resource_analyzer import ResourceProfile
from ai_worker.video.image_filter import evaluate_image   # Phase 4.5에서 lazy import
from config.settings import EMOTION_TAGS
```

---

## content_processor.py — Phase 1~7 통합 오케스트레이터

전체 파이프라인을 순서대로 실행하는 단일 진입점.

### process_content

```python
async def process_content(
    post,                          # Post 객체 (post.content, post.title, post.id)
    images: list[str],             # 이미지 URL/경로 목록
    cfg: dict | None = None,       # 파이프라인 설정 (comment_voices 등)
) -> list[SceneDecision]:
```

실행 순서:

| 단계 | 호출 | 조건 |
|------|------|------|
| Phase 1 | `analyze_resources(post, images)` | 항상 |
| Phase 2 | `chunk_with_llm(content, profile)` | 항상 |
| Phase 3 | `validate_and_fix(llm_output)` | 항상 |
| Phase 4 | `SceneDirector(...).direct()` | 항상 |
| Phase 4.5 | `assign_video_modes(scenes, ...)` | `VIDEO_GEN_ENABLED` |
| Phase 5 | `synthesize()` (Fish Speech TTS) | 항상 |
| Phase 6 | `VideoPromptEngine.generate_batch()` | `VIDEO_GEN_ENABLED` |
| Phase 7 | `VideoManager.generate_all_clips()` | `VIDEO_GEN_ENABLED` |

### _clear_vram_for_video

```python
async def _clear_vram_for_video() -> None:
```

Phase 7 진입 전 1막→2막 VRAM 전환 시퀀스:

```
1. Ollama 강제 언로드 (keep_alive=0)
2. Fish Speech 모델 언로드 (/v1/models/unload)
3. torch.cuda.empty_cache() + gc.collect()
4. nvidia-smi 여유 VRAM 확인 (< 20GB 시 긴급 정리)
```

### 의존성 (외부 모듈 호출)

| Phase | 외부 모듈 |
|-------|---------|
| 5 (TTS) | `ai_worker.tts.fish_client.synthesize` |
| 6 (프롬프트) | `ai_worker.video.prompt_engine.VideoPromptEngine` |
| 7 (비디오) | `ai_worker.video.manager.VideoManager`, `ai_worker.video.comfy_client.ComfyUIClient` |
| VRAM 관리 | `ai_worker.gpu_manager.GPUMemoryManager` |

---

## 외부 사용처

### 파이프라인 코어

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `ai_worker/processor.py` | `analyze_resources`, `SceneDirector`, `validate_and_fix`, `assign_video_modes`, `chunk_with_llm` | 메인 프로세서에서 Phase별 개별 호출 |

### 대시보드

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `dashboard/workers/hd_render.py` | `analyze_resources`, `SceneDirector`, `validate_and_fix` | HD 렌더링 백그라운드 태스크 |

### 테스트

| 파일 | import 대상 |
|------|-------------|
| `test/test_full_pipeline_e2e.py` | `analyze_resources`, `chunk_with_llm`, `create_chunking_prompt`, `validate_and_fix`, `SceneDirector`, `assign_video_modes` |
| `test/test_pipeline_phases.py` | 동일 |
| `test/run_scene_scenarios.py` | `ResourceProfile`, `SceneDecision`, `SceneDirector` |
| `test/test_scene_director_dc_download.py` | `_download_and_cache_image`, `_get_dc_session`, `_is_dc_url` |
| `test/test_image_rendering.py` | `SceneDirector`, `ResourceProfile`, `distribute_images`, `SceneDecision` |
| `test/test_scene_policy.py` | `pick_random_file`, `distribute_images`, `ResourceProfile`, `SceneDirector` |

---

## 설정 참조 (config/)

### config/settings.py

| 설정 | 기본값 | 사용 Phase |
|------|--------|-----------|
| `OLLAMA_MODEL` | `qwen2.5:14b` | Phase 2 |
| `MAX_BODY_CHARS` | layout.json에서 로드 | Phase 2, 3 |
| `MAX_HOOK_CHARS` | layout.json에서 로드 | Phase 3 |
| `VIDEO_GEN_ENABLED` | `false` | Phase 4.5~7 게이트 |
| `VIDEO_I2V_THRESHOLD` | `0.6` | Phase 4.5 |
| `EMOTION_TAGS` | scene_type별 빈 문자열 | Phase 4 |

### config/scene_policy.json

| 키 | 용도 |
|-----|------|
| `moods.{mood}.tts_emotion` | TTS 감정 톤 키 |
| `moods.{mood}.intro_image_dir` | intro 에셋 폴더 |
| `moods.{mood}.outro_image_dir` | outro 에셋 폴더 |
| `moods.{mood}.bgm_dir` | BGM 폴더 |
| `defaults.max_body_images` | 본문 최대 이미지 수 |
| `defaults.fallback_mood` | 미인식 mood 폴백 |
| `scene_rules.outro.fixed_texts` | outro 고정 텍스트 목록 |

### config/layout.json

| 키 | 용도 |
|-----|------|
| `constraints.hook_text.max_chars` | Phase 3 hook 검증 |
| `constraints.body_sentence.max_chars` | Phase 2, 3 body 검증 |
| `constraints.post_title.max_chars` | 제목 검증 |

---

## 데이터 흐름 요약

```
Post (DB)
  │
  ├─ post.content ──→ Phase 1 ──→ ResourceProfile { strategy, ratio, ... }
  │                       │
  │                       ▼
  ├─ post.content ──→ Phase 2 ──→ { hook, body[], closer, mood, tags }
  │                       │
  │                       ▼
  │                   Phase 3 ──→ { hook, body[], closer } (max_chars 보정)
  │                       │
  │                       ▼
  ├─ images[] ──────→ Phase 4 ──→ list[SceneDecision]
  │                       │         type, text_lines, image_url, mood, bgm, ...
  │                       ▼
  │                  Phase 4.5 ──→ SceneDecision.video_mode = t2v | i2v | static
  │                       │
  │                       ▼
  │                   Phase 5 ──→ text_lines = [{"text": ..., "audio": path}]
  │                       │         (Fish Speech TTS)
  │                       ▼
  │                   Phase 6 ──→ SceneDecision.video_prompt = "English prompt..."
  │                       │         (Ollama 한→영 변환)
  │                       ▼
  │                   Phase 7 ──→ SceneDecision.video_clip_path = "media/.../clip.mp4"
  │                                 (ComfyUI LTX-2)
  ▼
list[SceneDecision] ──→ Phase 8 (ai_worker/renderer/) ──→ 최종 영상
```
