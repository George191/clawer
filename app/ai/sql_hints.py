"""SQL 智能提示引擎 — 为 ETL 数据浏览器提供 SQL 补全与安全校验。

特性：
- Schema 缓存：启动时扫描 ts_rds/ts_ods/ts_task/ts_dwd/ts_dws/ts_dim 表结构
- 上下文感知补全：根据光标位置智能推荐关键字/表名/列名
- 预定义模板：常用查询模板一键插入
- 安全分析：只读 SQL 校验，拦截危险写操作
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.storage.postgres_client import PostgresClient

logger = logging.getLogger(__name__)

# ── ETL Schema 列表 ──────────────────────────────────────────
_ETL_SCHEMAS = ["ts_rds", "ts_ods", "ts_task", "ts_dwd", "ts_dws", "ts_dim"]

# ── SQL 关键字（用于补全） ──────────────────────────────────
_SQL_KEYWORDS: list[str] = [
    "SELECT", "FROM", "WHERE", "JOIN", "INNER JOIN", "LEFT JOIN",
    "RIGHT JOIN", "FULL JOIN", "CROSS JOIN", "ON", "GROUP BY",
    "ORDER BY", "LIMIT", "OFFSET", "COUNT", "SUM", "AVG", "MAX",
    "MIN", "DISTINCT", "AS", "IN", "BETWEEN", "LIKE", "ILIKE",
    "AND", "OR", "NOT", "NULL", "IS", "IS NOT", "IS NULL",
    "HAVING", "UNION", "UNION ALL", "CASE", "WHEN", "THEN",
    "ELSE", "END", "CAST", "EXISTS", "ANY", "ALL", "ASC", "DESC",
    "SIMILAR TO", "WITH", "EXPLAIN", "EXPLAIN ANALYZE", "SHOW",
    "FETCH", "NEXT", "ROWS ONLY", "PARTITION BY", "OVER",
    "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD",
    "COALESCE", "NULLIF", "GREATEST", "LEAST", "STRING_AGG",
    "ARRAY_AGG", "JSONB_BUILD_OBJECT", "TO_CHAR", "NOW",
    "CURRENT_TIMESTAMP", "INTERVAL", "EXTRACT", "DATE_TRUNC",
    "FILTER", "LATERAL", "TABLESAMPLE",
]

# ── 危险关键字（写操作 — 将被拦截） ──────────────────────────
_DANGEROUS_KEYWORDS: list[str] = [
    "DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "TRUNCATE",
    "CREATE", "GRANT", "REVOKE", "EXECUTE", "CALL", "MERGE",
    "REPLACE", "COPY", "VACUUM", "ANALYZE", "REINDEX", "CLUSTER",
    "DISCARD", "SET", "BEGIN", "COMMIT", "ROLLBACK", "LOCK",
    "UNLISTEN", "NOTIFY",
]

# ── 只读关键字（校验白名单前缀） ────────────────────────────
_READONLY_PREFIXES: list[str] = [
    "SELECT", "WITH", "EXPLAIN", "SHOW", "DESCRIBE", "DESC",
]


@dataclass
class Completion:
    """SQL 补全项。"""
    type: str          # keyword | table | column
    text: str          # 补全文本
    detail: str = ""   # 附加信息（如列类型、列数）


@dataclass
class ValidationResult:
    """SQL 安全校验结果。"""
    is_safe: bool
    violations: list[str] = field(default_factory=list)
    warning: str = ""


# =====================================================================
#  SQL 上下文解析（纯正则，无外部依赖）
# =====================================================================

# 匹配已写的表引用（FROM / JOIN 后面的 schema.table 或 table）
_RE_TABLE_REFS = re.compile(
    r"""
    (?:FROM|JOIN)\s+
    (?:
        ([a-zA-Z_][a-zA-Z0-9_]*\.[a-zA-Z_][a-zA-Z0-9_]*)  # schema.table
        |
        ([a-zA-Z_][a-zA-Z0-9_]*)                             # 纯表名
        |
        \(                                                    # 子查询开始
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# 匹配 SQL 子句边界关键词
_RE_CLAUSE_KEYWORDS = re.compile(
    r'\b('
    r'SELECT|FROM|WHERE|JOIN|INNER\s+JOIN|LEFT\s+JOIN|RIGHT\s+JOIN|'
    r'FULL\s+JOIN|CROSS\s+JOIN|ON|GROUP\s+BY|ORDER\s+BY|HAVING|'
    r'LIMIT|SET|INTO'
    r')\b',
    re.IGNORECASE,
)

# 匹配注释
_RE_SINGLE_LINE_COMMENT = re.compile(r'--[^\n]*')
_RE_MULTI_LINE_COMMENT = re.compile(r'/\*.*?\*/', re.DOTALL)


# =====================================================================
#  辅助函数
# =====================================================================

def _current_token(sql: str, position: int) -> tuple[str, int, int]:
    """返回光标所在 token 及起止位置。

    例如: sql="SELECT * FROM ts_r", position=16 → ("ts_r", 14, 18)
    """
    if position < 0 or position > len(sql):
        return "", 0, 0

    start = position
    while start > 0 and (sql[start - 1].isalnum() or sql[start - 1] in "_."):
        start -= 1

    end = position
    while end < len(sql) and (sql[end].isalnum() or sql[end] in "_."):
        end += 1

    return sql[start:end], start, end


def _last_clause(sql: str, position: int) -> str:
    """返回光标位置之前遇到的最后一个 SQL 子句关键词。"""
    before = sql[:position]
    matches = list(_RE_CLAUSE_KEYWORDS.finditer(before))
    if not matches:
        return ""
    raw = matches[-1].group(1).upper()
    return raw.replace(" ", "_")


def _extract_tables(sql: str) -> list[str]:
    """从 SQL 中提取所有 FROM/JOIN 后的表引用（忽略子查询）。

    返回 ["ts_rds.rds_raw_records", ...] 形式。
    """
    tables: list[str] = []
    # 追踪子查询深度
    base_sql = _remove_comments(sql)

    for m in _RE_TABLE_REFS.finditer(base_sql):
        matched = m.group(0)
        if "(" in matched:
            continue  # 跳过子查询
        if m.group(1):
            tables.append(m.group(1))      # schema.table
        elif m.group(2):
            tables.append(m.group(2))      # bare table

    return tables


def _remove_comments(sql: str) -> str:
    """移除 SQL 注释（避免注释中的关键字干扰解析）。"""
    sql = _RE_SINGLE_LINE_COMMENT.sub("", sql)
    sql = _RE_MULTI_LINE_COMMENT.sub("", sql)
    return sql


def _strip_strings(sql: str) -> str:
    """移除 SQL 字符串字面量（避免字面量中的关键字被误判）。"""
    sql = re.sub(r"'[^']*'", "''", sql)
    sql = re.sub(r"\$\$.*?\$\$", "$$", sql, flags=re.DOTALL)
    return sql


# =====================================================================
#  SQLHintEngine
# =====================================================================

class SQLHintEngine:
    """SQL 智能提示引擎。

    用法::

        engine = SQLHintEngine()
        await engine.initialize(pg_client)

        # 获取补全
        completions = engine.get_completions("SELECT * FROM ", 14)

        # 校验 SQL
        result = engine.validate_sql("DROP TABLE x")
    """

    def __init__(self) -> None:
        # schema_name → table_name → [{"column_name": ..., "data_type": ...}, ...]
        self._schema: dict[str, dict[str, list[dict[str, str]]]] = {}
        self._all_tables: list[str] = []

    # ── Schema 缓存 ──────────────────────────────────────

    async def initialize(self, pg_client: "PostgresClient") -> None:
        """初始化：连接数据库并加载表结构元数据。"""
        await pg_client.connect()
        await self.refresh_schema()

    async def refresh_schema(self) -> None:
        """刷新 schema 缓存 — 从 information_schema 重新加载。"""
        from app.storage.postgres_client import get_pg_client

        pg = get_pg_client()
        await pg.connect()

        self._schema = {}
        self._all_tables = []

        query = """
            SELECT
                table_schema,
                table_name,
                column_name,
                data_type,
                ordinal_position
            FROM information_schema.columns
            WHERE table_schema = ANY(:schemas)
            ORDER BY table_schema, table_name, ordinal_position
        """
        rows = await pg.fetch_all(query, {"schemas": _ETL_SCHEMAS})

        for row in rows:
            s = row["table_schema"]
            t = row["table_name"]
            col_info = {
                "column_name": row["column_name"],
                "data_type": row["data_type"],
            }
            self._schema.setdefault(s, {}).setdefault(t, []).append(col_info)

        self._all_tables = sorted(
            f"{s}.{t}" for s, tables in self._schema.items() for t in tables
        )

        total_tables = sum(len(t) for t in self._schema.values())
        logger.info(
            "SQLHintEngine schema refreshed: %d schemas, %d tables",
            len(self._schema), total_tables,
        )

    # ── 补全入口 ─────────────────────────────────────────

    def get_completions(self, sql: str, position: int) -> list[Completion]:
        """核心方法：根据光标位置返回智能补全列表。

        Args:
            sql: 当前编辑器中的 SQL 文本
            position: 光标位置（int，字符偏移量）

        Returns:
            补全项列表（按匹配度排序）
        """
        token, token_start, token_end = _current_token(sql, position)
        context = _last_clause(sql, position)

        # ── FROM / JOIN 后 → 表名 + 子查询关键字 ──
        if context in ("FROM", "JOIN", "INNER_JOIN", "LEFT_JOIN",
                        "RIGHT_JOIN", "FULL_JOIN", "CROSS_JOIN"):
            completions = self.get_table_completions(token)
            if not token:
                completions.append(Completion(
                    type="keyword", text="SELECT", detail="子查询开头",
                ))
            return completions

        # ── SELECT 后 → 列名 + 聚合/窗口函数 ──
        if context == "SELECT":
            tables = _extract_tables(sql[:position])
            completions = self._column_completions_for_tables(tables, token)
            for kw in ("COUNT", "SUM", "AVG", "MAX", "MIN",
                        "DISTINCT", "CAST", "CASE", "COALESCE",
                        "ROW_NUMBER", "RANK", "STRING_AGG", "ARRAY_AGG"):
                if not token or kw.startswith(token.upper()):
                    completions.append(
                        Completion(type="keyword", text=kw, detail="函数/关键字")
                    )
            return completions

        # ── WHERE / ON / HAVING / AND / OR → 列名 + 条件关键字 ──
        if context in ("WHERE", "ON", "HAVING"):
            tables = _extract_tables(sql[:position])
            completions = self._column_completions_for_tables(tables, token)
            for kw in ("AND", "OR", "NOT", "IN", "BETWEEN", "LIKE",
                        "ILIKE", "IS", "IS NOT", "NULL", "EXISTS",
                        "ANY", "ALL", "SIMILAR TO"):
                if not token or kw.startswith(token.upper()):
                    completions.append(
                        Completion(type="keyword", text=kw)
                    )
            return completions

        # ── GROUP BY / ORDER BY → 列名 ──
        if context in ("GROUP_BY", "ORDER_BY"):
            tables = _extract_tables(sql[:position])
            completions = self._column_completions_for_tables(tables, token)
            if context == "ORDER_BY":
                for kw in ("ASC", "DESC", "NULLS FIRST", "NULLS LAST"):
                    completions.append(
                        Completion(type="keyword", text=kw)
                    )
            return completions

        # ── 无子句上下文 → 关键字 ──
        completions: list[Completion] = []
        for kw in _SQL_KEYWORDS:
            if not token or kw.upper().startswith(token.upper()):
                completions.append(Completion(type="keyword", text=kw))
        # 也补充表名
        completions.extend(self.get_table_completions(token))
        return completions

    # ── 表名补全 ─────────────────────────────────────────

    def get_table_completions(self, prefix: str = "") -> list[Completion]:
        """返回匹配前缀的 ``schema.table`` 补全列表。"""
        prefix_lower = prefix.lower()
        results: list[Completion] = []
        for full_name in self._all_tables:
            if full_name.lower().startswith(prefix_lower):
                parts = full_name.split(".", 1)
                col_count = len(self._schema.get(parts[0], {}).get(parts[1], []))
                results.append(Completion(
                    type="table",
                    text=full_name,
                    detail=f"{col_count} 列",
                ))
        return results

    # ── 列名补全 ─────────────────────────────────────────

    def get_column_completions(
        self, table: str, prefix: str = ""
    ) -> list[Completion]:
        """返回指定表的列名补全列表。

        Args:
            table: 表引用，支持 ``schema.table`` 或纯 ``table``（会在所有 schema 中查找）
            prefix: 列名前缀过滤
        """
        prefix_lower = prefix.lower()
        columns: list[dict[str, str]] = []

        if "." in table:
            s, t = table.split(".", 1)
            columns = self._schema.get(s, {}).get(t, [])
        else:
            for s in _ETL_SCHEMAS:
                cols = self._schema.get(s, {}).get(table, [])
                if cols:
                    columns = cols
                    break

        results: list[Completion] = []
        for col in columns:
            name = col["column_name"]
            if name.lower().startswith(prefix_lower):
                results.append(Completion(
                    type="column",
                    text=name,
                    detail=col["data_type"],
                ))
        return results

    def _column_completions_for_tables(
        self, tables: list[str], token: str
    ) -> list[Completion]:
        """为多个表收集列名补全。"""
        results: list[Completion] = []
        seen: set[str] = set()
        for table in tables:
            for c in self.get_column_completions(table, token):
                if c.text not in seen:
                    seen.add(c.text)
                    results.append(c)
        return results

    # ── SQL 模板 ─────────────────────────────────────────

    def get_templates(self) -> list[dict]:
        """返回预定义 SQL 查询模板列表。

        每个模板是一个 dict:
            - name: 模板名称
            - description: 说明
            - sql: SQL 模板（使用 {placeholder} 占位）
            - category: 分类
            - tokens: 占位符列表
        """
        return [
            {
                "name": "查询全部（限制100行）",
                "description": "从表中查询所有列，限制 100 行",
                "sql": "SELECT *\nFROM {table}\nLIMIT 100;",
                "category": "基础查询",
                "tokens": ["table"],
            },
            {
                "name": "统计总数",
                "description": "统计表中记录总数",
                "sql": "SELECT COUNT(*) AS total\nFROM {table};",
                "category": "聚合查询",
                "tokens": ["table"],
            },
            {
                "name": "分组统计",
                "description": "按某列分组并统计数量",
                "sql": (
                    "SELECT {column}, COUNT(*) AS cnt\n"
                    "FROM {table}\n"
                    "GROUP BY {column}\n"
                    "ORDER BY cnt DESC\n"
                    "LIMIT 100;"
                ),
                "category": "聚合查询",
                "tokens": ["table", "column"],
            },
            {
                "name": "多列分组统计",
                "description": "按多列分组统计",
                "sql": (
                    "SELECT {column1}, {column2}, COUNT(*) AS cnt\n"
                    "FROM {table}\n"
                    "GROUP BY {column1}, {column2}\n"
                    "ORDER BY cnt DESC\n"
                    "LIMIT 100;"
                ),
                "category": "聚合查询",
                "tokens": ["table", "column1", "column2"],
            },
            {
                "name": "INNER JOIN",
                "description": "通过 record_id 关联两张表",
                "sql": (
                    "SELECT a.*, b.*\n"
                    "FROM {table1} a\n"
                    "INNER JOIN {table2} b ON a.record_id = b.record_id\n"
                    "LIMIT 100;"
                ),
                "category": "关联查询",
                "tokens": ["table1", "table2"],
            },
            {
                "name": "LEFT JOIN",
                "description": "左连接，保留左表全部记录",
                "sql": (
                    "SELECT a.*, b.*\n"
                    "FROM {table1} a\n"
                    "LEFT JOIN {table2} b ON a.record_id = b.record_id\n"
                    "LIMIT 100;"
                ),
                "category": "关联查询",
                "tokens": ["table1", "table2"],
            },
            {
                "name": "时间范围查询",
                "description": "按 created_at 筛选时间范围",
                "sql": (
                    "SELECT *\nFROM {table}\n"
                    "WHERE created_at BETWEEN '{start_date}' AND '{end_date}'\n"
                    "ORDER BY created_at DESC\n"
                    "LIMIT 100;"
                ),
                "category": "条件查询",
                "tokens": ["table", "start_date", "end_date"],
            },
            {
                "name": "按状态筛选",
                "description": "按 status 字段筛选记录",
                "sql": (
                    "SELECT *\nFROM {table}\n"
                    "WHERE status = '{status}'\n"
                    "ORDER BY created_at DESC\n"
                    "LIMIT 100;"
                ),
                "category": "条件查询",
                "tokens": ["table", "status"],
            },
            {
                "name": "JSONB 字段查询",
                "description": "查询 JSONB 字段内的值",
                "sql": (
                    "SELECT *\nFROM {table}\n"
                    "WHERE {json_column} ->> '{key}' = '{value}'\n"
                    "LIMIT 100;"
                ),
                "category": "高级查询",
                "tokens": ["table", "json_column", "key", "value"],
            },
            {
                "name": "窗口函数排名",
                "description": "使用 ROW_NUMBER 窗口函数排名",
                "sql": (
                    "SELECT *,\n"
                    "  ROW_NUMBER() OVER ("
                    "PARTITION BY {column} ORDER BY created_at DESC"
                    ") AS rn\n"
                    "FROM {table}\n"
                    "LIMIT 100;"
                ),
                "category": "高级查询",
                "tokens": ["table", "column"],
            },
            {
                "name": "数据源统计",
                "description": "按 data_source 分组统计",
                "sql": (
                    "SELECT data_source, data_type, COUNT(*) AS cnt\n"
                    "FROM {table}\n"
                    "GROUP BY data_source, data_type\n"
                    "ORDER BY cnt DESC;"
                ),
                "category": "ETL 专用",
                "tokens": ["table"],
            },
            {
                "name": "最近记录",
                "description": "查询最近创建的记录",
                "sql": (
                    "SELECT *\nFROM {table}\n"
                    "ORDER BY created_at DESC\n"
                    "LIMIT {limit};"
                ),
                "category": "基础查询",
                "tokens": ["table", "limit"],
            },
        ]

    # ── SQL 安全分析 ─────────────────────────────────────

    def validate_sql(self, sql: str) -> ValidationResult:
        """校验 SQL 是否安全（只读查询）。

        检测规则：
        1. 去除字符串字面量和注释
        2. 检查是否以只读关键字开头
        3. 扫描是否包含危险写操作关键字

        Returns:
            ValidationResult: is_safe=True 表示可以安全执行
        """
        cleaned = _remove_comments(sql)
        cleaned = _strip_strings(cleaned)
        upper = cleaned.strip().upper()

        # 1. 扫描危险关键字（先扫，用于更准确的错误信息）
        violations: list[str] = []
        for kw in _DANGEROUS_KEYWORDS:
            if re.search(r'\b' + re.escape(kw) + r'\b', upper):
                violations.append(kw)

        # 2. 检查是否以只读关键字开头
        starts_readonly = any(
            upper.startswith(prefix) for prefix in _READONLY_PREFIXES
        )

        if violations and not starts_readonly:
            return ValidationResult(
                is_safe=False,
                violations=violations,
                warning=(
                    f"检测到危险操作: {', '.join(violations)}。"
                    f"SQL 编辑器仅支持只读查询（SELECT / WITH / EXPLAIN / SHOW）。"
                ),
            )

        if not starts_readonly:
            return ValidationResult(
                is_safe=False,
                violations=["INVALID_PREFIX"],
                warning=(
                    "SQL 必须以 SELECT / WITH / EXPLAIN / SHOW 开头。"
                ),
            )

        if violations:
            return ValidationResult(
                is_safe=False,
                violations=violations,
                warning=(
                    f"SQL 中包含危险关键字: {', '.join(violations)}。"
                    f"仅允许只读查询操作。"
                ),
            )

        return ValidationResult(is_safe=True, violations=[], warning="")