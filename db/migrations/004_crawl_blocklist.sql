CREATE TABLE IF NOT EXISTS crawl_blocklist (
    id         BIGINT       AUTO_INCREMENT PRIMARY KEY,
    site_code  VARCHAR(32)  NOT NULL,
    origin_id  VARCHAR(64)  NOT NULL,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_blocklist_site_origin (site_code, origin_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
