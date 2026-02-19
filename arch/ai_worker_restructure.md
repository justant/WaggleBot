# ai_worker 디렉토리 구조 재편 계획

## 목적

`ai_worker/` 는 현재 단일 flat 디렉토리에 여러 기능이 혼재되어 있다.  
같은 Docker 컨테이너를 유지하되, **기능 도메인별 서브디렉토리**로 파일을 분리한다.

---

## 현재 구조 → 목표 구조

### AS-IS (현재)

```
ai_worker/
├── __init__.py
├── main.py               # 진입점 / 워커 루프
├── processor.py          # 파이프라인 오케스트레이터
├── gpu_manager.py        # GPU/VRAM 관리
├── content_processor.py  # Phase 1~5 통합 실행
├── resource_analyzer.py  # Phase 1: 자원 분석
├── llm_chunker.py        # Phase 2: LLM 청킹
├── text_validator.py     # Phase 3: 텍스트 검증
├── scene_director.py     # Phase 4: 씬 배분
├── llm.py                # LLM 호출 (generate_script)
├── llm_logger.py         # LLM 호출 이력 DB 저장
├── tts_worker.py         # Fish Speech HTTP 클라이언트
├── tts/                  # 레거시 TTS 엔진 (edge/kokoro/gptsovits)
│   ├── __init__.py
│   ├── base.py
│   ├── edge_tts.py
│   ├── kokoro.py
│   └── gptsovits.py
├── layout_renderer.py    # 레이아웃 렌더러 (PIL + FFmpeg)
├── video.py              # 레거시 FFmpeg 영상 렌더링
├── subtitle.py           # ASS 자막 생성
└── thumbnail.py          # 썸네일 생성
```

### TO-BE (목표)

```
ai_worker/
├── __init__.py
├── main.py                      # 진입점 / 워커 루프 (이동 없음)
├── processor.py                 # 파이프라인 오케스트레이터 (이동 없음)
├── gpu_manager.py               # GPU/VRAM 관리 (이동 없음)
│
├── pipeline/                    # ★ 5-Phase 콘텐츠 파이프라인
│   ├── __init__.py
│   ├── content_processor.py     # ← ai_worker/content_processor.py
│   ├── resource_analyzer.py     # ← ai_worker/resource_analyzer.py
│   ├── llm_chunker.py           # ← ai_worker/llm_chunker.py
│   ├── text_validator.py        # ← ai_worker/text_validator.py
│   └── scene_director.py        # ← ai_worker/scene_director.py
│
├── llm/                         # ★ LLM 호출 / 로깅
│   ├── __init__.py
│   ├── client.py                # ← ai_worker/llm.py (ScriptData, generate_script)
│   └── logger.py                # ← ai_worker/llm_logger.py
│
├── tts/                         # ★ TTS (Fish Speech + 레거시 엔진)
│   ├── __init__.py
│   ├── fish_client.py           # ← ai_worker/tts_worker.py
│   ├── base.py                  # ← ai_worker/tts/base.py (이동 없음)
│   ├── edge_tts.py              # ← ai_worker/tts/edge_tts.py
│   ├── kokoro.py                # ← ai_worker/tts/kokoro.py
│   └── gptsovits.py             # ← ai_worker/tts/gptsovits.py
│
└── renderer/                    # ★ 영상 / 이미지 렌더링
    ├── __init__.py
    ├── layout.py                # ← ai_worker/layout_renderer.py
    ├── video.py                 # ← ai_worker/video.py
    ├── subtitle.py              # ← ai_worker/subtitle.py
    └── thumbnail.py             # ← ai_worker/thumbnail.py
```

---

## 파일 이동 목록 (전체)

| 현재 경로 | 이동 후 경로 | 비고 |
|---|---|---|
| `ai_worker/content_processor.py` | `ai_worker/pipeline/content_processor.py` | |
| `ai_worker/resource_analyzer.py` | `ai_worker/pipeline/resource_analyzer.py` | |
| `ai_worker/llm_chunker.py` | `ai_worker/pipeline/llm_chunker.py` | |
| `ai_worker/text_validator.py` | `ai_worker/pipeline/text_validator.py` | |
| `ai_worker/scene_director.py` | `ai_worker/pipeline/scene_director.py` | |
| `ai_worker/llm.py` | `ai_worker/llm/client.py` | 파일명 변경 |
| `ai_worker/llm_logger.py` | `ai_worker/llm/logger.py` | 파일명 변경 |
| `ai_worker/tts_worker.py` | `ai_worker/tts/fish_client.py` | 파일명 변경 |
| `ai_worker/tts/base.py` | `ai_worker/tts/base.py` | 경로 동일, 내용 유지 |
| `ai_worker/tts/edge_tts.py` | `ai_worker/tts/edge_tts.py` | 경로 동일, 내용 유지 |
| `ai_worker/tts/kokoro.py` | `ai_worker/tts/kokoro.py` | 경로 동일, 내용 유지 |
| `ai_worker/tts/gptsovits.py` | `ai_worker/tts/gptsovits.py` | 경로 동일, 내용 유지 |
| `ai_worker/layout_renderer.py` | `ai_worker/renderer/layout.py` | 파일명 변경 |
| `ai_worker/video.py` | `ai_worker/renderer/video.py` | |
| `ai_worker/subtitle.py` | `ai_worker/renderer/subtitle.py` | |
| `ai_worker/thumbnail.py` | `ai_worker/renderer/thumbnail.py` | |

> `main.py`, `processor.py`, `gpu_manager.py`, `__init__.py` 는 `ai_worker/` 루트에 유지한다.

---

## import 경로 변경 목록

이동 후 반드시 수정해야 할 import 문이다.  
**변경이 필요한 파일과 수정 내용을 열거한다.**

### `ai_worker/main.py`

```python
# 변경 전
from ai_worker.tts_worker import wait_for_fish_speech
from ai_worker.processor import RobustProcessor

# 변경 후
from ai_worker.tts.fish_client import wait_for_fish_speech
from ai_worker.processor import RobustProcessor  # 그대로
```

### `ai_worker/processor.py`

```python
# 변경 전
from ai_worker.llm import ScriptData, generate_script, summarize
from ai_worker.llm_logger import LLMCallTimer, log_llm_call   # (간접 사용)
from ai_worker.tts import get_tts_engine
from ai_worker.video import render_preview
from ai_worker.thumbnail import generate_thumbnail, get_thumbnail_path
from ai_worker.layout_renderer import render_layout_video_from_scenes
from ai_worker.resource_analyzer import analyze_resources
from ai_worker.scene_director import SceneDirector
from ai_worker.text_validator import validate_and_fix
from ai_worker.llm_chunker import chunk_with_llm

# 변경 후
from ai_worker.llm.client import ScriptData, generate_script, summarize
from ai_worker.tts import get_tts_engine
from ai_worker.renderer.video import render_preview
from ai_worker.renderer.thumbnail import generate_thumbnail, get_thumbnail_path
from ai_worker.renderer.layout import render_layout_video_from_scenes
from ai_worker.pipeline.resource_analyzer import analyze_resources
from ai_worker.pipeline.scene_director import SceneDirector
from ai_worker.pipeline.text_validator import validate_and_fix
from ai_worker.pipeline.llm_chunker import chunk_with_llm
```

### `ai_worker/pipeline/content_processor.py`

```python
# 변경 전
from ai_worker.llm_chunker import chunk_with_llm
from ai_worker.resource_analyzer import ResourceProfile, analyze_resources
from ai_worker.scene_director import SceneDecision, SceneDirector
from ai_worker.text_validator import validate_and_fix
from ai_worker.tts_worker import synthesize

# 변경 후
from ai_worker.pipeline.llm_chunker import chunk_with_llm
from ai_worker.pipeline.resource_analyzer import ResourceProfile, analyze_resources
from ai_worker.pipeline.scene_director import SceneDecision, SceneDirector
from ai_worker.pipeline.text_validator import validate_and_fix
from ai_worker.tts.fish_client import synthesize
```

### `ai_worker/pipeline/llm_chunker.py`

```python
# 변경 전
from ai_worker.resource_analyzer import ResourceProfile
from ai_worker.llm_logger import LLMCallTimer, log_llm_call

# 변경 후
from ai_worker.pipeline.resource_analyzer import ResourceProfile
from ai_worker.llm.logger import LLMCallTimer, log_llm_call
```

### `ai_worker/pipeline/scene_director.py`

```python
# 변경 전
from ai_worker.resource_analyzer import ResourceProfile

# 변경 후
from ai_worker.pipeline.resource_analyzer import ResourceProfile
```

### `ai_worker/llm/client.py` (구 llm.py)

```python
# 변경 전
from ai_worker.llm_logger import LLMCallTimer, log_llm_call

# 변경 후
from ai_worker.llm.logger import LLMCallTimer, log_llm_call
```

### `ai_worker/llm/logger.py` (구 llm_logger.py)

```python
# 변경 없음 — db.models / db.session 만 참조하므로 수정 불필요
```

### `ai_worker/tts/__init__.py`

```python
# 변경 전
from ai_worker.tts.base import BaseTTS
from ai_worker.tts.edge_tts import EdgeTTS
from ai_worker.tts.kokoro import KokoroTTS
from ai_worker.tts.gptsovits import GptSoVITS

# 변경 없음 — 이미 ai_worker/tts/ 내부 참조이므로 수정 불필요
```

### `ai_worker/tts/fish_client.py` (구 tts_worker.py)

```python
# 변경 없음 — config.settings 만 참조하므로 수정 불필요
```

### `ai_worker/renderer/layout.py` (구 layout_renderer.py)

```python
# 변경 전
from ai_worker.video import _check_nvenc
from ai_worker.tts_worker import synthesize as fish_synthesize

# 변경 후
from ai_worker.renderer.video import _check_nvenc
from ai_worker.tts.fish_client import synthesize as fish_synthesize
```

### `ai_worker/renderer/video.py` (구 video.py)

```python
# 변경 전
from ai_worker.llm import ScriptData
from ai_worker.subtitle import get_comment_timings, write_ass_file

# 변경 후
from ai_worker.llm.client import ScriptData
from ai_worker.renderer.subtitle import get_comment_timings, write_ass_file
```

### `ai_worker/renderer/subtitle.py`, `ai_worker/renderer/thumbnail.py`

```python
# 변경 없음 — 외부 모듈 참조 없음
```

---

## `__init__.py` 파일 생성 목록

새 서브디렉토리에 각각 빈 `__init__.py` 를 생성한다.  
`llm/` 의 경우 외부에서 `from ai_worker.llm import ScriptData` 형태로 쓰이므로 re-export 를 추가한다.

### `ai_worker/pipeline/__init__.py`

```python
# 빈 파일 (또는 아래처럼 공개 API 명시)
from ai_worker.pipeline.content_processor import process_content
from ai_worker.pipeline.resource_analyzer import ResourceProfile, analyze_resources
from ai_worker.pipeline.scene_director import SceneDecision, SceneDirector
from ai_worker.pipeline.text_validator import validate_and_fix
from ai_worker.pipeline.llm_chunker import chunk_with_llm
```

### `ai_worker/llm/__init__.py`

```python
# processor.py 가 from ai_worker.llm import ScriptData 로 쓸 수 있도록 re-export
from ai_worker.llm.client import ScriptData, generate_script, summarize
from ai_worker.llm.logger import LLMCallTimer, log_llm_call
```

### `ai_worker/tts/__init__.py`

```python
# 기존 ai_worker/tts/__init__.py 내용 그대로 유지
# fish_client 만 추가
from ai_worker.tts.base import BaseTTS
from ai_worker.tts.fish_client import synthesize, wait_for_fish_speech
from ai_worker.tts.edge_tts import EdgeTTS
from ai_worker.tts.kokoro import KokoroTTS
from ai_worker.tts.gptsovits import GptSoVITS

TTS_ENGINES: dict[str, type[BaseTTS]] = {
    "edge-tts": EdgeTTS,
    "kokoro": KokoroTTS,
    "gpt-sovits": GptSoVITS,
}

def get_tts_engine(name: str) -> BaseTTS:
    cls = TTS_ENGINES.get(name)
    if cls is None:
        raise ValueError(f"Unknown TTS engine: {name!r}  (available: {list(TTS_ENGINES)})")
    return cls()
```

### `ai_worker/renderer/__init__.py`

```python
# 빈 파일 (또는 아래처럼 공개 API 명시)
from ai_worker.renderer.layout import render_layout_video, render_layout_video_from_scenes
from ai_worker.renderer.video import render_preview
from ai_worker.renderer.thumbnail import generate_thumbnail, get_thumbnail_path
```

---

## 작업 순서 (Claude Code 실행 순서)

1. **서브디렉토리 생성**
   ```
   ai_worker/pipeline/
   ai_worker/llm/
   ai_worker/renderer/
   ```
   (`ai_worker/tts/` 는 이미 존재하므로 생성 불필요)

2. **파일 이동** (위 이동 목록 순서대로)

3. **`__init__.py` 생성** (위 내용 그대로)

4. **import 수정** (위 변경 목록 순서대로, 파일별로 한 번에 처리)

5. **동작 확인**
   ```bash
   python -c "from ai_worker.pipeline.content_processor import process_content; print('OK')"
   python -c "from ai_worker.llm.client import generate_script; print('OK')"
   python -c "from ai_worker.tts.fish_client import synthesize; print('OK')"
   python -c "from ai_worker.renderer.layout import render_layout_video_from_scenes; print('OK')"
   python -m ai_worker.main --help 2>&1 | head -5
   ```

---

## 변경하지 않는 것

- Docker 구성 (`docker-compose.yml`, `docker-compose.galaxybook.yml`) — 컨테이너는 동일
- `config/settings.py` — 상수 경로 불변
- `db/`, `uploaders/`, `analytics/` — ai_worker 외부 모듈 불변
- `ai_worker/main.py` 의 전체 동작 로직 — 루트에 유지, import 경로만 수정
- `ai_worker/processor.py` 의 전체 동작 로직 — 루트에 유지, import 경로만 수정
- `ai_worker/gpu_manager.py` — 루트에 유지

---

## 각 서브디렉토리 책임 요약

| 디렉토리 | 책임 | 핵심 파일 |
|---|---|---|
| `pipeline/` | 게시글 → 대본 → 씬 배분 5단계 파이프라인 | `content_processor.py` |
| `llm/` | Ollama API 호출, 대본 파싱, 호출 이력 DB 저장 | `client.py`, `logger.py` |
| `tts/` | Fish Speech HTTP 클라이언트 + 레거시 TTS 엔진 | `fish_client.py` |
| `renderer/` | PIL 프레임 합성, FFmpeg 인코딩, 자막, 썸네일 | `layout.py`, `video.py` |
