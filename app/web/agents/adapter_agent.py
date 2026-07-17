from __future__ import annotations

import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config.settings import settings

_MODEL_PATH = Path(__file__).resolve().parents[3] / "models" / "Qwen" / "2.5-0.5B-Instruct"


@dataclass
class AdapterGenerationResult:
    adapter_code: str
    template_name: str
    template_yaml: str
    validation_errors: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


class LocalQwenModel:
    def __init__(self, model_path: Path | None = None) -> None:
        self._model_path = model_path or _MODEL_PATH
        self._model: Any = None
        self._tokenizer: Any = None
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            if not (self._model_path / "model.safetensors").is_file():
                raise RuntimeError(f"Local Qwen model is incomplete: {self._model_path}")
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                str(self._model_path), local_files_only=True
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                str(self._model_path),
                local_files_only=True,
                dtype="auto",
                low_cpu_mem_usage=True,
            )
            self._model.eval()
            self._model.generation_config.do_sample = False
            self._model.generation_config.temperature = None
            self._model.generation_config.top_p = None
            self._model.generation_config.top_k = None

    async def generate(self, prompt: str, max_tokens: int = 4096, temperature: float = 0.1, do_sample: bool = False) -> str:
        import torch

        self._load()

        encoded = self._tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)
        with torch.inference_mode():
            if do_sample:
                outputs = self._model.generate(
                    **encoded,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    num_return_sequences=1,
                    do_sample=True,
                )
            else:
                outputs = self._model.generate(
                    **encoded,
                    max_new_tokens=max_tokens,
                    num_return_sequences=1,
                    do_sample=False,
                )

        response = self._tokenizer.decode(outputs[0], skip_special_tokens=True)
        return response[len(prompt) :] if prompt in response else response


class AdapterAgent:
    _ALLOWED_IMPORTS = {
        "__future__",
        "typing",
        "urllib.parse",
        "lxml",
        "app.adapters",
        "app.models",
        "re",
        "json",
    }

    def __init__(self) -> None:
        self._model = LocalQwenModel()
        self._prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        return """你是一个专业的爬虫适配器开发专家。请根据以下模板生成Python适配器代码。

模板名称: {template_name}
模板内容:
{template_yaml}

请生成一个Python适配器类，继承自BaseSiteAdapter，包含以下要求：

1. 类名格式：{camel_name}Adapter
2. 使用@register_adapter装饰器注册
3. 实现on_after_page方法处理每页的数据解析
4. 使用lxml进行HTML解析
5. 为模板中每个list_fields字段实现提取逻辑
6. 处理异常情况，避免单个字段提取失败影响整体
7. 代码格式规范，使用PEP 8风格

输出格式要求：
```python
from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from app.adapters import BaseSiteAdapter, register_adapter

@register_adapter("{template_name}")
class {camel_name}Adapter(BaseSiteAdapter):
    adapter_name = "{template_name}"

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        parsed: list[dict] = []
        for record in records:
            item_html = record.get("item_html")
            if not item_html:
                continue
            root = lxml_html.fromstring(item_html)
            item: dict[str, Any] = {{}}
            
            # 提取字段逻辑
            title_node = root.cssselect("标题选择器")
            if title_node:
                item["title"] = title_node[0].text_content().strip()
            
            # 提取其他字段...
            
            parsed.append(item)
        return parsed
```

请确保输出的Python代码格式正确，不要包含其他文字。
"""

    async def generate_adapter(self, template_name: str, template_yaml: str) -> AdapterGenerationResult:
        camel_name = "".join(part.capitalize() for part in template_name.split("_"))

        prompt = self._prompt_template.format(
            template_name=template_name,
            template_yaml=template_yaml,
            camel_name=camel_name,
        )

        response = await self._model.generate(prompt)
        code = self._extract_code(response)
        validation_errors = self._validate_adapter(code)
        suggestions = self._generate_suggestions(template_yaml, validation_errors)

        return AdapterGenerationResult(
            adapter_code=code,
            template_name=template_name,
            template_yaml=template_yaml,
            validation_errors=validation_errors,
            suggestions=suggestions,
        )

    async def refine_adapter(self, adapter_code: str, template_yaml: str, feedback: str) -> AdapterGenerationResult:
        template_name = self._extract_template_name(template_yaml)
        camel_name = "".join(part.capitalize() for part in template_name.split("_"))

        refine_prompt = f"""你是一个专业的爬虫适配器开发专家。请根据以下反馈优化适配器代码。

原始适配器代码:
{adapter_code}

模板内容:
{template_yaml}

优化反馈:
{feedback}

请根据反馈修改适配器代码，确保：
1. 修复指出的问题
2. 保持代码格式规范
3. 不引入新的错误

输出格式要求：
```python
# 优化后的适配器代码
```

请确保输出的Python代码格式正确，不要包含其他文字。
"""

        response = await self._model.generate(refine_prompt)
        code = self._extract_code(response)
        validation_errors = self._validate_adapter(code)

        return AdapterGenerationResult(
            adapter_code=code,
            template_name=template_name,
            template_yaml=template_yaml,
            validation_errors=validation_errors,
            suggestions=[],
        )

    def _extract_code(self, response: str) -> str:
        python_match = re.search(r"```python\s*([\s\S]*?)\s*```", response)
        if python_match:
            return python_match.group(1)
        return response.strip()

    def _validate_adapter(self, code: str) -> list[str]:
        errors = []

        if "@register_adapter" not in code:
            errors.append("缺少@register_adapter装饰器")

        if "async def on_after_page" not in code:
            errors.append("缺少on_after_page方法")

        if "BaseSiteAdapter" not in code:
            errors.append("未继承BaseSiteAdapter")

        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            if "import" in line:
                parts = line.replace("from", " ").replace("import", " ").split()
                for part in parts:
                    if part and part not in self._ALLOWED_IMPORTS and not part.startswith("."):
                        errors.append(f"第{i}行: 禁止导入 {part}")

        if len(lines) > settings.llm_max_tokens // 4:
            errors.append(f"代码行数超过限制 ({len(lines)} 行)")

        return errors

    def _generate_suggestions(self, template_yaml: str, validation_errors: list[str]) -> list[str]:
        suggestions = []

        if "download" in template_yaml.lower():
            suggestions.append("建议添加资源下载字段的处理逻辑")

        if "pagination" in template_yaml.lower() and "next_page" not in template_yaml.lower():
            suggestions.append("建议检查分页逻辑是否完整")

        if "dedup" in template_yaml.lower():
            suggestions.append("建议在适配器中实现去重逻辑")

        if not validation_errors:
            suggestions.append("代码验证通过，可以进行测试")

        return suggestions

    @staticmethod
    def _extract_template_name(template_yaml: str) -> str:
        name_match = re.search(r"name:\s*(\S+)", template_yaml)
        return name_match.group(1) if name_match else "unknown_adapter"


adapter_agent = AdapterAgent()