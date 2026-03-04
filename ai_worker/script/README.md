# ai_worker/script — LLM 대본 생성 모듈

Ollama LLM HTTP 클라이언트, JSON 파싱/복구, 텍스트 정규화, 청킹, 로깅을 담당.
기존 `ai_worker/llm/` 패키지를 대체하여 대본 관련 LLM 기능을 통합한다.

---

## 파일 구조

```
ai_worker/script/
├── __init__.py       # 퍼블릭 API re-export (6줄)
├── client.py         # Ollama HTTP 클라이언트 (208줄)
├── parser.py         # JSON 파싱/복구 → ScriptData (176줄)
├── normalizer.py     # 댓글 후처리 — 줄 분리/최대 글자수 (87줄)
├── chunker.py        # LLM 기반 의미 단위 청킹 (186줄)
├── logger.py         # LLM 호출 로깅/타이머 (105줄)
└── settings.yaml     # 도메인별 설정
```

---

## 퍼블릭 API

`__init__.py`에서 re-export:

```python
from ai_worker.script.client import call_ollama_raw, generate_script
from ai_worker.script.parser import parse_script_json
from ai_worker.script.normalizer import split_comment_lines, ensure_comments
from ai_worker.script.logger import LLMCallTimer, log_llm_call
```

---

## 모듈 상세

### client.py (208줄)

Ollama HTTP API 클라이언트. 모든 LLM 호출의 단일 진입점.

| 함수 | 역할 |
|------|------|
| `call_ollama_raw(prompt, system, max_tokens, temperature)` | Ollama `/api/generate` POST 호출 |
| `generate_script(post, comments, mood, feedback)` | 게시글 → LLM 대본 JSON 생성 |

**의존성:** `config/settings.py` → `OLLAMA_HOST`, `OLLAMA_MODEL`

### parser.py (176줄)

LLM 응답 JSON을 `ScriptData` 객체로 파싱. JSON 깨짐 시 regex 폴백.

| 함수 | 역할 |
|------|------|
| `parse_script_json(raw)` | JSON 파싱 → ScriptData 변환 |
| `_fix_control_chars(raw)` | 제어문자 제거 |
| `_repair_json(raw)` | 깨진 JSON 복구 (괄호 매칭, 쉼표 등) |
| `_extract_fields_regex(raw)` | regex 폴백 — hook/body/closer 추출 |

**body 블록 타입:**

| 입력 | 변환 결과 |
|------|-----------|
| `{"type": "body", "lines": [...]}` | 그대로 유지 |
| `{"type": "comment", "author": "닉네임", "lines": [...]}` | 그대로 유지 |
| `{"lines": [...]}` (type 없음) | `"type": "body"` 자동 부여 |
| `"문자열"` (레거시) | `{"type": "body", "lines": ["문자열"]}` 변환 |

### normalizer.py (87줄)

대본 후처리 — 댓글 줄 분리, 글자수 제한 적용.

| 함수/상수 | 역할 |
|-----------|------|
| `split_comment_lines(body)` | 긴 댓글을 `MAX_LINE_CHARS` 기준으로 분리 |
| `ensure_comments(body)` | 댓글 블록 최소 1개 보장 |
| `MAX_LINE_CHARS` | 줄당 최대 글자수 상수 |

### chunker.py (186줄)

LLM을 사용한 의미 단위 대본 청킹. Phase 2 실행 주체.

| 함수 | 역할 |
|------|------|
| `chunk_with_llm(text, mood)` | 텍스트 → 의미 단위 분할 (LLM 호출) |

### logger.py (105줄)

LLM 호출 로깅 및 타이머 유틸리티.

| 심볼 | 역할 |
|------|------|
| `LLMCallTimer` | 컨텍스트 매니저 — 호출 시간 측정 |
| `log_llm_call(call_type, prompt, response, duration)` | DB `LLMLog` 테이블에 호출 기록 저장 |

### settings.yaml

```yaml
llm:
  model: qwen2.5:14b
  max_tokens: 2048
  temperature: 0.7
script:
  max_line_chars: 40
  min_comments: 1
```

`get_domain_setting('script', 'llm', 'model')` 형태로 접근.

---

## 외부 사용처

| 파일 | import 대상 | 용도 |
|------|-------------|------|
| `ai_worker/pipeline/content_processor.py` | `generate_script`, `parse_script_json` | Phase 2~3 대본 생성/파싱 |
| `ai_worker/video/prompt_engine.py` | `call_ollama_raw`, `LLMCallTimer`, `log_llm_call` | Phase 6 비디오 프롬프트 LLM 호출 |
| `ai_worker/scene/director.py` | (간접) ScriptData 소비 | Phase 4 씬 배분 |
| `test/test_script_pipeline_fix.py` | `parse_script_json`, `_extract_fields_regex` | 파싱 단위 테스트 |
| `test/test_fish_speech.py` | (간접) normalizer 검증 | TTS 정규화 테스트 |
