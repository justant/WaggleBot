-- Migration: llm_logs 테이블 생성
-- LLM 호출 이력 (프롬프트 튜닝용)
--
-- 실행 방법:
--   docker exec wagglebot-crawler-1 python db/migrations/run_llm_logs_migration.py
-- 또는 MariaDB 클라이언트에서 직접 실행

CREATE TABLE IF NOT EXISTS llm_logs (
    id             BIGINT       AUTO_INCREMENT PRIMARY KEY,
    post_id        BIGINT       NULL COMMENT '연결 게시글 (삭제 시 NULL 유지)',

    -- 호출 메타
    call_type      VARCHAR(32)  NOT NULL COMMENT 'generate_script | chunk',
    model_name     VARCHAR(64)  NULL,
    strategy       VARCHAR(32)  NULL      COMMENT 'img_heavy | balanced | text_heavy',
    image_count    INT          NOT NULL DEFAULT 0,
    content_length INT          NOT NULL DEFAULT 0 COMMENT '원문 글자 수',

    -- 프롬프트 / 응답 (MEDIUMTEXT: 최대 16MB)
    prompt_text    MEDIUMTEXT   NULL COMMENT 'LLM에 전달한 전체 프롬프트',
    raw_response   MEDIUMTEXT   NULL COMMENT 'Ollama 원시 응답',
    parsed_result  JSON         NULL COMMENT 'validate_and_fix 후 최종 dict',

    -- 결과
    success        TINYINT(1)   NOT NULL DEFAULT 1,
    error_message  TEXT         NULL,
    duration_ms    INT          NULL     COMMENT '응답 소요 밀리초',

    created_at     DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_llm_logs_post FOREIGN KEY (post_id)
        REFERENCES posts(id) ON DELETE SET NULL,

    INDEX ix_llm_logs_post_id    (post_id),
    INDEX ix_llm_logs_call_type  (call_type),
    INDEX ix_llm_logs_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='LLM 호출 이력 (프롬프트 튜닝용)';
