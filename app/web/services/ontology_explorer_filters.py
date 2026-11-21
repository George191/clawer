"""Compile the common Explorer filter expression against authorized semantic fields."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import operator
import re

from fastapi import HTTPException
from sqlalchemy import Boolean, Date, DateTime, Numeric, and_, cast, false, or_, type_coerce
from sqlalchemy.dialects.postgresql import JSONB

from app.web.api.models.ontology_runtime import OntologyRuntimeRecord as Record
from app.web.services.ontology_runtime_values import check_record_json

OPERATORS = {"=", "!=", "<", "<=", ">", ">=", "包含", "包含（不区分大小写）", "不包含", "不包含（不区分大小写）", "开始以", "不是开始于", "ends with", "不是结束于", "是 null", "不是 null", "是空的", "不是空的", "介于", "不介于", "在列表", "不在列表"}
UNARY = {"是 null", "不是 null", "是空的", "不是空的"}
TEXT = {"包含", "包含（不区分大小写）", "不包含", "不包含（不区分大小写）", "开始以", "不是开始于", "ends with", "不是结束于"}


def joined(terms):
    # The connector belongs to the previous effective term; AND has SQL precedence.
    groups, current = [], []
    for index, (join, expression) in enumerate(terms):
        current.append(expression)
        if index == len(terms) - 1 or join == "or":
            groups.append(and_(*current))
            current = []
    return or_(*groups)


def workbench_conditions(grant, query, normalize, values=None, temporal_types=None):
    _, definition, _, fields = grant
    props = {prop.id: prop for prop in definition.properties if prop.id in fields}
    values = Record.values if values is None else values

    def operand(prop, raw):
        check_record_json({prop.id: raw})
        try:
            if prop.type == "integer":
                if not re.fullmatch(r"[+-]?[0-9]+", raw.strip()):
                    raise ValueError
                value = int(raw)
                if not -(2**63) <= value < 2**63:
                    raise ValueError
                return value
            if prop.type == "number":
                value = Decimal(raw)
                if not value.is_finite() or not -16383 <= value.as_tuple().exponent <= 131071 or value and not -16383 <= value.adjusted() <= 131071:
                    raise ValueError
                return value
            if prop.type == "boolean":
                if raw.lower() not in {"true", "false"}:
                    raise ValueError
                return raw.lower() == "true"
            if prop.type == "json":
                value = json.loads(raw)
                check_record_json({prop.id: value})
                return value
            normalized = normalize(definition, {prop.id: raw})[prop.id]
            if prop.type == "date":
                return date.fromisoformat(normalized)
            if prop.type == "datetime":
                value = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
                if temporal_types is not None and (value.tzinfo is not None) != temporal_types[prop.id]:
                    raise HTTPException(422, "Timestamp timezone must match the mapped property")
                return value
            return normalized
        except (ValueError, InvalidOperation, OverflowError):
            raise HTTPException(422, f"{prop.id} must be a valid {prop.type}") from None

    def scalar(prop, sample):
        value = values[prop.id]
        if prop.type == "json":
            return value
        value = value.astext
        if prop.type in {"integer", "number"}:
            return cast(value, Numeric)
        if prop.type == "boolean":
            return cast(value, Boolean)
        if prop.type == "date":
            return cast(value, Date)
        if prop.type == "datetime":
            return cast(value, DateTime(timezone=sample.tzinfo is not None))
        return value

    stack, enabled = [[]], [True]
    for rule in query.workbenchFilters:
        if rule.id.startswith("group-open-"):
            stack.append([])
            enabled.append(enabled[-1] and rule.active)
            continue
        if rule.id.startswith("group-close-"):
            if len(stack) == 1:
                raise HTTPException(422, "Unbalanced filter group")
            terms = stack.pop()
            enabled.pop()
            if terms:
                stack[-1].append((rule.join, joined(terms)))
            continue
        if not enabled[-1] or not rule.active:
            continue
        op = re.sub(r"\s+", " ", rule.operator).strip()
        if op not in OPERATORS:
            raise HTTPException(422, "Unsupported filter operator")
        if not rule.field and not rule.value and op == "=":
            continue
        if rule.field not in props:
            raise HTTPException(403, "Filter references an unavailable property")
        prop = props[rule.field]
        check_record_json({prop.id: rule.value})
        text = values[prop.id].astext
        if op in UNARY:
            expression = {"是 null": text.is_(None), "不是 null": text.is_not(None), "是空的": text == "", "不是空的": text != ""}[op]
        elif rule.value == "":
            continue
        elif op in TEXT:
            if op in {"开始以", "不是开始于"}:
                expression = text.startswith(rule.value, autoescape=True)
            elif op in {"ends with", "不是结束于"}:
                expression = text.endswith(rule.value, autoescape=True)
            else:
                expression = text.icontains(rule.value, autoescape=True) if "不区分大小写" in op else text.contains(rule.value, autoescape=True)
            if op.startswith(("不包含", "不是")):
                expression = ~expression
        else:
            raw = [value.strip() for value in rule.value.split(",")] if op in {"介于", "不介于", "在列表", "不在列表"} else [rule.value]
            if op in {"介于", "不介于"} and len(raw) != 2:
                raise HTTPException(422, "Between requires two values")
            operands = [operand(prop, value) for value in raw]
            if prop.type == "datetime" and len({value.tzinfo is not None for value in operands}) != 1:
                raise HTTPException(422, "Timestamp operands must have the same timezone semantics")
            value = scalar(prop, operands[0])
            if prop.type == "json":
                operands = [type_coerce(item, JSONB) for item in operands]
            if op in {"介于", "不介于"}:
                expression = value.between(*operands)
                if op == "不介于":
                    expression = ~expression
            elif op in {"在列表", "不在列表"}:
                expression = value.in_(operands)
                if op == "不在列表":
                    expression = ~expression
            else:
                item = operands[0]
                expression = {"=": operator.eq, "!=": operator.ne, "<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge}[op](value, item)
        stack[-1].append((rule.join, expression))
    if len(stack) != 1:
        raise HTTPException(422, "Unbalanced filter group")
    conditions = [joined(stack[0])] if stack[0] else []
    if query.search:
        check_record_json({"search": query.search})
        conditions.append(or_(*(values[field].astext.icontains(query.search, autoescape=True) for field in props)) if props else false())
    return conditions
