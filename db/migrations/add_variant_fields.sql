-- A/B 테스트 variant 필드 추가 마이그레이션
-- 대상 테이블: contents
-- 실행 방법:
--   docker exec wagglebot-crawler-1 python -c "
--     from db.session import engine
--     with engine.connect() as conn:
--         conn.execute(open('db/migrations/add_variant_fields.sql').read())
--         conn.commit()
--   "
-- 또는 MariaDB 클라이언트에서 직접 실행

ALTER TABLE contents
    ADD COLUMN IF NOT EXISTS variant_group  VARCHAR(64)  NULL COMMENT 'A/B 테스트 그룹 ID',
    ADD COLUMN IF NOT EXISTS variant_label  VARCHAR(32)  NULL COMMENT '"A" 또는 "B"',
    ADD COLUMN IF NOT EXISTS variant_config JSON         NULL COMMENT '변형별 설정값 (extra_instructions 등)';

-- 인덱스: 그룹별 성과 조회 최적화
CREATE INDEX IF NOT EXISTS ix_contents_variant_group
    ON contents (variant_group);
