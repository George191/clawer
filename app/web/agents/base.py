from __future__ import annotations

import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict

from app.logger import get_logger

logger = get_logger(__name__)


class BaseAgent:
    def __init__(self, model_path: str | None = None):
        self._model_path = model_path or str(
            Path(__file__).parent.parent.parent.parent
            / "models"
            / "Qwen"
            / "2.5-0.5B-Instruct"
        )
        self._model = None
        self._tokenizer = None
        self._torch = None
        self._model_lock = asyncio.Lock()
        self._prompts: Dict[str, str] = {}

    @property
    def model_name(self) -> str:
        return Path(self._model_path).name

    async def _ensure_model(self) -> None:
        if self._model is not None:
            return
        async with self._model_lock:
            if self._model is None:
                await asyncio.to_thread(self._load_model)

    def _load_model(self) -> None:
        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "Local AI model dependencies are unavailable; install torch and transformers"
            ) from exc

        logger.info("Loading model from: %s", self._model_path)
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_path, trust_remote_code=True)
        self._model = AutoModelForCausalLM.from_pretrained(
            self._model_path,
            device_map="auto",
            trust_remote_code=True,
            dtype=torch.float16,
        )
        self._torch = torch
        self._model.generation_config.do_sample = False
        self._model.generation_config.temperature = None
        self._model.generation_config.top_p = None
        self._model.generation_config.top_k = None
        logger.info("Model loaded successfully")

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
        await self._ensure_model()

        model_input = self._tokenizer.apply_chat_template(
            [{"role": "user", "content": prompt}],
            tokenize=False,
            add_generation_prompt=True,
        )
        encoded = self._tokenizer(
            model_input,
            return_tensors="pt",
            truncation=True,
            max_length=4096,
        )
        encoded = {k: v.to(self._model.device) for k, v in encoded.items()}
        input_length = encoded["input_ids"].shape[-1]
        
        if on_chunk is None:
            with self._torch.inference_mode():
                outputs = self._model.generate(
                    **encoded,
                    max_new_tokens=max_tokens,
                    num_return_sequences=1,
                    do_sample=False,
                )

            return self._tokenizer.decode(outputs[0][input_length:], skip_special_tokens=True).strip()

        from transformers import TextIteratorStreamer

        streamer = TextIteratorStreamer(
            self._tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
        )
        generation_errors: list[BaseException] = []

        def run_generation() -> None:
            try:
                with self._torch.inference_mode():
                    self._model.generate(
                        **encoded,
                        streamer=streamer,
                        max_new_tokens=max_tokens,
                        num_return_sequences=1,
                        do_sample=False,
                    )
            except BaseException as exc:
                generation_errors.append(exc)
                streamer.text_queue.put(streamer.stop_signal)

        generation_task = asyncio.create_task(asyncio.to_thread(run_generation))
        chunks: list[str] = []
        while True:
            finished, chunk = await asyncio.to_thread(self._next_stream_chunk, streamer)
            if finished:
                break
            if chunk:
                chunks.append(chunk)
                on_chunk(chunk)

        await generation_task
        if generation_errors:
            raise generation_errors[0]
        return "".join(chunks).strip()

    @staticmethod
    def _next_stream_chunk(streamer: Any) -> tuple[bool, str]:
        try:
            return False, next(streamer)
        except StopIteration:
            return True, ""

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
        
        logger.error("JSON generation failed after %d attempts", max_retries)
        return {"data_type": "list", "fields": [], "pagination": {"type": "none"}, "api_endpoints": [], "download_fields": [], "dedup_fields": [], "description": ""}

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
