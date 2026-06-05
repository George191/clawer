"""工作流模型 — 定义多步骤采集工作流的配置与结果类型。

从单步骤采集升级为含依赖的多步骤 DAG 工作流。
支持步骤类型: crawl / filter / transform / export / ai_analyze。
"""

from __future__ import annotations

from enum import Enum
from typing import Any
from collections import deque
from datetime import datetime, timezone

from pydantic import BaseModel, Field


# ── 步骤类型枚举 ────────────────────────────────────────────────────────

class StepType(str, Enum):
    CRAWL = "crawl"             # 执行模板采集 (SpiderEngine)
    FILTER = "filter"            # 数据筛选/过滤 (Python 表达式)
    TRANSFORM = "transform"      # 数据转换 (Jinja2 模板)
    EXPORT = "export"            # 导出 (XLSX/CSV/JSON)
    AI_ANALYZE = "ai_analyze"    # AI 分析 (调用 LLM)


class ExportFormat(str, Enum):
    XLSX = "xlsx"
    CSV = "csv"
    JSON = "json"
    JSONL = "jsonl"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── 工作流配置 ──────────────────────────────────────────────────────────

class WorkflowStep(BaseModel):
    """工作流步骤定义。"""

    id: str = Field(description="步骤唯一标识")
    type: StepType = Field(description="步骤类型")
    template: str | None = Field(default=None, description="CRAWL: 模板名称")
    input_from: str | None = Field(default=None, description="上游步骤 id，即数据来源")
    params: dict[str, Any] = Field(default_factory=dict, description="模板参数或转换参数")
    condition: str | None = Field(default=None, description="FILTER: 筛选条件")
    transform_template: str | None = Field(default=None, description="TRANSFORM: Jinja2 模板")
    export_format: ExportFormat = Field(default=ExportFormat.XLSX, description="EXPORT: 输出格式")
    export_filename: str | None = Field(default=None, description="EXPORT: 文件名（不含后缀）")
    ai_prompt: str | None = Field(default=None, description="AI_ANALYZE: 分析提示词")
    ai_model: str | None = Field(default=None, description="AI_ANALYZE: 模型名称")
    concurrency: int = Field(default=1, description="并发数")
    on_failure: str = Field(default="abort", description="失败策略: abort | skip | continue")
    retries: int = Field(default=1, description="失败重试次数")

    @property
    def input_steps(self) -> list[str]:
        """依赖的上游步骤 id 列表。"""
        if self.input_from:
            return [s.strip() for s in self.input_from.split(",") if s.strip()]
        return []


class WorkflowConfig(BaseModel):
    """工作流配置 — YAML 格式定义。

    示例 YAML::

        name: patent_landscape
        inputs:
          company: Google
        steps:
          - id: search
            type: crawl
            template: google_patent
            params:
              assignee: "{{ inputs.company }}"
              num: 100
          - id: filter_grants
            type: filter
            input_from: search
            condition: "record.status == 'GRANT'"
          - id: enrich
            type: crawl
            template: google_patent_detail
            input_from: filter_grants
            concurrency: 5
          - id: export
            type: export
            input_from: enrich
            export_format: xlsx
            export_filename: patent_report
    """

    name: str = Field(description="工作流名称")
    description: str = Field(default="", description="工作流描述")
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description="工作流全局输入参数，可在步骤 params 中通过 inputs.xxx 引用",
    )
    steps: list[WorkflowStep] = Field(description="步骤列表（DAG 顺序）")
    concurrency: int = Field(default=3, description="同级步骤最大并发数")
    timeout: float = Field(default=3600.0, description="工作流超时（秒）")

    @property
    def root_steps(self) -> list[WorkflowStep]:
        """获取没有 input_from 的根步骤（入口）。"""
        return [s for s in self.steps if not s.input_steps]

    @property
    def step_map(self) -> dict[str, WorkflowStep]:
        """步骤 id → 步骤映射。"""
        return {s.id: s for s in self.steps}

    def get_downstream(self, step_id: str) -> list[WorkflowStep]:
        """获取依赖指定步骤的下游步骤。"""
        return [s for s in self.steps if step_id in s.input_steps]

    def validate_dag(self) -> tuple[bool, list[str]]:
        """校验工作流是否为有效 DAG（无环、依赖存在）。"""
        errors: list[str] = []
        ids = {s.id for s in self.steps}

        for step in self.steps:
            for dep in step.input_steps:
                if dep not in ids:
                    errors.append(f"Step '{step.id}' depends on unknown step '{dep}'")

        visited: set[str] = set()
        rec_stack: set[str] = set()

        def _has_cycle(node_id: str) -> bool:
            visited.add(node_id)
            rec_stack.add(node_id)
            step = self.step_map.get(node_id)
            if step:
                for dep in step.input_steps:
                    if dep not in visited:
                        if _has_cycle(dep):
                            return True
                    elif dep in rec_stack:
                        return True
            rec_stack.discard(node_id)
            return False

        for sid in ids:
            if sid not in visited:
                if _has_cycle(sid):
                    errors.append("Workflow contains a cycle (not a valid DAG)")
                    break

        return len(errors) == 0, errors

    def topological_sort(self) -> list[list[str]]:
        """Kahn 算法拓扑排序，返回层级列表。

        Returns:
            如 [["search"], ["filter"], ["enrich", "export"]]
            同级步骤可并发执行。
        """
        in_degree: dict[str, int] = {s.id: len(s.input_steps) for s in self.steps}
        adj: dict[str, list[str]] = {s.id: [] for s in self.steps}
        for s in self.steps:
            for dep in s.input_steps:
                if dep in adj:
                    adj[dep].append(s.id)

        queue = deque([sid for sid, deg in in_degree.items() if deg == 0])
        levels: list[list[str]] = []

        while queue:
            level = list(queue)
            queue.clear()
            levels.append(level)

            for sid in level:
                for neighbor in adj.get(sid, []):
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        queue.append(neighbor)

        return levels


# ── 工作流结果 ──────────────────────────────────────────────────────────

class StepResult(BaseModel):
    """单个步骤的执行结果。"""

    step_id: str = Field(description="步骤 ID")
    step_type: StepType = Field(description="步骤类型")
    status: StepStatus = Field(default=StepStatus.PENDING, description="执行状态")
    records: list[dict[str, Any]] = Field(default_factory=list, description="输出记录")
    total_records: int = Field(default=0, description="记录总数")
    errors: list[str] = Field(default_factory=list, description="错误信息")
    started_at: str = Field(default="", description="开始时间")
    finished_at: str = Field(default="", description="完成时间")
    export_file: str = Field(default="", description="EXPORT: 导出文件路径")
    ai_output: str = Field(default="", description="AI_ANALYZE: 分析输出")

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "step_type": self.step_type.value,
            "status": self.status.value,
            "total_records": self.total_records,
            "errors": self.errors,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "export_file": self.export_file,
            "ai_output": self.ai_output,
        }


class WorkflowResult(BaseModel):
    """工作流执行结果。"""

    workflow_id: str = Field(description="执行实例 ID")
    workflow_name: str = Field(description="工作流名称")
    status: StepStatus = Field(default=StepStatus.PENDING, description="整体状态")
    steps: dict[str, StepResult] = Field(default_factory=dict, description="步骤结果 (step_id → result)")
    total_records: int = Field(default=0, description="总记录数")
    errors: list[str] = Field(default_factory=list, description="全局错误")
    started_at: str = Field(default="", description="开始时间")
    finished_at: str = Field(default="", description="完成时间")

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "workflow_name": self.workflow_name,
            "status": self.status.value,
            "total_records": self.total_records,
            "errors": self.errors,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "steps": {
                sid: sr.to_dict() for sid, sr in self.steps.items()
            },
        }

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()