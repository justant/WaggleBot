-- Migration 001: Add images column to posts + Create contents table
-- Run this against an existing DB where init_db() was already called.
-- For fresh DBs, init_db() handles everything automatically.

-- 1. posts.images 컬럼 추가
ALTER TABLE posts
  ADD COLUMN IF NOT EXISTS images JSON DEFAULT NULL
  AFTER content;

-- 2. contents 테이블 생성
CREATE TABLE IF NOT EXISTS contents (
  id          BIGINT AUTO_INCREMENT PRIMARY KEY,
  post_id     BIGINT NOT NULL UNIQUE,
  summary_text TEXT DEFAULT NULL,
  audio_path  VARCHAR(255) DEFAULT NULL,
  video_path  VARCHAR(255) DEFAULT NULL,
  upload_meta JSON DEFAULT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_contents_post FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
