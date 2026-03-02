-- 005: Content 테이블에 pipeline_state JSON 컬럼 추가
-- 비디오 생성 체크포인트 (씬별 진행 상태) 저장용
ALTER TABLE contents ADD COLUMN IF NOT EXISTS pipeline_state JSON DEFAULT NULL;
