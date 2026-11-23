from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable, Dict

from app.ai.gateway import complete
from app.config.ai_settings import ai_settings
from app.logger import get_logger

logger = get_logger(__name__)


class BaseAgent:
    def __init__(self):
        self._prompts: Dict[str, str] = {}

    @property
    def model_name(self) -> str:
        return ai_settings.scout_model

    def register_prompt(self, name: str, prompt: str) -> None:
        self._prompts[name] = prompt

    def get_prompt(self, name: str) -> str:
        return self._prompts.get(name, "")

    def render_prompt(self, name: str, **kwargs: Any) -> str:
        prompt = self.get_prompt(name)
        return prompt.format(**kwargs)

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 8192,
        on_chunk: Callable[[str], None] | None = None,
    ) -> str:
        return await complete(
            [{"role": "user", "content": prompt}],
            self.model_name,
            max_tokens=max_tokens,
            on_chunk=on_chunk,
        )

    async def generate_complete_json(
        self,
        prompt: str,
        max_retries: int = 3,
        max_tokens: int = 3072,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        for attempt in range(max_retries):
            response = await self.generate(prompt, max_tokens=max_tokens)
            if on_event:
                on_event({
                    "kind": "output",
                    "attempt": attempt + 1,
                    "content": response[:4000],
                })
            parsed = self._parse_json(response)
            
            if parsed and self._is_valid_json_structure(parsed):
                logger.info("JSON generation succeeded on attempt %d", attempt + 1)
                return parsed
            
            logger.warning("JSON generation attempt %d/%d failed, retrying...", attempt + 1, max_retries)
            if on_event and attempt < max_retries - 1:
                on_event({"kind": "retry", "attempt": attempt + 2, "reason": "invalid_model_output"})
            await asyncio.sleep(1)
        
        raise ValueError(f"Model output failed JSON validation after {max_retries} attempts")

    def _is_valid_json_structure(self, data: Dict[str, Any]) -> bool:
        if data.get("_parse_failed"):
            return False
        required_keys = ["data_type", "fields", "pagination"]
        for key in required_keys:
            if key not in data:
                return False
        
        if not isinstance(data.get("fields"), list):
            return False
        
        if not isinstance(data.get("pagination"), dict):
            return False
        
        pagination_type = data["pagination"].get("type")
        if pagination_type not in ["none", "page_number", "scroll", "next_button"]:
            return False
        
        return True

    async def generate_json(self, prompt: str, max_tokens: int = 8192) -> Dict[str, Any]:
        response = await self.generate(prompt, max_tokens)
        print(response)
        return self._parse_json(response)

    def _parse_json(self, text: str) -> Dict[str, Any]:
        match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
        if match:
            text = match.group(1)
        
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            text = match.group(0)
        
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.error("Failed to parse JSON, trying to fix: %s", text[:500])
            fixed = self._fix_incomplete_json(text)
            try:
                return json.loads(fixed)
            except json.JSONDecodeError:
                logger.error("Failed to fix JSON, returning default")
                return {"_parse_failed": True}

    def _fix_incomplete_json(self, text: str) -> str:
        text = text.strip()
        if not text.startswith('{'):
            text = '{' + text
        
        lines = text.split('\n')
        fixed_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped == '':
                fixed_lines.append(line)
                continue
            
            if re.match(r'^\s*"(\w+)"\s*$', stripped):
                fixed_lines.append(line.rstrip() + ': ""')
            elif re.match(r'^\s*"(\w+)"\s*:\s*$', stripped):
                fixed_lines.append(line.rstrip() + ' ""')
            else:
                fixed_lines.append(line)
        
        text = '\n'.join(fixed_lines)
        
        brace_count = text.count('{') - text.count('}')
        if brace_count > 0:
            text += '}' * brace_count
        
        bracket_count = text.count('[') - text.count(']')
        if bracket_count > 0:
            text += ']' * bracket_count
        
        return text

    async def analyze(self, url: str, html_text: str, **kwargs: Any) -> Dict[str, Any]:
        raise NotImplementedError
