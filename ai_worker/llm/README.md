# ai_worker/llm — LLM 클라이언트 모듈

Ollama HTTP 통신, 대본 생성, JSON 파싱, 호출 로깅을 담당하는 모듈.
WaggleBot 파이프라인에서 모든 LLM 호출의 단일 진입점 역할을 한다.

---

## 파일 구조

```
ai_worker/llm/
├── __init__.py   # 퍼블릭 API re-export
├── client.py     # Ollama HTTP 클라이언트 + 대본 생성 + JSON 파싱
├── logger.py     # LLM 호출 이력 DB 기록
└── README.md     # (이 파일)
```

---

## 퍼블릭 API

`__init__.py`에서 re-export하므로 `from ai_worker.llm import ...`로 사용 가능.

| 심볼 | 출처 | 용도 |
|------|------|------|
| `generate_script()` | client.py | 구조화 대본(ScriptData) 생성 |
| `call_ollama_raw()` | client.py | 범용 Ollama 호출 (원시 텍스트 반환) |
| `summarize()` | client.py | 하위 호환 래퍼 (→ `generate_script().to_plain_text()`) |
| `ScriptData` | db.models (re-export) | 대본 데이터 구조체 |
| `LLMCallTimer` | logger.py | 경과시간 측정 컨텍스트 매니저 |
| `log_llm_call()` | logger.py | LLM 호출 이력 DB 저장 |

---

## client.py — 상세 구조

### 의존성

```python
from config.settings import get_ollama_host, OLLAMA_MODEL
from db.models import ScriptData  # re-export 겸용
```

- **HTTP 라이브러리**: `requests` + `HTTPAdapter` + `Retry`
- **모듈 레벨 세션**: `_ollama_session` — 커넥션 풀 재사용

### 함수 목록

#### 퍼블릭

```python
def generate_script(
    title: str,
    body: str,
    comments: list[str],
    *,
    model: str | None = None,           # 기본: OLLAMA_MODEL (qwen2.5:14b)
    extra_instructions: str | None = None,  # 프롬프트 끝 추가 지시사항
    post_id: int | None = None,          # LLM 로그 연결용
    call_type: str = "generate_script",  # 로그 호출 유형
) -> ScriptData:
```
- 메인 대본 생성 API
- `_SCRIPT_PROMPT_V2` 템플릿으로 프롬프트 구성 → Ollama 호출 → JSON 파싱 → 댓글 후처리
- 본문 최대 4000자, 댓글 최대 5개 사용
- `num_predict=2048`, `temperature=0.7`
- **항상** `log_llm_call()` 기록 (finally 블록)

```python
def call_ollama_raw(
    prompt: str,
    model: str | None = None,
    max_tokens: int = 512,
    temperature: float = 0.5,
    timeout: int = 120,
) -> str:
```
- 범용 Ollama 호출 — JSON 파싱 없이 원시 응답 반환
- 비디오 프롬프트, 피드백 분석, 대시보드 분석 등에서 사용

```python
def summarize(title: str, body: str, comments: list[str], *, model: str | None = None) -> str:
```
- 하위 호환 래퍼: `generate_script()` → `script.to_plain_text()`

#### 내부 (private)

| 함수 | 역할 |
|------|------|
| `_build_ollama_session()` | 재시도 전략 포함 requests 세션 생성 (2회, 1s backoff, 500/502/503/504) |
| `_call_ollama(prompt, model, num_predict, timeout)` | Ollama `/api/generate` HTTP POST |
| `_fix_control_chars(s)` | JSON 문자열 내부 제어 문자(\n, \r, \t 등) 이스케이프 |
| `_repair_json(s)` | LLM 흔한 JSON 오류 보정 (닫힘 누락, trailing comma 등) |
| `_extract_fields_regex(raw)` | JSON 완전 실패 시 regex로 개별 필드 추출 (마지막 폴백) |
| `_parse_script_json(raw)` | 3단계 JSON 파싱 파이프라인 |
| `_split_comment_lines(text)` | 댓글 텍스트를 20자 이하 줄 단위로 분할 |
| `_ensure_comments(script, input_comments, min_comments)` | LLM 댓글 누락 시 자동 주입 (최소 3개) |

### JSON 파싱 파이프라인 (`_parse_script_json`)

```
1차: json.loads(raw)           — 직접 파싱
 ↓ 실패
2차: _repair_json(raw) + parse — 공통 LLM JSON 오류 자동 보정
 ↓ 실패
3차: _extract_fields_regex(raw) — regex로 필드별 추출 (최종 폴백)
 ↓ 실패
ValueError 발생
```

**`_repair_json` 보정 대상:**
- `"value"}}` → `"value"]}` (lines 배열 닫힘 누락)
- `]]` → `]}` (body 항목 닫힘 오류)
- trailing comma before `}` / `]`
- leading comma inside array `[, "text"]`
- missing value `": ,` → `": "",`

### 프롬프트 템플릿 (`_SCRIPT_PROMPT_V2`)

YouTube 쇼츠 대본 작성 시스템 프롬프트. 주요 규칙:

| 규칙 | 내용 |
|------|------|
| 출력 형식 | JSON — hook/body/closer/title_suggestion/tags/mood |
| 블록 타입 | body(본문) vs comment(댓글) 분리 필수 |
| 자막 분할 | lines 1개 문자열 ≤ 20자 |
| 본문 완결 | 생략/요약 절대 금지, 끝까지 작성 |
| 댓글 인용 | 최소 3개, 가짜 댓글 금지 |
| 감정 분류 | 9종 mood: humor, touching, anger, sadness, horror, info, controversy, daily, shock |
| 검열 해제 | 민감 단어 원문 유지 (픽션 기반 심리 분석용) |
| body 항목 수 | 최소 6개 ~ 최대 23개 |

### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `_MAX_LINE_CHARS` | 20 | 자막 줄 최대 글자 수 |

---

## logger.py — 상세 구조

### 의존성

```python
from db.models import LLMLog
from db.session import SessionLocal
```

### LLMCallTimer

```python
class LLMCallTimer:
    """with 블록으로 사용하는 경과시간 측정기."""
    elapsed_ms: int  # 밀리초 단위 경과시간

# 사용 예:
with LLMCallTimer() as timer:
    raw = call_ollama(prompt)
# timer.elapsed_ms → 1234
```

### log_llm_call

```python
def log_llm_call(
    *,                                    # 키워드 전용
    call_type: str,                       # 'generate_script', 'chunk', 'video_prompt_t2v' 등
    post_id: int | None,                  # 게시글 ID
    model_name: str,                      # Ollama 모델명
    prompt_text: str,                     # 전체 프롬프트
    raw_response: str,                    # 원시 응답
    parsed_result: Any | None = None,     # 파싱된 결과 (dict/list만 저장)
    strategy: str | None = None,          # 'img_heavy' | 'balanced' | 'text_heavy'
    image_count: int = 0,                 # 이미지 수
    content_length: int = 0,              # 원문 글자 수
    success: bool = True,                 # 성공 여부
    error_message: str | None = None,     # 에러 메시지
    duration_ms: int | None = None,       # 소요 시간 (ms)
) -> None:
```

**핵심 설계:**
- DB 저장 실패 시 `logger.warning`만 출력, **예외 전파하지 않음** → 파이프라인 안전
- 텍스트 필드 truncation: prompt/response → 60KB, error → 2KB
- `parsed_result`는 dict/list만 저장 (그 외 None 처리)

### 상수

| 상수 | 값 | 용도 |
|------|-----|------|
| `_MAX_TEXT_LEN` | 60,000 | TEXT 컬럼 안전 마진 (64KB 한계) |

---

## 호출 흐름도

### 대본 생성 (Phase 2 대체 / Processor)

```
generate_script(title, body, comments)
  ├─ _SCRIPT_PROMPT_V2.format(title, body[:4000], comments[:5])
  ├─ + extra_instructions (feedback_config.json에서 주입)
  ├─ LLMCallTimer 시작
  ├─ _call_ollama(prompt, model, num_predict=2048)
  │   └─ POST {get_ollama_host()}/api/generate
  ├─ log_llm_call() [finally — 성공/실패 모두]
  ├─ _parse_script_json(raw)
  │   ├─ json.loads()           [1차]
  │   ├─ _repair_json() + parse [2차]
  │   └─ _extract_fields_regex() [3차]
  ├─ _ensure_comments(script, comments)
  └─ return ScriptData
```

### 비디오 프롬프트 (Phase 6)

```
VideoPromptEngine.generate_prompt(scene)
  ├─ 프롬프트 구성 (T2V/I2V별 시스템 프롬프트)
  ├─ call_ollama_raw(prompt, max_tokens=180)
  │   └─ _call_ollama() → Ollama HTTP
  ├─ log_llm_call(call_type="video_prompt_t2v")
  └─ return video_prompt (영어)
```

### 피드백 분석 (Analytics)

```
generate_structured_insights(performance_data)
  ├─ 프롬프트 구성 (YouTube 성과 데이터)
  ├─ call_ollama_raw(prompt)
  └─ JSON 파싱 → insights dict
```

---

## 외부 사용처

### 파이프라인 코어

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `ai_worker/processor.py` | `generate_script`, `ScriptData`, `summarize` | 메인 대본 생성 |
| `ai_worker/pipeline/llm_chunker.py` | `LLMCallTimer`, `log_llm_call` | Phase 2 LLM 청킹 로깅 |
| `ai_worker/video/prompt_engine.py` | `call_ollama_raw`, `LLMCallTimer`, `log_llm_call` | Phase 6 비디오 프롬프트 |
| `ai_worker/renderer/video.py` | `ScriptData` | `ScriptData.from_json()` 역직렬화 |

### 대시보드

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `dashboard/workers/editor_tasks.py` | `generate_script` | 편집실 백그라운드 LLM 태스크 |
| `dashboard/workers/ai_analysis_tasks.py` | `call_ollama_raw` | 콘텐츠 적합성 분석 |
| `dashboard/tabs/analytics.py` | `call_ollama_raw` | 분석 탭 LLM 호출 |

### 분석

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `analytics/feedback.py` | `call_ollama_raw` | YouTube 성과 → LLM 인사이트 |

---

## 설정 (config/settings.py)

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama 서버 URL (`get_ollama_host()`로 스킴/포트 자동 보정) |
| `OLLAMA_MODEL` | `qwen2.5:14b` | 기본 모델 (RTX 3090 24GB, 8-bit ~14GB) |

### 모델 옵션 (RTX 3090 24GB)

| 모델 | 양자화 | VRAM | 비고 |
|------|--------|------|------|
| qwen2.5:7b | 4-bit | ~4.5GB | 경량, 빠름 |
| qwen2.5:7b | 8-bit | ~7.0GB | 균형 |
| qwen2.5:14b | 4-bit | ~9.0GB | 고품질 경량 |
| **qwen2.5:14b** | **8-bit** | **~14.0GB** | **기본값, 최고 품질** |

---

## 에러 처리 전략

| 계층 | 전략 | 예외 전파 |
|------|------|-----------|
| Ollama HTTP | 2회 재시도 (1s backoff, 500/502/503/504) | `TimeoutError` / `ConnectionError` |
| JSON 파싱 | 3단계 폴백 (직접 → 보정 → regex) | `ValueError` (3단계 전부 실패 시) |
| 댓글 후처리 | 누락 시 자동 주입 (최소 3개) | 전파 안 함 |
| LLM 로그 저장 | DB 실패 시 warning만 출력 | **전파 안 함** (파이프라인 안전) |

---

## DB 테이블

### LLMLog (llm_logs)

`log_llm_call()`이 기록하는 테이블. 호출 유형별 추적, 성능 분석, 디버깅에 사용.

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `post_id` | int | 게시글 FK (nullable) |
| `call_type` | str | generate_script, chunk, video_prompt_t2v 등 |
| `model_name` | str | Ollama 모델명 |
| `strategy` | str | img_heavy / balanced / text_heavy |
| `image_count` | int | 이미지 수 |
| `content_length` | int | 원문 글자 수 |
| `prompt_text` | text | 전체 프롬프트 (≤60KB) |
| `raw_response` | text | 원시 응답 (≤60KB) |
| `parsed_result` | json | 파싱된 결과 (dict/list) |
| `success` | bool | 성공 여부 |
| `error_message` | text | 에러 메시지 (≤2KB) |
| `duration_ms` | int | 소요 시간 (ms) |
| `created_at` | datetime | 기록 시각 |
