-- ProdVideo AI Factory - 数据库初始化脚本
CREATE DATABASE IF NOT EXISTS prodvideo CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 商品表
CREATE TABLE IF NOT EXISTS products (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    platform VARCHAR(32) NOT NULL COMMENT '来源平台',
    platform_product_id VARCHAR(128) NOT NULL COMMENT '平台商品ID',
    title VARCHAR(512) NOT NULL,
    description TEXT,
    main_image_url VARCHAR(1024),
    image_urls JSON COMMENT '商品图片列表',
    price DECIMAL(12,2),
    currency VARCHAR(8) DEFAULT 'CNY',
    sales_count BIGINT DEFAULT 0,
    rating DECIMAL(3,2) DEFAULT 0.00,
    category VARCHAR(128),
    tags JSON,
    raw_data JSON COMMENT '原始抓取数据',
    score DECIMAL(5,2) DEFAULT 0.00 COMMENT '选品评分',
    tier ENUM('hot','normal','cold') DEFAULT 'normal',
    status ENUM('active','inactive','archived') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_platform_product (platform, platform_product_id),
    INDEX idx_tenant (tenant_id),
    INDEX idx_score (score),
    INDEX idx_tier (tier)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 采集计划表
CREATE TABLE IF NOT EXISTS crawl_plans (
    id VARCHAR(64) PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    name VARCHAR(255) NOT NULL,
    platform VARCHAR(64) NOT NULL,
    keyword VARCHAR(255) NOT NULL,
    category VARCHAR(255) DEFAULT '',
    max_count INT DEFAULT 100,
    sort_by VARCHAR(64) DEFAULT 'sales',
    cron_expression VARCHAR(128) DEFAULT NULL,
    enabled TINYINT(1) DEFAULT 1,
    last_run_at TIMESTAMP NULL,
    next_run_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_tenant (tenant_id),
    INDEX idx_enabled_next (enabled, next_run_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 生成流水线表
CREATE TABLE IF NOT EXISTS generation_pipelines (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    product_id BIGINT NOT NULL,
    stage ENUM('pending','crawling','analyzing','generating','composing','publishing','completed','failed','content_filtered') DEFAULT 'pending',
    copywriting TEXT COMMENT '生成的文案',
    copywriting_status ENUM('pending','running','completed','failed') DEFAULT 'pending',
    image_urls JSON COMMENT '生成的图片URL列表',
    images_status ENUM('pending','running','completed','failed') DEFAULT 'pending',
    video_clip_urls JSON COMMENT '生成的视频片段URL列表',
    video_clips_status ENUM('pending','running','completed','failed') DEFAULT 'pending',
    final_video_url VARCHAR(1024) COMMENT '合成后最终视频URL',
    compose_status ENUM('pending','running','completed','failed') DEFAULT 'pending',
    publish_log_id BIGINT,
    publish_status ENUM('pending','running','completed','failed') DEFAULT 'pending',
    config JSON COMMENT '流水线配置快照',
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_tenant (tenant_id),
    INDEX idx_stage (stage),
    INDEX idx_product (product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 发布日志表
CREATE TABLE IF NOT EXISTS publish_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    pipeline_id BIGINT NOT NULL,
    platform VARCHAR(32) NOT NULL,
    platform_post_id VARCHAR(128),
    public_url VARCHAR(1024),
    status ENUM('pending','uploading','published','failed') DEFAULT 'pending',
    publish_content JSON COMMENT '发布的标题/描述/标签等',
    scheduled_time TIMESTAMP NULL,
    published_at TIMESTAMP NULL,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_tenant (tenant_id),
    INDEX idx_pipeline (pipeline_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 模型用量日志表
CREATE TABLE IF NOT EXISTS model_usage_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    pipeline_id BIGINT,
    adapter_id VARCHAR(64) NOT NULL COMMENT '适配器ID',
    adapter_type ENUM('llm','image','video','tts') NOT NULL,
    model VARCHAR(128) NOT NULL,
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    image_count INT DEFAULT 0,
    duration_seconds INT DEFAULT 0,
    estimated_cost_usd DECIMAL(12,6) DEFAULT 0.000000,
    status ENUM('success','failed') DEFAULT 'success',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tenant (tenant_id),
    INDEX idx_adapter (adapter_id),
    INDEX idx_pipeline (pipeline_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 平台配置表
CREATE TABLE IF NOT EXISTS platform_config (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',
    platform VARCHAR(32) NOT NULL,
    config_key VARCHAR(64) NOT NULL,
    config_value TEXT NOT NULL,
    description VARCHAR(256),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_platform_config (platform, config_key),
    INDEX idx_tenant (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 租户配置表
CREATE TABLE IF NOT EXISTS tenant_config (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    config_key VARCHAR(64) NOT NULL,
    config_value TEXT NOT NULL,
    description VARCHAR(256),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_tenant_config (tenant_id, config_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- API Key 表
CREATE TABLE IF NOT EXISTS api_keys (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    api_key_hash VARCHAR(256) NOT NULL,
    prefix VARCHAR(16) NOT NULL COMMENT 'Key前缀用于显示',
    scopes JSON COMMENT '权限范围白名单',
    max_concurrency INT DEFAULT 10,
    enabled TINYINT(1) DEFAULT 1,
    expires_at TIMESTAMP NULL,
    last_used_at TIMESTAMP NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_tenant (tenant_id),
    INDEX idx_key_hash (api_key_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 平台授权表
CREATE TABLE IF NOT EXISTS platform_authorizations (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    platform VARCHAR(32) NOT NULL,
    platform_user_id VARCHAR(128),
    platform_username VARCHAR(128),
    token_encrypted TEXT COMMENT '加密的refresh_token',
    access_token_expires_at TIMESTAMP NULL,
    scopes JSON,
    status ENUM('active','expired','revoked') DEFAULT 'active',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_tenant_platform (tenant_id, platform),
    INDEX idx_tenant (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
