"""AI 智能层配置模块。

支持 OpenAI / Claude 等多 LLM 提供商切换，以及分析超时、重试等通用配置。
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProvider(str, Enum):
    """支持的 LLM 提供商。"""

    OPENAI = "openai"
    CLAUDE = "claude"
    CUSTOM = "custom"


class AISettings(BaseSettings):
    """AI 智能层配置。

    通过环境变量 `SPIDER_AI_*` 前缀注入，也可在 `.env` 中定义。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="SPIDER_AI_",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM Provider ──
    llm_provider: LLMProvider = Field(
        default=LLMProvider.OPENAI,
        description="LLM 提供商: openai / claude / custom",
    )

    # ── OpenAI / 兼容 API ──
    openai_api_key: str = Field(default="", description="OpenAI API Key")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1",
        description="OpenAI 兼容 API Base URL",
    )
    openai_model: str = Field(default="gpt-4o", description="使用的模型名")

    # ── Claude / Anthropic ──
    claude_api_key: str = Field(default="", description="Anthropic API Key")
    claude_base_url: str = Field(
        default="https://api.anthropic.com",
        description="Anthropic API Base URL",
    )
    claude_model: str = Field(
        default="claude-sonnet-4-20250514",
        description="Claude 模型名",
    )

    # ── Custom Provider ──
    custom_api_key: str = Field(default="", description="自定义 API Key")
    custom_base_url: str = Field(default="", description="自定义 API Base URL")
    custom_model: str = Field(default="", description="自定义模型名")

    # ── 功能开关 ──
    ai_enabled: bool = Field(
        default=True,
        description="是否启用 AI 智能层功能（关闭后使用纯规则引擎）",
    )
    llm_pagination_detection: bool = Field(
        default=True,
        description="是否使用 LLM 辅助翻页检测（关闭后用纯规则引擎）",
    )
    llm_template_generation: bool = Field(
        default=True,
        description="是否使用 LLM 生成模板（关闭后只做规则分析）",
    )
    llm_structure_analysis: bool = Field(
        default=True,
        description="是否使用 LLM 辅助页面结构分析",
    )

    # ── 超时 & 重试 ──
    llm_request_timeout: float = Field(
        default=120.0,
        description="LLM API 请求超时（秒）",
    )
    llm_max_retries: int = Field(
        default=2,
        description="LLM API 请求最大重试次数",
    )
    llm_retry_backoff: float = Field(
        default=2.0,
        description="LLM 请求重试退避因子（秒）",
    )

    # ── 分析参数 ──
    page_fetch_timeout: float = Field(
        default=30.0,
        description="页面抓取超时（秒）",
    )
    max_html_chars_for_llm: int = Field(
        default=30000,
        description="发送给 LLM 的 HTML 片段最大字符数（控制 token 成本）",
    )
    max_list_items_for_analysis: int = Field(
        default=20,
        description="结构分析时最多取多少条列表项（避免 token 爆炸）",
    )

    # ── 缓存 ──
    analysis_cache_enabled: bool = Field(
        default=True,
        description="是否启用分析结果缓存（避免重复调用 LLM）",
    )
    analysis_cache_ttl: int = Field(
        default=3600,
        description="分析结果缓存 TTL（秒）",
    )

    # ── 便捷属性 ──
    @property
    def active_api_key(self) -> str:
        """根据当前 provider 返回对应的 API Key。"""
        key_map = {
            LLMProvider.OPENAI: self.openai_api_key,
            LLMProvider.CLAUDE: self.claude_api_key,
            LLMProvider.CUSTOM: self.custom_api_key,
        }
        return key_map[self.llm_provider]

    @property
    def active_base_url(self) -> str:
        """根据当前 provider 返回对应的 Base URL。"""
        url_map = {
            LLMProvider.OPENAI: self.openai_base_url,
            LLMProvider.CLAUDE: self.claude_base_url,
            LLMProvider.CUSTOM: self.custom_base_url,
        }
        return url_map[self.llm_provider]

    @property
    def active_model(self) -> str:
        """根据当前 provider 返回对应的模型名。"""
        model_map = {
            LLMProvider.OPENAI: self.openai_model,
            LLMProvider.CLAUDE: self.claude_model,
            LLMProvider.CUSTOM: self.custom_model,
        }
        return model_map[self.llm_provider]


# 单例
ai_settings = AISettings()