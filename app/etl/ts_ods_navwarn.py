"""ETL ODS 层 — 航行警告 (navwarn) 入库模块。

职责：
- 定义 ods_navwarn 表的 DDL
- 定义 INSERT/UPSERT SQL
- 提供 TsOds 的 _handler_navwarn 处理逻辑

扩展方式（不修改 ts_ods.py）：
    在 ts_ods.py 同级或由 main.py 引入时加载此模块即可触发注册。
    TsOds 基类通过反射机制自动发现 _handler_<table_name> 方法。

参考 Google Patent 入库逻辑设计：
- record_id 唯一标识: "sealagom:{cleaned_warning_no}" 或 "sealagom:msg_{message_id}"
- UPSERT 策略: ON CONFLICT 更新除主键外的所有字段
- quality_score / quality_flags 质量评估
- 额外字段放入 extra_data JSONB
"""

from __future__ import annotations

# ═══════════════════════════════════════════════════════════════════════════
# ODS 航行警告表 DDL
# ═══════════════════════════════════════════════════════════════════════════

ODS_NAVWARN_DDL = """
CREATE TABLE IF NOT EXISTS ts_ods.ods_navwarn (
    id                  BIGSERIAL,
    data_source         VARCHAR(128)    NOT NULL,
    data_type           VARCHAR(64)     NOT NULL,
    record_id           VARCHAR(256)    NOT NULL,

    -- 航行警告核心字段
    navarea_id          SMALLINT,
    warning_no          VARCHAR(128),
    warning_prefix      VARCHAR(64),
    serial_number       INT,
    year                SMALLINT,
    sea_name            VARCHAR(128),
    issue_date          DATE,

    -- 消息内容
    message_text        TEXT,

    -- 分类与关联
    hazard_type         VARCHAR(64),
    coordinates         JSONB,

    -- 质量评估
    quality_score       DOUBLE PRECISION,
    quality_flags       JSONB,
    extra_data          JSONB,

    -- 时间戳
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_ods_navwarn PRIMARY KEY (id, created_at),
    CONSTRAINT uq_ods_navwarn_record UNIQUE (record_id, data_source, created_at)
) PARTITION BY RANGE (created_at);

-- 索引：按 NAVAREA 区域快速查询
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_navarea
    ON ts_ods.ods_navwarn (navarea_id, year, serial_number);

-- 索引：按警告编号查询
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_warning_no
    ON ts_ods.ods_navwarn (warning_no);

-- 索引：按海域名称查询
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_sea_name
    ON ts_ods.ods_navwarn (sea_name);

-- 索引：按危险类型查询
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_hazard
    ON ts_ods.ods_navwarn (hazard_type);

-- 索引：按日期范围查询
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_issue_date
    ON ts_ods.ods_navwarn (issue_date DESC);

-- 部分索引：仅索引生效中的警告
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_recent
    ON ts_ods.ods_navwarn (navarea_id, serial_number DESC)
    WHERE issue_date >= NOW() - INTERVAL '90 days';

-- GIN 索引：坐标 JSONB 查询
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_coordinates
    ON ts_ods.ods_navwarn USING GIN (coordinates);
"""

# ═══════════════════════════════════════════════════════════════════════════
# ODS 航行警告 INSERT / UPSERT SQL
# ═══════════════════════════════════════════════════════════════════════════

ODS_NAVWARN_INSERT = """
INSERT INTO ts_ods.ods_navwarn (
    data_source, data_type, record_id,
    navarea_id, warning_no, warning_prefix, serial_number, year, sea_name, issue_date,
    message_text,
    hazard_type, coordinates,
    quality_score, quality_flags, extra_data,
    created_at, updated_at
) VALUES (
    :data_source, :data_type, :record_id,
    :navarea_id, :warning_no, :warning_prefix, :serial_number, :year, :sea_name,
    CAST(:issue_date AS date),
    :message_text,
    :hazard_type, CAST(:coordinates AS jsonb),
    CAST(:quality_score AS float), CAST(:quality_flags AS jsonb),
    CAST(:extra_data AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, created_at) DO UPDATE SET
    navarea_id = EXCLUDED.navarea_id,
    warning_no = EXCLUDED.warning_no,
    warning_prefix = EXCLUDED.warning_prefix,
    serial_number = EXCLUDED.serial_number,
    year = EXCLUDED.year,
    sea_name = EXCLUDED.sea_name,
    issue_date = EXCLUDED.issue_date,
    message_text = EXCLUDED.message_text,
    hazard_type = EXCLUDED.hazard_type,
    coordinates = EXCLUDED.coordinates,
    quality_score = EXCLUDED.quality_score,
    quality_flags = EXCLUDED.quality_flags,
    extra_data = EXCLUDED.extra_data,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""
