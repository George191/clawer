"""工作流引擎 — 按 DAG 执行多步骤采集工作流。

将单步 SpiderEngine.crawl() 升级为多步骤编排:
- crawl → SpiderEngine 执行模板采集
- filter → Python 表达式筛选记录
- transform → Jinja2 数据转换
- export → XLSX/CSV/JSON 导出
- ai_analyze → LLM 分析

引擎负责 DAG 拓扑排序、步骤间数据传递、并发执行同级步骤。
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.models.workflow import (
    ExportFormat,
    StepResult,
    StepStatus,
    StepType,
    WorkflowConfig,
    WorkflowResult,
    WorkflowStep,
)

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """工作流引擎 — 按 DAG 编排多步骤采集。

    使用::

        config = WorkflowConfig(name="my_flow", steps=[...])
        engine = WorkflowEngine()
        result = await engine.execute(config)
    """

    def __init__(self) -> None:
        self._step_results: dict[str, StepResult] = {}
        self._step_data: dict[str, list[dict[str, Any]]] = {}

    # ── Public API ───────────────────────────────────────────────────────

    async def execute(self, workflow: WorkflowConfig) -> WorkflowResult:
        """执行完整工作流。

        流程:
        1. 校验 DAG
        2. 拓扑排序确定执行顺序
        3. 逐层并发执行
        4. 步骤间自动传递数据
        """
        result = WorkflowResult(
            workflow_id=self._gen_id(),
            workflow_name=workflow.name,
            started_at=WorkflowResult._now(),
        )

        # 校验 DAG
        valid, errors = workflow.validate_dag()
        if not valid:
            result.status = StepStatus.FAILED
            result.errors = errors
            result.finished_at = WorkflowResult._now()
            return result

        # 初始化步骤结果
        for step in workflow.steps:
            self._step_results[step.id] = StepResult(
                step_id=step.id,
                step_type=step.type,
                status=StepStatus.PENDING,
            )

        # 解析全局 input 参数 (Jinja2 渲染)
        global_inputs = await self._render_inputs(workflow.inputs, {})

        # 拓扑排序执行
        order = workflow.topological_sort()
        logger.info("Workflow %s: %d steps, order=%s", workflow.name, len(order), order)

        for level in order:
            # 同级步骤并发执行
            tasks = []
            for step_id in level:
                step = workflow.step_map[step_id]
                tasks.append(self._execute_step(step, workflow, global_inputs))

            level_results = await asyncio.gather(*tasks, return_exceptions=True)

            # 检查是否有 abort 步骤
            for step_result in level_results:
                if isinstance(step_result, Exception):
                    logger.error("Level execution failed: %s", step_result)
                    result.errors.append(str(step_result))
                    result.status = StepStatus.FAILED
                    result.finished_at = WorkflowResult._now()
                    return result

                if (
                    step_result.status == StepStatus.FAILED
                    and workflow.step_map.get(step_result.step_id)
                    and workflow.step_map[step_result.step_id].on_failure == "abort"
                ):
                    result.status = StepStatus.FAILED
                    result.errors.append(
                        f"Step '{step_result.step_id}' failed: {step_result.errors}"
                    )
                    result.steps = self._step_results
                    result.finished_at = WorkflowResult._now()
                    return result

        # 汇总
        result.status = StepStatus.DONE
        result.steps = self._step_results
        result.total_records = sum(
            r.total_records for r in self._step_results.values() if r.status == StepStatus.DONE
        )
        result.errors = []
        for sr in self._step_results.values():
            result.errors.extend(sr.errors)
        result.finished_at = WorkflowResult._now()

        logger.info(
            "Workflow %s complete: %d steps, %d records, %d errors",
            workflow.name,
            len(order),
            result.total_records,
            len(result.errors),
        )
        return result



    async def _execute_step(
        self,
        step: WorkflowStep,
        workflow: WorkflowConfig,
        global_inputs: dict[str, Any],
    ) -> StepResult:
        """执行单个步骤，含重试逻辑。"""
        result = self._step_results[step.id]
        result.status = StepStatus.RUNNING
        result.started_at = StepResult.model_fields["started_at"].default_factory() if hasattr(StepResult, 'model_fields') else datetime.now(timezone.utc).isoformat()

        # 从上游步骤收集输入数据
        input_data = self._collect_input_data(step, workflow)

        last_error: Exception | None = None
        for attempt in range(step.retries + 1):
            try:
                handler = _STEP_HANDLERS.get(step.type)
                if handler is None:
                    raise ValueError(f"Unknown step type: {step.type}")

                output = await handler(step, input_data, global_inputs, workflow)

                result.records = output.get("records", [])
                result.total_records = len(result.records)
                result.export_file = output.get("export_file", "")
                result.ai_output = output.get("ai_output", "")
                result.status = StepStatus.DONE
                result.finished_at = datetime.now(timezone.utc).isoformat()

                # 存储数据供下游步骤使用
                if result.records:
                    self._step_data[step.id] = deepcopy(result.records)

                return result

            except Exception as e:
                last_error = e
                logger.error(
                    "Step '%s' failed (attempt %d/%d): %s",
                    step.id, attempt + 1, step.retries + 1, e,
                )
                if attempt < step.retries:
                    await asyncio.sleep(2 ** attempt)

        # 全部重试失败
        result.status = StepStatus.FAILED
        result.errors.append(f"{last_error}\n{traceback.format_exc()}")
        result.finished_at = datetime.now(timezone.utc).isoformat()
        return result

    def _collect_input_data(
        self,
        step: WorkflowStep,
        workflow: WorkflowConfig,
    ) -> list[dict[str, Any]]:
        """从上游步骤收集数据，合并为一个列表。"""
        if not step.input_steps:
            return []

        merged: list[dict[str, Any]] = []
        for dep_id in step.input_steps:
            dep_data = self._step_data.get(dep_id, [])
            merged.extend(dep_data)

        return merged

    async def _render_inputs(
        self,
        inputs: dict[str, Any],
        step_data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """渲染全局 inputs（支持 Jinja2 模板替换）。"""
        if not inputs:
            return {}

        try:
            from app.engine.jinja2_renderer import Jinja2Renderer
            renderer = Jinja2Renderer()
            rendered: dict[str, Any] = {}
            for key, value in inputs.items():
                if isinstance(value, str) and ("{{" in value or "{%" in value):
                    rendered[key] = renderer.render_string(value, {"inputs": inputs, "data": step_data})
                else:
                    rendered[key] = value
            return rendered
        except ImportError:
            return inputs

    @staticmethod
    def _gen_id() -> str:
        import uuid
        return str(uuid.uuid4())[:8]


# ── 步骤处理器注册表 ───────────────────────────────────────────────────

_StepHandler = Any  # async callable(step, input_data, global_inputs, workflow) -> dict


_STEP_HANDLERS: dict[StepType, _StepHandler] = {}


def register_handler(step_type: StepType):
    """装饰器：注册步骤处理器。"""
    def decorator(fn):
        _STEP_HANDLERS[step_type] = fn
        return fn
    return decorator


# ── CRAWL 处理器 ───────────────────────────────────────────────────────

@register_handler(StepType.CRAWL)
async def _handle_crawl(
    step: WorkflowStep,
    input_data: list[dict[str, Any]],
    global_inputs: dict[str, Any],
    workflow: WorkflowConfig,
) -> dict[str, Any]:
    """执行模板采集 (SpiderEngine)。"""
    from app.config.settings import settings
    from app.engine.template_loader import TemplateLoader
    from app.engine.spider_engine import SpiderEngine

    logger.info("Step '%s' (crawl): template=%s", step.id, step.template)

    # 渲染参数（Jinja2）
    params = await _render_step_params(step, global_inputs, input_data)

    loader = TemplateLoader()
    template = loader.load(step.template, param_values=params)

    engine = SpiderEngine()
    try:
        crawl_result = await engine.crawl(template)
        records = crawl_result.records
        errors = crawl_result.errors

        return {"records": records, "errors": errors}
    finally:
        await engine.close()


# ── FILTER 处理器 ──────────────────────────────────────────────────────

@register_handler(StepType.FILTER)
async def _handle_filter(
    step: WorkflowStep,
    input_data: list[dict[str, Any]],
    global_inputs: dict[str, Any],
    workflow: WorkflowConfig,
) -> dict[str, Any]:
    """数据筛选 — Python 表达式过滤。"""
    condition = step.condition or "True"
    logger.info("Step '%s' (filter): condition=%s", step.id, condition)

    # 安全求值环境
    safe_globals: dict[str, Any] = {
        "__builtins__": {
            "True": True, "False": False, "None": None,
            "len": len, "str": str, "int": int, "float": float,
            "bool": bool, "list": list, "dict": dict,
            "isinstance": isinstance, "abs": abs, "min": min, "max": max,
            "round": round, "sum": sum, "any": any, "all": all,
        },
    }

    filtered: list[dict[str, Any]] = []
    for record in input_data:
        try:
            safe_locals = {"record": record}
            ok = eval(condition, safe_globals, safe_locals)
            if ok:
                filtered.append(record)
        except Exception as e:
            logger.debug("Filter eval failed for record: %s", e)

    logger.info(
        "Step '%s' (filter): %d → %d records",
        step.id, len(input_data), len(filtered),
    )
    return {"records": filtered}


# ── TRANSFORM 处理器 ───────────────────────────────────────────────────

@register_handler(StepType.TRANSFORM)
async def _handle_transform(
    step: WorkflowStep,
    input_data: list[dict[str, Any]],
    global_inputs: dict[str, Any],
    workflow: WorkflowConfig,
) -> dict[str, Any]:
    """数据转换 — Jinja2 模板渲染。"""
    logger.info("Step '%s' (transform): %d records", step.id, len(input_data))

    template_str = step.transform_template or ""
    if not template_str:
        logger.warning("Step '%s' (transform): no template, passing through", step.id)
        return {"records": input_data}

    try:
        from app.engine.jinja2_renderer import Jinja2Renderer

        renderer = Jinja2Renderer()
        transformed: list[dict[str, Any]] = []

        for record in input_data:
            try:
                context = {
                    "record": record,
                    "inputs": global_inputs,
                }
                result = renderer.render_string(template_str, context)
                # 尝试解析 JSON
                import json
                parsed = json.loads(result)
                if isinstance(parsed, dict):
                    transformed.append(parsed)
                elif isinstance(parsed, list):
                    transformed.extend(parsed)
                else:
                    transformed.append({"_result": result})
            except Exception as e:
                logger.debug("Transform failed for record: %s", e)
                transformed.append(record)  # 原样保留

        logger.info("Step '%s' (transform): %d → %d records", step.id, len(input_data), len(transformed))
        return {"records": transformed}

    except ImportError:
        logger.warning("Jinja2 not available, passing through")
        return {"records": input_data}


# ── EXPORT 处理器 ──────────────────────────────────────────────────────

@register_handler(StepType.EXPORT)
async def _handle_export(
    step: WorkflowStep,
    input_data: list[dict[str, Any]],
    global_inputs: dict[str, Any],
    workflow: WorkflowConfig,
) -> dict[str, Any]:
    """导出数据为 XLSX/CSV/JSON 文件。"""
    import json
    from pathlib import Path

    logger.info("Step '%s' (export): format=%s, records=%d", step.id, step.export_format, len(input_data))

    filename = step.export_filename or f"{workflow.name}_{step.id}"
    output_dir = Path(settings.output_dir) / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)

    export_file = ""

    if step.export_format == ExportFormat.JSON or step.export_format == ExportFormat.JSONL:
        filepath = output_dir / f"{filename}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(input_data, f, ensure_ascii=False, indent=2)
        export_file = str(filepath)

    elif step.export_format == ExportFormat.CSV:
        filepath = output_dir / f"{filename}.csv"
        if input_data:
            import csv
            keys = list(input_data[0].keys())
            with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                writer.writerows(input_data)
        else:
            filepath.write_text("", encoding="utf-8-sig")
        export_file = str(filepath)

    elif step.export_format == ExportFormat.XLSX:
        filepath = output_dir / f"{filename}.xlsx"
        try:
            import openpyxl

            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = workflow.name[:31]

            if input_data:
                keys = list(input_data[0].keys())
                ws.append(keys)
                for record in input_data:
                    row = [record.get(k, "") for k in keys]
                    ws.append(row)

            wb.save(filepath)
            export_file = str(filepath)
        except ImportError:
            # 回退到 CSV
            logger.warning("openpyxl not installed, falling back to CSV")
            filepath = output_dir / f"{filename}.csv"
            import csv
            if input_data:
                keys = list(input_data[0].keys())
                with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    writer.writerows(input_data)
            else:
                filepath.write_text("", encoding="utf-8-sig")
            export_file = str(filepath)

    logger.info("Step '%s' (export): saved to %s", step.id, export_file)
    return {"records": input_data, "export_file": export_file}


# ── AI_ANALYZE 处理器 ─────────────────────────────────────────────────

@register_handler(StepType.AI_ANALYZE)
async def _handle_ai_analyze(
    step: WorkflowStep,
    input_data: list[dict[str, Any]],
    global_inputs: dict[str, Any],
    workflow: WorkflowConfig,
) -> dict[str, Any]:
    """AI 分析 — 调用 LLM 对数据进行分析。

    使用 step.ai_prompt 作为系统提示，将数据摘要附加到 prompt 中。
    """
    logger.info("Step '%s' (ai_analyze): %d records, model=%s", step.id, len(input_data), step.ai_model or "default")

    prompt = step.ai_prompt or "分析以下数据集，识别关键模式和趋势。"
    model = step.ai_model or "gpt-4o"

    # 构造数据摘要（截断过长数据）
    data_summary = _build_data_summary(input_data)

    full_prompt = f"""{prompt}

数据摘要（共 {len(input_data)} 条记录）:
{data_summary}

请提供分析结论。"""

    # 调用 LLM
    ai_output = ""
    try:
        llm_url = getattr(settings, "llm_api_url", None) or settings.db_url or ""
        llm_key = getattr(settings, "llm_api_key", None) or ""

        if llm_url and llm_key:
            # 实际调用 OpenAI-compatible API
            ai_output = await _call_llm(full_prompt, model, llm_url, llm_key)
        else:
            # 无配置时返回数据摘要作为占位
            ai_output = f"[AI analysis unavailable — LLM not configured]\n\nRecords analyzed: {len(input_data)}\n\nFields: {_list_fields(input_data)}"

    except Exception as e:
        logger.error("AI analyze failed: %s", e)
        ai_output = f"Analysis failed: {e}"

    logger.info("Step '%s' (ai_analyze): output length=%d", step.id, len(ai_output))
    return {"records": input_data, "ai_output": ai_output}


# ── 辅助函数 ────────────────────────────────────────────────────────────

async def _render_step_params(
    step: WorkflowStep,
    global_inputs: dict[str, Any],
    input_data: list[dict[str, Any]],
) -> dict[str, str]:
    """渲染步骤参数（Jinja2 模板替换）。"""
    params: dict[str, str] = {}

    for key, value in step.params.items():
        str_value = str(value)

        # 替换全局变量: {{ inputs.company }}
        if ("{{" in str_value) or ("{%" in str_value):
            try:
                from app.engine.jinja2_renderer import Jinja2Renderer
                renderer = Jinja2Renderer()
                str_value = renderer.render_string(
                    str_value,
                    {"inputs": global_inputs, "data": input_data},
                )
            except ImportError:
                pass

        params[key] = str_value

    return params


def _build_data_summary(input_data: list[dict[str, Any]], max_items: int = 20) -> str:
    """构造数据摘要文本（截断）。"""
    if not input_data:
        return "(no data)"

    sample = input_data[:max_items]
    import json

    lines = [f"Total records: {len(input_data)}", f"Showing first {len(sample)}:", ""]
    for i, record in enumerate(sample[:5]):
        lines.append(f"  [{i}] {json.dumps(record, ensure_ascii=False, default=str)[:500]}")

    return "\n".join(lines)


def _list_fields(input_data: list[dict[str, Any]]) -> str:
    """列出所有字段名。"""
    if not input_data:
        return "(none)"
    fields = list(input_data[0].keys())
    return ", ".join(fields)


async def _call_llm(
    prompt: str,
    model: str,
    api_url: str,
    api_key: str,
    timeout: float = 120.0,
) -> str:
    """调用 OpenAI-compatible LLM API。"""
    import json as _json

    from app.downloader.http_client import HttpClient

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个数据分析专家。请根据数据提供专业、简洁的分析。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 4000,
    }

    client = HttpClient()
    try:
        from app.models.template import RequestConfig

        config = RequestConfig(
            method="POST",
            headers=headers,
        )
        resp = await client.request_page(
            api_url,
            config,
            data=_json.dumps(body),
        )
        data = _json.loads(resp)
        content = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        return content or resp[:2000]
    finally:
        await client.close()