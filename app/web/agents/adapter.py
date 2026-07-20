from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from app.web.agents.base import BaseAgent

logger = logging.getLogger(__name__)


@dataclass
class AdapterResult:
    adapter_code: str
    warnings: list[str] = None
    adapter_name: str = ""

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


class AdapterAgent(BaseAgent):
    def __init__(self):
        model_path = Path(__file__).parents[3] / "models" / "Qwen" / "2.5-Coder-1.5B-Instruct"
        super().__init__(str(model_path))
        self._register_default_prompts()

    def _register_default_prompts(self) -> None:
        self.register_prompt(
            "generate_adapter",
            """
            你是一个专业的爬虫适配器代码生成专家。请根据提供的YAML采集模板，生成对应的Python适配器代码。
                
            要求：
            1. 代码必须符合Python语法规范
            2. 必须包含完整的适配器类，类名使用模板名称的PascalCase格式
            3. 实现列表页采集和详情页采集逻辑
            4. 处理分页、字段提取、数据去重、资源下载
            5. 包含错误处理和重试机制
            6. 使用标准的requests或aiohttp库
            7. 代码必须可运行，无语法错误

            YAML模板：
            {template_yaml}

            请输出完整的Python适配器代码，使用```python和```包裹：
            """,    
        )

    async def generate_adapter(
        self, template_name: str, template_yaml: str
    ) -> AdapterResult:
        prompt = self.render_prompt("generate_adapter", template_yaml=template_yaml)
        response = await self.generate(prompt, max_tokens=8192)

        code = self._extract_code(response)
        warnings = self._validate_code(code)

        return AdapterResult(
            adapter_code=code,
            warnings=warnings,
            adapter_name=f"{template_name}_adapter",
        )

    def _extract_code(self, response: str) -> str:
        match = re.search(r"```python\s*([\s\S]*?)\s*```", response)
        if match:
            return match.group(1).strip()

        match = re.search(r"```\s*([\s\S]*?)\s*```", response)
        if match:
            return match.group(1).strip()

        return response.strip()

    def _validate_code(self, code: str) -> list[str]:
        warnings = []

        if not code:
            warnings.append("Generated code is empty")
            return warnings

        if "class " not in code:
            warnings.append("Missing adapter class definition")

        if "def " not in code:
            warnings.append("Missing method definition")

        if (
            not code.strip().endswith(")")
            and not code.strip().endswith(":")
            and not code.strip().endswith('"')
            and not code.strip().endswith("'")
        ):
            warnings.append("Code may be truncated")

        return warnings


import re

adapter_agent = AdapterAgent()
