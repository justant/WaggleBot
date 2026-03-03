# DB 동시성 에러 + ComfyUI 타임아웃 수정

## 1. 작업 배경 및 목적 (Context & Objective)
- **이슈:** 파이프라인 1-2시간 렌더링 완료 후 `pymysql.err.OperationalError: (1020, "Record has changed since last read in table 'contents'")` 에러로 작업 롤백 → 무한 재시작. 동시에 ComfyUI Distilled 모드에서 600초 타임아웃 초과.
- **근본 원인 (DB):** SQLAlchemy 세션의 identity map에 캐시된 stale 객체로 장시간 후 DB 업데이트 시도. 렌더링 중 대시보드/모니터링이 같은 레코드를 수정하여 버전 불일치 발생.
- **근본 원인 (Timeout):** `VIDEO_GEN_TIMEOUT_DISTILLED` 기본값 600초가 실제 생성 시간(517초+)에 대기열 처리까지 합치면 부족.

## 2. 핵심 작업 결과 (Core Achievements)
- `process_with_retry` 저장 직전에 `session.expire_all()` + Post re-fetch로 stale 객체 문제 해결
- `_mark_as_failed`에도 동일한 세션 갱신 로직 적용 (실패 경로도 방어)
- ComfyUI 타임아웃을 Full/Distilled 모두 1200초(20분)로 통일

## 3. 상세 수정 내용 (Detailed Modifications)
- `ai_worker/processor.py`:
    - `process_with_retry` 내 `_save_content` 호출 직전에 `session.expire_all()` + `post = session.query(Post).filter_by(id=post.id).first()` 추가 (line 150~151)
    - `_mark_as_failed` 메서드에 `session.expire_all()` + Post re-fetch 추가 (line 508~510)
- `config/settings.py`:
    - `VIDEO_GEN_TIMEOUT_DISTILLED` 기본값 `"600"` → `"1200"` (line 337)
- `ai_worker/video/comfy_client.py`:
    - `generate_t2v` 함수 시그니처 기본 timeout: `300` → `1200` (line 116)
    - `generate_i2v` 함수 시그니처 기본 timeout: `300` → `1200` (line 206)

## 4. 하드 제약 및 시스템 영향도 (Constraints & System Impact)
- **VRAM 제약:** 영향 없음 (DB/타임아웃 설정 변경만)
- **FFmpeg 코덱:** 영향 없음
- **DB 마이그레이션 필요 여부:** X
- **환경 변수 (.env) 변경:** X (기존 `VIDEO_GEN_TIMEOUT_DISTILLED` 환경변수로 오버라이드 중이면 해당 값도 1200으로 변경 필요)
- **의존성 (requirements.txt) 변경:** X

## 5. 엣지 케이스 및 예외 처리 (Edge Cases & Fallbacks)
- `render_stage` 경로는 이미 `session.expire_all()` + re-fetch가 적용되어 있었음 (line 877~878) — 추가 수정 불필요
- `_mark_as_failed`에서도 `session.expire_all()` 후 Post가 None일 경우는 retry 중 삭제된 극단적 케이스이나, 현재 프로덕션에서 Post 삭제는 없으므로 별도 가드 미적용

## 6. 테스트 및 검증 (Test & Validation)
- **수동 재현/테스트 스텝:**
    1. `docker compose up ai_worker -d` 실행
    2. 대시보드에서 게시글 APPROVED 상태로 설정하여 파이프라인 트리거
    3. 렌더링 완료 후 `PREVIEW_RENDERED` 상태로 정상 전이되는지 확인
    4. `docker compose logs --tail 100 ai_worker | grep -E "(1020|expire_all|timeout)"` 로 에러 재발 여부 확인

## 7. 알려진 문제 및 향후 과제 (Known Issues & TODOs)
- `llm_tts_stage` 경로의 `session.commit()` (line 803)도 장시간 소요 시 동일 문제 가능성 있으나, LLM+TTS 단계는 수 분 내 완료되므로 현실적 위험도 낮음
- 타임아웃 1200초도 극단적 부하 시 부족할 수 있으나, RTX 3090 기준 단일 클립 생성이 20분을 초과하면 근본적 성능 문제이므로 별도 조사 필요

## 8. 추천 커밋 메시지 (한글로 작성)
```text
fix: DB 동시성 에러(1020) 해결 + ComfyUI 타임아웃 1200초 상향

- processor.py: 장시간 렌더링 후 session.expire_all() + Post re-fetch로 stale 객체 방지
- _mark_as_failed에도 세션 갱신 로직 적용
- VIDEO_GEN_TIMEOUT_DISTILLED 기본값 600→1200초
- comfy_client.py t2v/i2v 기본 timeout 300→1200초
```
