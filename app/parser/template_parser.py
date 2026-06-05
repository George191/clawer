"""模板解析器 — 将 HTML / JSON 页面内容按 FieldMapping 配置解析为结构化记录。

支持 CSS / XPath / Regex / JSON 四种选择器，以及 TEXT / ATTR / HTML / HREF / SRC 等字段类型。
内置 transform 注册机制，可自定义 strip / int / date_parse 等后处理函数。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from lxml import etree, html as lxml_html

from app.models.template import (
    FieldMapping,
    FieldType,
    SelectorType,
)

logger = logging.getLogger(__name__)

_TRANSFORM_REGISTRY: dict[str, Any] = {}


def register_transform(name: str):
    def decorator(func):
        _TRANSFORM_REGISTRY[name] = func
        return func
    return decorator


@register_transform("strip")
def _strip(value: str) -> str:
    return value.strip()


@register_transform("int")
def _to_int(value: str) -> int:
    return int(str(value).strip().replace(",", ""))


@register_transform("float")
def _to_float(value: str) -> float:
    return float(str(value).strip().replace(",", ""))


@register_transform("lower")
def _to_lower(value: str) -> str:
    return str(value).strip().lower()


@register_transform("upper")
def _to_upper(value: str) -> str:
    return str(value).strip().upper()


def apply_transform(value: Any, transform_name: str) -> Any:
    func = _TRANSFORM_REGISTRY.get(transform_name)
    if func is None:
        raise ValueError(f"Unknown transform: {transform_name}")
    return func(value)


def resolve_json_path(data: Any, path: str) -> Any:
    parts = re.split(r'\.|\[|\]', path)
    parts = [p for p in parts if p]
    value = data
    for part in parts:
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list):
            if part.isdigit():
                idx = int(part)
                value = value[idx] if idx < len(value) else None
            else:
                return None
        else:
            return None
    return value


class TemplateParser:
    def parse_list(self, html_content: str, fields: list[FieldMapping]) -> list[dict[str, Any]]:
        tree = lxml_html.fromstring(html_content)
        row_count = self._detect_row_count(tree, fields)
        if row_count == 0:
            logger.warning("No rows detected from list page")
            return []

        results: list[dict[str, Any]] = []
        for i in range(row_count):
            row = self._extract_row(tree, fields, row_index=i)
            if row is not None:
                results.append(row)
        return results

    def parse_list_json(
        self,
        json_data: dict[str, Any],
        item_path: str,
        fields: list[FieldMapping],
    ) -> list[dict[str, Any]]:
        items = resolve_json_path(json_data, item_path)
        if items is None:
            logger.warning("JSON item path '%s' returned None", item_path)
            return []
        if not isinstance(items, list):
            items = [items]

        results: list[dict[str, Any]] = []
        for item in items:
            row = self._extract_json_row(item, fields)
            if row is not None:
                results.append(row)
        return results

    def parse_detail(self, html_content: str, fields: list[FieldMapping]) -> dict[str, Any]:
        tree = lxml_html.fromstring(html_content)
        return self._extract_row(tree, fields, row_index=None) or {}

    def parse_detail_json(self, json_data: dict[str, Any], fields: list[FieldMapping]) -> dict[str, Any]:
        return self._extract_json_row(json_data, fields) or {}

    def extract_links(
        self,
        html_content: str,
        selector: str,
        selector_type: SelectorType,
    ) -> list[str]:
        tree = lxml_html.fromstring(html_content)
        elements = self._select_elements(tree, selector, selector_type)
        links: list[str] = []
        for el in elements:
            href = el.get("href") or el.get("src", "")
            if href:
                links.append(href.strip())
        return links

    def extract_download_url(
        self,
        html_content: str,
        selector: str,
        selector_type: SelectorType,
        link_type: FieldType,
    ) -> str | None:
        tree = lxml_html.fromstring(html_content)
        elements = self._select_elements(tree, selector, selector_type)
        if not elements:
            return None
        el = elements[0]
        if link_type == FieldType.HREF:
            return el.get("href", "").strip()
        if link_type == FieldType.SRC:
            return el.get("src", "").strip()
        if link_type == FieldType.ATTR:
            return el.text_content().strip() if el.text else None
        return None

    def extract_download_url_json(
        self,
        json_data: dict[str, Any],
        json_path: str,
    ) -> str | None:
        value = resolve_json_path(json_data, json_path)
        if value is None:
            return None
        return str(value)

    def _detect_row_count(self, tree, fields: list[FieldMapping]) -> int:
        for field in fields:
            if field.selector_type == SelectorType.CSS:
                elements = tree.cssselect(field.selector)
            elif field.selector_type == SelectorType.XPATH:
                elements = tree.xpath(field.selector)
            else:
                continue
            if elements:
                return len(elements)
        return 0

    def _extract_row(
        self,
        tree,
        fields: list[FieldMapping],
        row_index: int | None = None,
    ) -> dict[str, Any] | None:
        row: dict[str, Any] = {}
        has_required_data = False
        has_any_data = False

        for field in fields:
            try:
                value = self._extract_field_value(tree, field, row_index)
            except Exception as e:
                logger.warning("Failed to extract field '%s': %s", field.name, e)
                value = field.default

            if value is None:
                if field.required and field.default is None:
                    logger.debug("Required field '%s' is missing, skipping row", field.name)
                    return None
                value = field.default

            if value is not None:
                has_any_data = True

            if field.required and value is not None:
                has_required_data = True

            if field.transform and value is not None:
                try:
                    value = apply_transform(value, field.transform)
                except Exception as e:
                    logger.warning("Transform '%s' failed for field '%s': %s", field.transform, field.name, e)

            row[field.name] = value

        if not has_required_data and not has_any_data:
            return None
        return row

    def _extract_json_row(
        self,
        item: Any,
        fields: list[FieldMapping],
    ) -> dict[str, Any] | None:
        row: dict[str, Any] = {}
        has_required_data = False
        has_any_data = False

        _TEXT_TYPES = {FieldType.TEXT, FieldType.ATTR, FieldType.HTML, FieldType.HREF, FieldType.SRC}

        for field in fields:
            try:
                value = resolve_json_path(item, field.selector)
                if value is not None and field.field_type in _TEXT_TYPES:
                    value = str(value)
            except Exception as e:
                logger.warning("Failed to extract JSON field '%s': %s", field.name, e)
                value = field.default

            if value is None:
                if field.required and field.default is None:
                    logger.warning("Required JSON field '%s' is missing, skipping row", field.name)
                    return None
                value = field.default

            if value is not None:
                has_any_data = True

            if field.required and value is not None:
                has_required_data = True

            if field.transform and value is not None:
                try:
                    value = apply_transform(value, field.transform)
                except Exception as e:
                    logger.warning("Transform '%s' failed for field '%s': %s", field.transform, field.name, e)

            row[field.name] = value

        if not has_required_data and not has_any_data:
            return None
        return row

    def _extract_field_value(
        self,
        tree,
        field: FieldMapping,
        row_index: int | None,
    ) -> str | None:
        if field.selector_type == SelectorType.CSS:
            elements = tree.cssselect(field.selector)
        elif field.selector_type == SelectorType.XPATH:
            elements = tree.xpath(field.selector)
        elif field.selector_type == SelectorType.REGEX:
            page_text = etree.tostring(tree, encoding="unicode", method="html")
            match = re.search(field.selector, page_text)
            return match.group(1) if match and match.lastindex else (match.group(0) if match else None)
        elif field.selector_type == SelectorType.JSON:
            return self._extract_from_json(tree, field)
        else:
            return None

        if not elements:
            return None

        target = elements[row_index] if row_index is not None and row_index < len(elements) else elements[0]
        if target is None:
            return None

        return self._extract_by_field_type(target, field)

    def _extract_by_field_type(self, element, field: FieldMapping) -> str | None:
        if field.field_type == FieldType.TEXT:
            return element.text_content().strip() if hasattr(element, "text_content") else str(element).strip()
        elif field.field_type == FieldType.ATTR:
            attr = field.attr_name or "href"
            return element.get(attr, "").strip() if hasattr(element, "get") else None
        elif field.field_type == FieldType.HREF:
            return element.get("href", "").strip() if hasattr(element, "get") else None
        elif field.field_type == FieldType.SRC:
            return element.get("src", "").strip() if hasattr(element, "get") else None
        elif field.field_type == FieldType.HTML:
            return etree.tostring(element, encoding="unicode", method="html") if hasattr(element, "tag") else None
        return None

    def _extract_from_json(self, tree, field: FieldMapping) -> str | None:
        try:
            script_elements = tree.cssselect("script[type='application/json']")
            if not script_elements:
                script_elements = tree.xpath("//script[contains(text(), '{')]")
            for script in script_elements:
                try:
                    data = json.loads(script.text_content())
                    value = resolve_json_path(data, field.selector)
                    if value is not None:
                        return str(value)
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
        except Exception as e:
            logger.warning("JSON extraction failed for field '%s': %s", field.name, e)
        return None

    @staticmethod
    def _select_elements(tree, selector: str, selector_type: SelectorType) -> list:
        if selector_type == SelectorType.CSS:
            return tree.cssselect(selector)
        elif selector_type == SelectorType.XPATH:
            result = tree.xpath(selector)
            return result if isinstance(result, list) else [result]
        return []
