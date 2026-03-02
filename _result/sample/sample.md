# 000. 샘플_작업_템플릿

## 1. 작업 배경 및 목적 (Context & Objective)
- **이슈 링크 / 요구사항:** (해결하려는 버그나 새로운 기능의 요구사항을 간략히 기술합니다.)
- **작업 목적:** (왜 이 작업이 필요했는지, 근본적인 원인(Root Cause)은 무엇이었는지 명시합니다.)
- 예: LLM 요약 모듈(`qwen2.5:14b`) 실행 시 간헐적으로 발생하는 OOM(Out of Memory) 문제 해결 및 처리 속도 향상.

## 2. 핵심 작업 결과 (Core Achievements)
- (본 작업을 통해 달성한 가장 중요한 결과물 1~3가지를 요약합니다.)
- 예: GPU 컨텍스트 매니저 적용으로 VRAM 누수 완벽 차단.
- 예: 텍스트 요약 실패 시 동작하는 2차 Fallback 파이프라인 구축 완료.

## 3. 상세 수정 내용 (Detailed Modifications)
- (변경, 추가, 삭제된 파일 목록과 핵심 로직의 변화를 구체적으로 설명합니다.)
- `ai_worker/processor.py`:
    - `managed_inference` 컨텍스트 매니저를 LLM과 미디어 렌더링 단계에 엄격하게 분리 적용.
    - VRAM 해제를 위한 `torch.cuda.empty_cache()` 및 `gc.collect()` 호출 위치 재조정.
- `db/models.py`:
    - `ScriptData` 모델에 `fallback_used` (Boolean) 컬럼 추가.

## 4. 하드 제약 및 시스템 영향도 (Constraints & System Impact)
- **VRAM 제약 (18GB 이하):** 준수됨 (LLM 14GB ↔ Fish Speech+LTX v2 전환 시 메모리 완전 해제 확인).
- **FFmpeg 코덱:** 프리뷰 렌더링 시 `h264_nvenc` 강제 적용 유지 확인.
- **DB 마이그레이션 필요 여부:** [O/X] (예: `alembic revision --autogenerate -m "add fallback_used"` 실행 필요)
- **환경 변수 (.env) 변경:** [O/X] (예: `LLM_RETRY_LIMIT` 변수 추가)
- **의존성 (requirements.txt) 변경:** [O/X] (새로 추가되거나 업데이트된 패키지 명시)

## 5. 엣지 케이스 및 예외 처리 (Edge Cases & Fallbacks)
- (정상 경로 외에 발생할 수 있는 예외 상황에 대해 어떻게 방어 로직을 짰는지 기록합니다.)
- 예: LLM이 JSON 형식이 아닌 일반 텍스트를 반환할 경우, 정규식으로 JSON 블록만 추출하도록 파싱 로직 강화.
- 예: 크롤러 접근 차단 시도 시, 3회 재시도(`base.py` retry 로직) 후 상태를 `FAILED`로 변경하고 알림 전송.

## 6. 테스트 및 검증 (Test & Validation)
- **테스트 결과물 저장 위치:**
    - 렌더링 비디오: `_result/media/output/preview_001.mp4`
    - 실행 로그: `_result/logs/llm_test_241026.log`
- **수동 재현/테스트 스텝:**
    1. `docker compose up ai_worker -d` 실행.
    2. `python scripts/insert_test_post.py` 로 테스트용 DB 레코드 삽입.
    3. 대시보드(수신함)에서 해당 게시글 상태가 `COLLECTED` → `PROCESSING` → `PREVIEW_RENDERED`로 정상 전이되는지 확인.
    4. `nvtop`을 통해 1막(LLM)과 2막(미디어) 사이 VRAM이 정상적으로 반환되는지 모니터링.

## 7. 알려진 문제 및 향후 과제 (Known Issues & TODOs)
- (현재 해결하지 못했거나, 임시로 타협한 부분, 다음 작업에서 개선해야 할 사항을 기록합니다.)
- 예: 현재 Fish Speech 추론 속도가 예상보다 느림. 향후 `tts_worker.py`의 배치 처리 최적화 필요.
- 예: 새 DB 컬럼 추가로 인해 대시보드 UI 일부 수정 필요 (프론트엔드 작업 대기 중).

## 8. 추천 커밋 메시지 (한글로 작성)
```text
feat: LLM 요약 VRAM OOM 해결 및 예외 처리 강화

- ai_worker/processor.py에 2막 구조 VRAM 관리(managed_inference) 엄격 적용
- JSON 파싱 실패 시 fallback 정규식 로직 추가
- db/models.py에 ScriptData fallback_used 컬럼 추가 (마이그레이션 필요)
- 연관 이슈: #42, #45