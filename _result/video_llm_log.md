# 비디오 LLM 이력 로깅 추가

## 1. 작업 배경 및 목적 (Context & Objective)
- **요구사항:** LLM 이력 탭에서 비디오 프롬프트 생성(Phase 6)에 대한 LLM 입출력이 기록되지 않아 디버깅/튜닝이 불가능했음.
- **작업 목적:** VideoPromptEngine의 모든 LLM 호출(T2V, I2V, Simplify)을 llm_logs 테이블에 기록하고, 대시보드에서 대본 LLM과 비디오 LLM을 분리해서 조회할 수 있도록 개선.

## 2. 핵심 작업 결과 (Core Achievements)
- VideoPromptEngine의 3종 LLM 호출(T2V, I2V, Simplify)에 `log_llm_call()` 로깅 추가.
- 대시보드 LLM 이력 탭에 서브뷰 라디오 버튼 추가 (📝 대본 LLM / 🎬 비디오 LLM).
- 비디오 LLM 뷰에서 씬 번호, 비디오 모드, 프롬프트 유형별 필터링 지원.

## 3. 상세 수정 내용 (Detailed Modifications)

### `ai_worker/video/prompt_engine.py`
- `LLMCallTimer`, `log_llm_call` import 추가.
- `generate_prompt()`: `post_id`, `scene_index` 파라미터 추가. `LLMCallTimer`로 시간 측정 + `log_llm_call()` 호출 (call_type: `video_prompt_t2v` / `video_prompt_i2v`). `parsed_result`에 `{"scene_index": N, "video_mode": "t2v"|"i2v"}` 저장.
- `simplify_prompt()`: `post_id`, `scene_index` 파라미터 추가. 동일 패턴으로 로깅 (call_type: `video_prompt_simplify`).
- `generate_batch()`: `post_id` 파라미터 추가. 내부 호출 시 `post_id`, `scene_index` 전달.

### `ai_worker/pipeline/content_processor.py`
- `prompt_engine.generate_batch()` 호출 시 `post_id=getattr(post, "id", None)` 전달.

### `ai_worker/processor.py`
- `prompt_engine.generate_batch()` 호출 시 `post_id=post_id` 전달.

### `dashboard/tabs/llm_log.py`
- 서브뷰 라디오 버튼 추가: "📝 대본 LLM (TTS·씬)" / "🎬 비디오 LLM (씬별 프롬프트)".
- 비디오 뷰 전용 필터: T2V / I2V / Simplify 유형 선택, 성공여부, 기간, Post ID.
- 비디오 로그 렌더링: 씬 번호, 비디오 모드, 생성된 프롬프트 프리뷰, 시스템 프롬프트 복사 블록.
- 통계 카드가 서브뷰 카테고리별로 분리 집계됨.

### `db/models.py`
- `LLMLog` docstring에 새 call_type 3종 문서화.

## 4. 하드 제약 및 시스템 영향도 (Constraints & System Impact)
- **VRAM 제약:** 영향 없음 (로깅은 DB I/O만 수행).
- **DB 마이그레이션 필요 여부:** X (기존 `call_type` String(32) 컬럼에 새 값만 추가, 스키마 변경 없음).
- **환경 변수 (.env) 변경:** X
- **의존성 변경:** X

## 5. 엣지 케이스 및 예외 처리 (Edge Cases & Fallbacks)
- `log_llm_call()`은 기존 `try-except` 래핑으로 DB 오류 시 파이프라인 중단 없음.
- `post_id`가 None인 경우 (테스트 등) 정상 동작 — LLMLog.post_id는 nullable.
- `model_name`은 빈 문자열로 저장 — `call_ollama_raw` 내부에서 기본 모델 사용하므로 외부에서 모델명 확인 불가. 향후 개선 가능.

## 6. 테스트 및 검증 (Test & Validation)
- **수동 테스트 방법:**
    1. `docker compose up ai_worker dashboard -d`
    2. 대시보드 → 수신함에서 콘텐츠 승인 → PROCESSING 대기
    3. Phase 6 완료 후 대시보드 → LLM 이력 탭 → "🎬 비디오 LLM" 라디오 선택
    4. 씬별 T2V/I2V/Simplify 로그가 표시되는지 확인
    5. Post ID 필터로 특정 콘텐츠의 비디오 프롬프트만 조회 가능한지 확인

## 7. 알려진 문제 및 향후 과제 (Known Issues & TODOs)
- `model_name`이 빈 문자열로 저장됨. `call_ollama_raw`가 내부적으로 `OLLAMA_MODEL`을 사용하지만 반환하지 않아 외부에서 확인 불가. 향후 `call_ollama_raw` 반환값에 모델명 포함 검토.

## 8. 추천 커밋 메시지 (한글로 작성)
```text
feat: 비디오 프롬프트 LLM 호출 로깅 + 대시보드 서브뷰 분리

- VideoPromptEngine에 LLM 호출 로깅 추가 (T2V/I2V/Simplify)
- LLM 이력 탭에 대본 LLM / 비디오 LLM 서브뷰 라디오 버튼 추가
- 씬별 비디오 프롬프트 입출력을 대시보드에서 조회 가능
```
