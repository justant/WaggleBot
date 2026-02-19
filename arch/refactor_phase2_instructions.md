# WaggleBot 구조 개선 Phase 2 — Task 6~12

> **전제**: Phase 1 (Task 1~5) 완료 후 진행
> **목적**: 확장성·유지보수성·안정성 강화
> **필독**: `CLAUDE.md` 코딩 규칙 준수

---

## 목차

| Task | 영역 | 심각도 | 작업량 |
|:---:|---|:---:|:---:|
| 6 | Content.get_script() 순환 import 제거 | 🔴 런타임 위험 | 소 |
| 7 | settings.py 도메인별 분리 | 🟡 비대화 | 중 |
| 8 | analytics 모듈 — Ollama 직접 호출 제거 | 🟡 일관성 | 소 |
| 9 | plugin_manager.py 단순화 | 🟡 과설계 | 소 |
| 10 | arch/ 문서 정리 (죽은 스펙 정리) | 🟡 혼란 유발 | 소 |
| 11 | Uploader 확장성 개선 | 🟢 향후 대비 | 중 |
| 12 | 에러 처리 일관성 확보 | 🟢 안정성 | 중 |

---

## Task 6: Content.get_script() 순환 import 제거

### 문제

`db/models.py`의 `Content.get_script()` 메서드가 **함수 내부에서 ai_worker를 import**한다:

```python
# db/models.py — Content 클래스 안
def get_script(self) -> "ScriptData | None":
    ...
    from ai_worker.llm import ScriptData   # ← 순환 import 위험
    ...
```

DB 모델(하위 레이어)이 AI 워커(상위 레이어)를 참조하는 **역방향 의존**이다.
현재는 lazy import라 당장 에러는 안 나지만:
- `ai_worker/` 구조 변경 시(예: `ai_worker_restructure.md` 계획) 경로가 바뀌면 즉시 깨짐
- 모듈 간 의존성 그래프가 순환 → 테스트·리팩터링 난이도 상승
- `db/models.py`를 수정 없이 단독 테스트 불가능

### 해결

`ScriptData`를 `db/` 또는 공유 레이어로 이동한다.

#### 6-1. `db/models.py`에 `ScriptData` 정의 이동

현재 `ai_worker/llm.py`에 있는 `ScriptData` dataclass를 `db/models.py`로 이동하라.

**`db/models.py` 하단에 추가:**

```python
import json as _json
from dataclasses import dataclass, field


@dataclass
class ScriptData:
    """구조화된 쇼츠 대본 데이터.

    ai_worker/llm.py에서 생성하고 Content.summary_text에 JSON으로 저장.
    """
    hook: str
    body: list[str]
    closer: str
    title_suggestion: str = ""
    tags: list[str] = field(default_factory=list)
    mood: str = "funny"

    def to_json(self) -> str:
        return _json.dumps(
            {
                "hook": self.hook,
                "body": self.body,
                "closer": self.closer,
                "title_suggestion": self.title_suggestion,
                "tags": self.tags,
                "mood": self.mood,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "ScriptData":
        data = _json.loads(raw)
        return cls(
            hook=data["hook"],
            body=data["body"],
            closer=data["closer"],
            title_suggestion=data.get("title_suggestion", ""),
            tags=data.get("tags", []),
            mood=data.get("mood", "funny"),
        )
```

#### 6-2. Content.get_script()에서 lazy import 제거

```python
# Before
def get_script(self) -> "ScriptData | None":
    from ai_worker.llm import ScriptData
    ...

# After
def get_script(self) -> "ScriptData | None":
    # ScriptData가 같은 파일에 있으므로 import 불필요
    ...
```

#### 6-3. ai_worker/llm.py에서 ScriptData를 re-export

기존에 `from ai_worker.llm import ScriptData`로 사용하는 코드가 많으므로,
`ai_worker/llm.py`에서 호환성을 유지하라:

```python
# ai_worker/llm.py 상단
from db.models import ScriptData  # re-export (기존 import 호환)
```

원본 ScriptData 정의 코드는 `ai_worker/llm.py`에서 **삭제**하라.

#### 6-4. 참조 검증

```bash
grep -rn "from ai_worker.llm import ScriptData\|from ai_worker.llm.client import ScriptData" --include="*.py" .
```

결과에 나오는 파일들이 정상 동작하는지 확인. `db/models.py`에서 직접 가져오도록 점진 교체 가능하지만, re-export로 당장은 호환된다.

---

## Task 7: settings.py 도메인별 분리

### 문제

Task 3에서 크롤러 섹션을 제거해도 `config/settings.py`는 여전히 **200줄 이상**이며, 전혀 관련 없는 도메인이 혼재한다:

- 크롤러 공통 설정 (USER_AGENTS, REQUEST_HEADERS)
- AI Worker 설정 (OLLAMA_HOST, GPU 관련)
- TTS 설정 (Fish Speech, VOICE_PRESETS, EMOTION_TAGS)
- 모니터링 설정 (GPU_TEMP, DISK_USAGE)
- 레이아웃 제약 (layout.json 로드)
- 플랫폼 인증 (PLATFORM_CREDENTIAL_FIELDS)
- 이메일/슬랙 알림

한 설정을 수정하려면 전체 파일을 읽어야 하고, 충돌 위험이 높다.

### 해결

`config/settings.py`를 메인 허브로 두되, 도메인별 서브모듈로 분리한다.

#### 7-1. 파일 구조

```
config/
├── __init__.py
├── settings.py          # 공통 설정 (DATABASE_URL 등) + 각 서브모듈 re-export
├── crawler.py           # 크롤러 공통 설정 (USER_AGENTS, REQUEST_HEADERS 등)
├── ai_worker.py         # AI Worker + TTS + 레이아웃 제약
├── monitoring.py        # 모니터링 임계값 + 알림 설정
├── pipeline.json        # (기존 유지)
├── layout.json          # (기존 유지)
└── feedback_config.json # (런타임 생성)
```

#### 7-2. `config/crawler.py` 생성

`settings.py`에서 아래 항목을 이동:

```python
"""크롤러 공통 설정."""
import os

USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ...",
    # ... 기존 목록 그대로
]

REQUEST_HEADERS: dict[str, str] = {
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "15"))
ENABLED_CRAWLERS: list[str] = os.getenv("ENABLED_CRAWLERS", "nate_pann").split(",")
```

#### 7-3. `config/monitoring.py` 생성

`settings.py`에서 아래 항목을 이동:

```python
"""모니터링 및 알림 설정."""
import os

MONITORING_ENABLED = os.getenv("MONITORING_ENABLED", "true").lower() == "true"
HEALTH_CHECK_INTERVAL = int(os.getenv("HEALTH_CHECK_INTERVAL", "300"))

# 임계값
GPU_TEMP_WARNING = int(os.getenv("GPU_TEMP_WARNING", "75"))
GPU_TEMP_CRITICAL = int(os.getenv("GPU_TEMP_CRITICAL", "80"))
DISK_USAGE_WARNING = int(os.getenv("DISK_USAGE_WARNING", "80"))
DISK_USAGE_CRITICAL = int(os.getenv("DISK_USAGE_CRITICAL", "90"))
MEMORY_USAGE_WARNING = int(os.getenv("MEMORY_USAGE_WARNING", "85"))
MEMORY_USAGE_CRITICAL = int(os.getenv("MEMORY_USAGE_CRITICAL", "95"))

# 이메일 알림
EMAIL_ALERTS_ENABLED = os.getenv("EMAIL_ALERTS_ENABLED", "false").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", "").split(",") if os.getenv("ALERT_EMAIL_TO") else []

# 슬랙 알림
SLACK_ALERTS_ENABLED = os.getenv("SLACK_ALERTS_ENABLED", "false").lower() == "true"
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
```

#### 7-4. settings.py를 허브로 유지 (호환성)

기존에 `from config.settings import USER_AGENTS`로 import하는 코드가 많으므로,
`settings.py`에서 re-export 하라:

```python
# config/settings.py 상단
# --- re-export (기존 import 경로 호환) ---
from config.crawler import (
    USER_AGENTS, REQUEST_HEADERS, REQUEST_TIMEOUT, ENABLED_CRAWLERS,
)
from config.monitoring import (
    MONITORING_ENABLED, HEALTH_CHECK_INTERVAL,
    GPU_TEMP_WARNING, GPU_TEMP_CRITICAL,
    # ... 나머지
)
```

이렇게 하면 기존 코드는 수정 없이 동작하면서, 새 코드는 `from config.crawler import USER_AGENTS`처럼 정확한 경로로 import 가능.

#### 7-5. 점진 마이그레이션

**이번 Task에서는 파일 분리 + re-export만 수행.**
각 모듈의 import 경로를 정확한 서브모듈로 바꾸는 것은 향후 점진적으로 진행.

---

## Task 8: analytics 모듈 — Ollama 직접 호출 제거

### 문제

`analytics/feedback.py`의 `generate_structured_insights()`가 **requests로 Ollama API를 직접 호출**한다:

```python
# analytics/feedback.py
resp = requests.post(
    f"{get_ollama_host()}/api/generate",
    json={"model": model, "prompt": prompt, ...},
    timeout=120,
)
```

프로젝트 전체에서 Ollama 호출은 `ai_worker/llm.py`가 담당하는데, `analytics/`가 독자적으로 HTTP를 쏜다.
이는:
- Ollama 호출 방식 변경 시 2곳을 동시 수정해야 함
- LLM 호출 로깅(`llm_logger.py`)을 우회
- GPU 매니저(`gpu_manager.py`)를 우회 → VRAM 충돌 가능

### 해결

#### 8-1. `ai_worker/llm.py`에 범용 LLM 호출 함수 추가

현재 `generate_script()`는 대본 특화 함수이다. 범용 프롬프트를 보내는 함수를 추가하라:

```python
# ai_worker/llm.py에 추가

def call_ollama_raw(
    prompt: str,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.5,
) -> str:
    """범용 Ollama API 호출. JSON 파싱 없이 원시 응답 반환.

    Args:
        prompt: 프롬프트 전체 텍스트
        model: Ollama 모델명 (None이면 기본값)
        max_tokens: 최대 토큰 수
        temperature: 샘플링 온도

    Returns:
        LLM 원시 응답 텍스트
    """
    import requests as _requests
    _model = model or OLLAMA_MODEL

    resp = _requests.post(
        f"{get_ollama_host()}/api/generate",
        json={
            "model": _model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": temperature},
        },
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()
```

#### 8-2. `analytics/feedback.py` 수정

직접 HTTP 호출을 `call_ollama_raw()`로 교체하라:

```python
# Before
resp = requests.post(
    f"{get_ollama_host()}/api/generate",
    json={...},
    timeout=120,
)
resp.raise_for_status()
raw = resp.json().get("response", "").strip()

# After
from ai_worker.llm import call_ollama_raw

raw = call_ollama_raw(
    prompt=prompt,
    model=model,
    max_tokens=512,
    temperature=0.5,
)
```

#### 8-3. feedback.py에서 불필요한 import 제거

```python
# 삭제
import requests
from config.settings import get_ollama_host, OLLAMA_MODEL
```

`OLLAMA_MODEL`은 `call_ollama_raw` 내부에서 처리하므로 feedback.py에서 불필요.

---

## Task 9: plugin_manager.py 단순화

### 문제

`plugin_manager.py`는 267줄이며 과설계된 부분이 있다:

1. **`auto_discover()`** (50줄): 모든 크롤러가 이미 `@CrawlerRegistry.register` 데코레이터를 사용하므로, "미등록 크롤러 발견" 경고를 출력하는 것이 유일한 역할. 실질적 가치 없음.
2. **`unregister()`**: 사용처 없음 (테스트에서도 안 씀)
3. **`clear()`**: "테스트용"이라 명시했으나 테스트 파일 없음
4. **모듈 레벨 편의 함수 3개** (`get_crawler()`, `list_crawlers()`, `auto_discover()`): `CrawlerRegistry` 클래스 메서드와 완전 중복

### 해결

#### 9-1. 삭제할 메서드/함수

- `CrawlerRegistry.auto_discover()` — 삭제
- `CrawlerRegistry.unregister()` — 삭제
- `CrawlerRegistry.clear()` — 삭제
- 모듈 레벨 `auto_discover()` 함수 — 삭제

#### 9-2. 유지할 것

- `CrawlerRegistry.register()` — 핵심 데코레이터
- `CrawlerRegistry.get_crawler()` — 인스턴스 생성
- `CrawlerRegistry.list_crawlers()` — 목록 조회
- `CrawlerRegistry.get_enabled_crawlers()` — 활성 목록
- `CrawlerRegistry.is_registered()` — 등록 확인
- 모듈 레벨 `get_crawler()`, `list_crawlers()` — 편의 함수 (사용처 있을 수 있음)

#### 9-3. 삭제 전 참조 확인

```bash
grep -rn "auto_discover\|unregister\|\.clear()" --include="*.py" .
```

`main.py`나 다른 곳에서 `auto_discover()`를 호출하고 있으면 해당 호출도 제거하라.

---

## Task 10: arch/ 문서 정리

### 문제

`arch/` 디렉토리에 과거 스펙 문서가 남아있으며, 일부는 현재 코드와 불일치한다:

| 파일 | 상태 |
|---|---|
| `arch/1. dev_spec.md` | Phase 3 이전 스펙 — 현재 코드와 다수 불일치 |
| `arch/2. next_spec_by_claude.md` | Phase 3 로드맵 — Phase 3A/3B/3C 완료 표시됨 |
| `arch/3. renderer_from_figma.md` | "Merged to Main" 명시 — 완료된 문서 |
| `arch/4. llm_optimization.md` | 5-Phase 파이프라인 계획 — `use_content_processor: false`로 비활성 |
| `arch/5. tts_inhancement.md` | Fish Speech 교체 계획 — 미완성 (아직 edge-tts 사용 중) |
| `arch/ai_worker_restructure.md` | 디렉토리 재편 계획 — 미실행 |

### 해결

#### 10-1. 완료된 스펙을 `arch/done/`으로 이동

```bash
mkdir -p arch/done
mv "arch/1. dev_spec.md" arch/done/
mv "arch/2. next_spec_by_claude.md" arch/done/
mv "arch/3. renderer_from_figma.md" arch/done/
```

#### 10-2. 미실행 스펙에 상태 표시 추가

아래 파일의 **맨 첫 줄**에 상태 배너를 추가하라:

**`arch/4. llm_optimization.md`:**
```markdown
> ⚠️ **문서 상태**: 계획 수립 완료, 미실행. `config/pipeline.json`의 `use_content_processor`가 `false`인 동안은 비활성.
```

**`arch/5. tts_inhancement.md`:**
```markdown
> ⚠️ **문서 상태**: 계획 수립 완료, 미실행. 현재 edge-tts 사용 중. Fish Speech 도입 시 이 문서 참조.
```

**`arch/ai_worker_restructure.md`:**
```markdown
> ⚠️ **문서 상태**: 계획 수립 완료, 미실행. ai_worker/ 파일이 증가하면 이 계획에 따라 재편.
```

#### 10-3. CLAUDE.md 업데이트

`CLAUDE.md`의 "Phase 3 개발 현황" 섹션 아래에 추가:

```markdown
### arch/ 문서 가이드
- `arch/done/` — 완료된 과거 스펙 (참고용)
- `arch/4. llm_optimization.md` — 5-Phase 파이프라인 (미실행, use_content_processor=false)
- `arch/5. tts_inhancement.md` — Fish Speech TTS 교체 (미실행)
- `arch/ai_worker_restructure.md` — ai_worker 디렉토리 재편 (미실행)
```

---

## Task 11: Uploader 확장성 개선

### 문제

`uploaders/base.py`의 `BaseUploader`를 상속하면 새 플랫폼을 추가할 수 있다고 문서에 명시되어 있지만, 실제로 **업로더 자동 등록 메커니즘이 없다**.

현재 `uploaders/uploader.py`에서 YouTube 업로더를 **하드코딩**으로 호출하고 있을 가능성이 높다.
크롤러는 `CrawlerRegistry`로 플러그인 등록이 되는데, 업로더에는 이 구조가 없다.

### 해결

#### 11-1. UploaderRegistry 패턴 도입

`uploaders/base.py`에 레지스트리를 추가하라:

```python
# uploaders/base.py

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Type

class BaseUploader(ABC):
    platform: str = ""

    @abstractmethod
    def validate_credentials(self) -> bool:
        """인증 정보 유효성 검증."""

    @abstractmethod
    def upload(self, video_path: Path, metadata: dict) -> dict:
        """영상 업로드. 반환: {"url": ..., "video_id": ..., "platform": ...}"""


class UploaderRegistry:
    """업로더 플러그인 레지스트리."""

    _uploaders: Dict[str, Type[BaseUploader]] = {}

    @classmethod
    def register(cls, platform: str):
        """업로더 등록 데코레이터."""
        def decorator(uploader_class: Type[BaseUploader]):
            cls._uploaders[platform] = uploader_class
            return uploader_class
        return decorator

    @classmethod
    def get_uploader(cls, platform: str) -> BaseUploader:
        if platform not in cls._uploaders:
            available = ", ".join(cls._uploaders.keys())
            raise ValueError(
                f"Unknown platform: '{platform}'. Available: {available}"
            )
        return cls._uploaders[platform]()

    @classmethod
    def list_platforms(cls) -> list[str]:
        return list(cls._uploaders.keys())
```

#### 11-2. YouTube 업로더에 데코레이터 적용

```python
# uploaders/youtube.py
from uploaders.base import BaseUploader, UploaderRegistry

@UploaderRegistry.register("youtube")
class YouTubeUploader(BaseUploader):
    platform = "youtube"
    ...
```

#### 11-3. `uploaders/uploader.py` 수정

`upload_post()` 함수에서 하드코딩 대신 레지스트리 사용:

```python
# Before (추정)
from uploaders.youtube import YouTubeUploader
uploader = YouTubeUploader()

# After
from uploaders.base import UploaderRegistry

def upload_post(post, content, platforms: list[str]) -> dict:
    results = {}
    for platform in platforms:
        uploader = UploaderRegistry.get_uploader(platform)
        if uploader.validate_credentials():
            results[platform] = uploader.upload(video_path, metadata)
    return results
```

#### 11-4. ADDING_UPLOADER.md 생성

`uploaders/ADDING_UPLOADER.md`를 `crawlers/ADDING_CRAWLER.md`와 동일한 패턴으로 작성하라:

```markdown
# 업로더 추가 가이드

## 구현 단계

### 1. 업로더 파일 생성

`uploaders/tiktok.py` 파일을 생성합니다.

\```python
from pathlib import Path
from uploaders.base import BaseUploader, UploaderRegistry

@UploaderRegistry.register("tiktok")
class TikTokUploader(BaseUploader):
    platform = "tiktok"

    def validate_credentials(self) -> bool:
        ...

    def upload(self, video_path: Path, metadata: dict) -> dict:
        ...
\```

### 2. pipeline.json 활성화

\```json
{"upload_platforms": "[\"youtube\", \"tiktok\"]"}
\```
```

---

## Task 12: 에러 처리 일관성 확보

### 문제

프로젝트 전체에서 에러 처리 패턴이 일관되지 않는다:

#### (A) 크롤러: except 범위가 너무 넓음

```python
# 현재 — 모든 예외를 잡아서 로그만 찍고 continue
except Exception:
    log.exception("Failed to parse %s", item["url"])
    continue
```

네트워크 에러, 파싱 에러, DB 에러가 전부 같은 방식으로 처리됨.

#### (B) analytics/feedback.py: HTTP 에러와 파싱 에러 미분리

```python
resp = requests.post(...)
resp.raise_for_status()
raw = resp.json().get("response", "").strip()
# JSON 파싱 실패 시 어디서 잡히는지 불명확
```

#### (C) 재시도 로직 부재

크롤러의 HTTP 요청, Ollama 호출 등에 재시도(retry) 로직이 없다.
일시적 네트워크 에러에도 해당 게시글을 영구 스킵.

### 해결

#### 12-1. BaseCrawler에 재시도 데코레이터 추가

```python
# crawlers/base.py에 추가

import time
from functools import wraps
from typing import TypeVar, Callable

T = TypeVar("T")


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple = (requests.RequestException,),
) -> Callable:
    """HTTP 요청 재시도 데코레이터.

    Args:
        max_attempts: 최대 시도 횟수
        delay: 첫 대기 시간 (초)
        backoff: 대기 시간 배수
        exceptions: 재시도할 예외 타입
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            current_delay = delay
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        log.warning(
                            "%s 재시도 %d/%d (%.1f초 후): %s",
                            func.__name__, attempt, max_attempts,
                            current_delay, e,
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
            raise last_exception  # type: ignore[misc]
        return wrapper
    return decorator
```

#### 12-2. `_get()`과 `_post()`에 재시도 적용

```python
class BaseCrawler(ABC):
    ...

    @retry(max_attempts=3, delay=1.0)
    def _get(self, url: str, **kwargs) -> requests.Response:
        self._rotate_ua()
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        resp = self._session.get(url, **kwargs)
        resp.raise_for_status()
        return resp

    @retry(max_attempts=3, delay=1.0)
    def _post(self, url: str, **kwargs) -> requests.Response:
        self._rotate_ua()
        kwargs.setdefault("timeout", REQUEST_TIMEOUT)
        resp = self._session.post(url, **kwargs)
        resp.raise_for_status()
        return resp
```

#### 12-3. 크롤러의 except 세분화 (선택)

각 크롤러의 `parse_post()`에서 잡는 예외를 세분화할 수 있다.
단, 현재 `BaseCrawler.run()`이 개별 게시글 실패를 잡아서 계속 진행하는 구조가 이미 안전하므로, **이 작업은 선택사항**이다.

변경한다면:

```python
# Before
except Exception:
    log.exception(...)

# After
except requests.RequestException:
    log.warning("네트워크 에러: %s", item["url"])
    continue
except (AttributeError, ValueError) as e:
    log.warning("파싱 에러: %s — %s", item["url"], e)
    continue
except Exception:
    log.exception("예기치 못한 에러: %s", item["url"])
    continue
```

---

## Task 순서 및 의존관계

```
Task 6 (순환 import) ──── 독립, 즉시 가능
Task 7 (settings 분리) ── 독립, 즉시 가능
Task 8 (analytics)  ───── Task 6 이후 (ScriptData 경로 변경 영향)
Task 9 (plugin_manager) ─ Task 1 완료 후 (Dead code 제거 후)
Task 10 (arch/ 정리) ──── 독립, 즉시 가능
Task 11 (Uploader) ────── 독립, 즉시 가능
Task 12 (에러 처리) ───── Task 2 완료 후 (BaseCrawler 수정 후)
```

**권장 실행 순서:**
```
Task 6 → Task 8 → Task 7 → Task 9 → Task 10 → Task 11 → Task 12
```

---

## 검증 (전체)

```bash
# 순환 import 없는지 확인
python -c "from db.models import ScriptData; print('ScriptData in db.models OK')"
python -c "from ai_worker.llm import ScriptData; print('ScriptData re-export OK')"

# settings 서브모듈
python -c "from config.crawler import USER_AGENTS; print('crawler config OK')"
python -c "from config.monitoring import MONITORING_ENABLED; print('monitoring config OK')"
python -c "from config.settings import USER_AGENTS; print('re-export OK')"

# analytics
python -c "from analytics.feedback import generate_structured_insights; print('feedback OK')"

# plugin_manager
python -c "from crawlers.plugin_manager import CrawlerRegistry; print('registry OK')"
python -c "
from crawlers.plugin_manager import CrawlerRegistry
assert not hasattr(CrawlerRegistry, 'auto_discover'), 'auto_discover 아직 남아있음'
print('plugin_manager 단순화 OK')
"

# uploader registry
python -c "from uploaders.base import UploaderRegistry; print('UploaderRegistry OK')"

# arch/ 구조
test -d arch/done && echo "arch/done 존재 OK" || echo "FAIL"
```

---

## 절대 수정 금지 (CLAUDE.md 준수)

| 대상 | 이유 |
|---|---|
| `db/models.py`의 기존 모델 컬럼 | 스키마 변경 시 마이그레이션 필요 (ScriptData 클래스 추가는 컬럼 변경이 아니므로 허용) |
| `.env` | 시크릿 포함 |
| `docker-compose.yml` / `docker-compose.galaxybook.yml` | GPU 매핑 민감 |
| `requirements.txt` | 의존성 충돌 위험 |

---

## 변경 파일 요약

| 파일 | Task | 작업 |
|---|:---:|---|
| `db/models.py` | 6 | ✏️ ScriptData 클래스 추가 (컬럼 변경 아님) |
| `ai_worker/llm.py` | 6,8 | ✏️ ScriptData 제거 + re-export, `call_ollama_raw()` 추가 |
| `analytics/feedback.py` | 8 | ✏️ 직접 HTTP 호출 → `call_ollama_raw()` 교체 |
| `config/settings.py` | 7 | ✏️ 도메인별 코드를 서브모듈로 이동 + re-export |
| `config/crawler.py` | 7 | 🆕 크롤러 공통 설정 |
| `config/monitoring.py` | 7 | 🆕 모니터링 설정 |
| `crawlers/plugin_manager.py` | 9 | ✏️ 불필요 메서드 삭제 |
| `arch/done/` | 10 | 🆕 디렉토리 생성 + 완료 문서 이동 |
| `arch/4,5,ai_worker_restructure.md` | 10 | ✏️ 상태 배너 추가 |
| `CLAUDE.md` | 10 | ✏️ arch/ 가이드 추가 |
| `uploaders/base.py` | 11 | ✏️ UploaderRegistry 추가 |
| `uploaders/youtube.py` | 11 | ✏️ `@UploaderRegistry.register` 적용 |
| `uploaders/uploader.py` | 11 | ✏️ 레지스트리 기반으로 교체 |
| `uploaders/ADDING_UPLOADER.md` | 11 | 🆕 업로더 추가 가이드 |
| `crawlers/base.py` | 12 | ✏️ `retry()` 데코레이터 추가 |
