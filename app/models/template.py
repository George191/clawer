"""模板配置模型 — 定义网站采集模板的数据结构。

包含请求配置、字段映射、分页配置、下载配置等 Pydantic 模型，
以及 SiteTemplate 核心模型，支持 URL 参数替换、编码、分页等。
"""

from __future__ import annotations

import random
import uuid
from enum import Enum
from typing import Any
from urllib.parse import urlparse, urlunparse, quote

from pydantic import BaseModel, Field, HttpUrl


class ResponseType(str, Enum):
    HTML = "html"
    JSON = "json"


class SelectorType(str, Enum):
    CSS = "css"
    XPATH = "xpath"
    REGEX = "regex"
    JSON = "json"


class FieldType(str, Enum):
    TEXT = "text"
    ATTR = "attr"
    HTML = "html"
    HREF = "href"
    SRC = "src"
    JSON = "json"
    NUMBER = "number"
    BOOLEAN = "boolean"


class PaginationType(str, Enum):
    NEXT_PAGE = "next_page"
    PAGE_NUMBER = "page_number"
    LOAD_MORE = "load_more"
    INFINITE_SCROLL = "infinite_scroll"


class FieldMapping(BaseModel):
    name: str = Field(description="字段名称, 如 title, contract_no, patent_id")
    selector: str = Field(description="选择器表达式")
    selector_type: SelectorType = Field(default=SelectorType.CSS, description="选择器类型")
    field_type: FieldType = Field(default=FieldType.TEXT, description="字段提取方式")
    attr_name: str | None = Field(default=None, description="当field_type=attr时, 要提取的属性名")
    required: bool = Field(default=True, description="是否为必填字段")
    default: Any = Field(default=None, description="字段缺失时的默认值")
    transform: str | None = Field(
        default=None,
        description="后处理函数名, 如 strip, int, date_parse 等",
    )


class PaginationConfig(BaseModel):
    type: PaginationType = Field(description="分页类型")
    next_selector: str | None = Field(default=None, description="下一页链接选择器")
    page_param: str | None = Field(default=None, description="页码参数名, 如 page")
    start_page: int = Field(default=1, description="起始页码")
    max_pages: int = Field(default=1000, description="最大翻页数(安全上限), 0=不限")
    results_per_page: int = Field(default=10, description="每页结果数, 用于从 total 动态计算页数")


class DownloadConfig(BaseModel):
    selector: str = Field(description="下载链接选择器或JSON路径")
    selector_type: SelectorType = Field(default=SelectorType.CSS)
    link_type: FieldType = Field(default=FieldType.HREF, description="链接所在属性")
    file_extension: str | None = Field(default=None, description="强制文件扩展名, 如 pdf, docx")
    filename_selector: str | None = Field(default=None, description="文件名选择器, 留空则从URL推断")
    filename_selector_type: SelectorType = Field(default=SelectorType.CSS)
    url_prefix: str | None = Field(
        default=None,
        description="下载URL前缀, 如 https://patentimages.storage.googleapis.com/",
    )


class RequestConfig(BaseModel):
    method: str = Field(default="GET", description="请求方法")
    headers: dict[str, str] = Field(default_factory=dict, description="额外请求头")
    params: dict[str, str] = Field(default_factory=dict, description="URL查询参数")
    form_data: dict[str, str] = Field(default_factory=dict, description="POST表单数据")
    cookies: dict[str, str] = Field(default_factory=dict, description="请求Cookies")
    encoding: str | None = Field(default=None, description="页面编码, 留空则自动检测")


class TemplateParam(BaseModel):
    name: str = Field(description="参数名, 如 assignee, keyword")
    description: str = Field(default="", description="参数描述")
    default: str | None = Field(default=None, description="默认值, 无默认值则必须传入")
    required: bool = Field(default=True, description="是否必须传入")


class BatchParamConfig(BaseModel):
    """批量参数配置（在模板 YAML 中定义）。"""

    file_path: str = Field(description="批量参数文件路径，每行一个参数值")
    param_name: str = Field(description="参数名称，如 publication_number")
    start_line: int = Field(
        default=0, description="起始行号（0-based）"
    )
    limit: int | None = Field(
        default=None, description="最大处理数量，None表示不限制"
    )
    delay: float = Field(
        default=1.5, description="每个请求之间的延迟（秒）"
    )


class PreHookConfig(BaseModel):
    """预处理钩子配置（在模板 YAML 中定义）。"""

    name: str = Field(description="钩子名称，需与代码中注册的钩子名称一致")
    description: str = Field(default="", description="钩子描述")
    args: dict[str, Any] = Field(
        default_factory=dict, description="传递给钩子的参数"
    )


class SiteTemplate(BaseModel):
    name: str = Field(description="模板唯一标识, 如 google_patent")
    display_name: str = Field(default="", description="模板显示名称")
    base_url: str = Field(description="网站基础URL")
    data_type: str = Field(description="数据类型: contract / patent / other")
    description: str = Field(default="", description="模板描述")
    priority: int = Field(
        default=50,
        description="采集优先级 (0=最高, 100=最低, 默认50=普通)",
    )
    adapter: str | None = Field(
        default=None,
        description="站点适配器名称, 如 google_patent, generic。留空则自动检测",
    )

    anti_crawl_enabled: bool | None = Field(
        default=None,
        description=(
            "是否为此模板启用反爬功能（代理池、请求延迟、身份轮换等）。"
            "None 表示回退到全局配置 SPIDER_ANTI_CRAWL_ENABLED，"
            "布尔值则覆盖全局配置。优先级：模板 > 全局 > False"
        ),
    )

    pre_hooks: list[PreHookConfig] = Field(
        default_factory=list,
        description="预处理钩子列表，在请求列表页之前按顺序执行",
    )

    params: list[TemplateParam] = Field(
        default_factory=list,
        description="模板参数定义, 可在 list_page/detail_page 等字段中用 {param_name} 引用",
    )

    response_type: ResponseType = Field(
        default=ResponseType.HTML,
        description="响应类型: html(解析HTML) / json(解析JSON API)",
    )
    json_item_path: str | None = Field(
        default=None,
        description="JSON响应中数据列表的路径, 如 results.cluster[0].result",
    )
    json_total_path: str | None = Field(
        default=None,
        description="JSON响应中总结果数的路径, 如 results.total_num_results",
    )
    json_page_path: str | None = Field(
        default=None,
        description="JSON响应中当前页码的路径, 如 results.num_page",
    )
    json_total_num_pages: str | None = Field(
        default=None,
        description="JSON响应中 API 允许的最大页数路径（优先于 total/per_page），"
        "如 results.total_num_pages。Google Patents 即使 total=13806 也只允许翻 10 页",
    )

    list_page: str = Field(description="列表页URL路径, 支持 {page} 和 {param_name} 占位符")
    list_request: RequestConfig = Field(default_factory=RequestConfig, description="列表页请求配置")
    list_fields: list[FieldMapping] = Field(description="列表页字段映射")
    list_pagination: PaginationConfig | None = Field(default=None, description="分页配置")

    detail_page: str | None = Field(
        default=None,
        description="详情页URL路径模板, 支持 {id} 和 {param_name} 占位符",
    )
    detail_url_selector: str | None = Field(
        default=None,
        description="从列表页提取详情页链接的选择器",
    )
    detail_url_selector_type: SelectorType = Field(default=SelectorType.CSS)
    detail_request: RequestConfig = Field(default_factory=RequestConfig, description="详情页请求配置")
    detail_fields: list[FieldMapping] = Field(default_factory=list, description="详情页字段映射")

    download: DownloadConfig | None = Field(default=None, description="文件下载配置")

    batch_params: BatchParamConfig | None = Field(
        default=None, description="批量参数配置，用于从本地文件读取参数"
    )

    _param_values: dict[str, str] = {}

    @property
    def effective_anti_crawl_enabled(self) -> bool:
        """解析反爬功能的最终启用状态。

        优先级：模板字段 > 全局配置 SPIDER_ANTI_CRAWL_ENABLED > False。

        Returns:
            True 如果反爬功能应启用，否则 False。
        """
        if self.anti_crawl_enabled is not None:
            return self.anti_crawl_enabled
        from app.config.settings import settings
        return settings.anti_crawl_enabled

    @staticmethod
    def _replace_params(text: str, params: dict[str, str]) -> str:
        for key, value in params.items():
            text = text.replace("{" + key + "}", value)
        return text

    def apply_params(self, param_values: dict[str, str] | None = None) -> SiteTemplate:
        merged: dict[str, str] = {}
        for p in self.params:
            if param_values and p.name in param_values:
                merged[p.name] = param_values[p.name]
            elif p.default is not None:
                merged[p.name] = p.default
            elif p.required:
                raise ValueError(f"Template '{self.name}' requires param '{p.name}' but no value provided")

        self._param_values = merged

        if merged:
            self.list_page = self._replace_params(self.list_page, merged)
            if self.detail_page:
                self.detail_page = self._replace_params(self.detail_page, merged)
            if self.display_name:
                self.display_name = self._replace_params(self.display_name, merged)
            if self.description:
                self.description = self._replace_params(self.description, merged)
            self._apply_params_to_request(self.list_request, merged)
            self._apply_params_to_request(self.detail_request, merged)

        return self

    def _apply_params_to_request(self, request: RequestConfig, params: dict[str, str]) -> None:
        for key, value in list(request.headers.items()):
            new_value = self._replace_params(value, params)
            if new_value != value:
                request.headers[key] = new_value
        for key, value in list(request.params.items()):
            new_value = self._replace_params(value, params)
            if new_value != value:
                request.params[key] = new_value

    def get_full_list_url(self, page: int = 1, num: int = 100, peid: str | None = None) -> str:
        url = f"{self.base_url}{self.list_page}"

        if "{peid}" in url and peid is None:
            p1 = uuid.uuid4().hex[:12]
            p2 = hex(random.randint(0, 0xFFF))[2:]
            p3 = uuid.uuid4().hex[:8]
            peid = f"{p1}:{p2}:{p3}"

        url = url.replace("{page}", str(page)).replace("{peid}", peid or "").replace("{num}", str(num))
        return self._encode_url(url)

    @staticmethod
    def _encode_url(url: str) -> str:

        parsed = urlparse(url)
        if not parsed.query:
            return url

        query = parsed.query

        if query.startswith("url="):
            top_level_keys = {"exp", "expid", "tags", "peid", "pa", "referer"}
            rest_start = -1
            for key in top_level_keys:
                sep = f"&{key}="
                idx = query.find(sep)
                if idx >= 0 and (rest_start < 0 or idx < rest_start):
                    rest_start = idx

            if rest_start >= 0:
                nested_value = query[4:rest_start]
                rest_params = query[rest_start + 1:]
            else:
                nested_value = query[4:]
                rest_params = ""

            encoded_nested = quote(nested_value, safe="")

            encoded_rest_parts = []
            for pair in rest_params.split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    encoded_rest_parts.append(f"{key}={quote(value, safe='')}")
                elif pair:
                    encoded_rest_parts.append(quote(pair, safe=""))
            encoded_rest = "&".join(encoded_rest_parts)

            encoded_query = f"url={encoded_nested}"
            if encoded_rest:
                encoded_query = f"{encoded_query}&{encoded_rest}"
            return urlunparse(parsed._replace(query=encoded_query))

        encoded_parts = []
        for pair in query.split("&"):
            if "=" in pair:
                key, value = pair.split("=", 1)
                encoded_parts.append(f"{key}={quote(value, safe='')}")
            elif pair:
                encoded_parts.append(quote(pair, safe=""))
        encoded_query = "&".join(encoded_parts)
        return urlunparse(parsed._replace(query=encoded_query))

    def get_full_detail_url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        return f"{self.base_url}{path}"
