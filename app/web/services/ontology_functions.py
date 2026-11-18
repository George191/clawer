"""Validate and evaluate reviewed, typed business functions without executing user code."""

from fastapi import HTTPException

from app.web.api.models.ontology_registry import BusinessFunction, Constant, Definition, Parameter
from app.web.services.ontology_runtime_values import check_record_json, normalize_value


def _parameters(function, properties):
    parameters = {}
    for assignment in function.assignments:
        for operand in assignment.operands:
            if isinstance(operand, Parameter):
                prop = properties[assignment.field]
                param = parameters.setdefault(
                    operand.parameter,
                    {"key": operand.parameter, "type": prop.type, "nullable": True},
                )
                if param["type"] != prop.type:
                    raise ValueError(f"Parameter {operand.parameter} has conflicting types")
                if prop.required or assignment.operation != "set":
                    param["nullable"] = False
    return parameters


def function_errors(definition: Definition):
    properties = {p.id: p for p in definition.properties}
    writable = {
        field for action in definition.actions if action.kind == "modify" for field in action.fields
    }
    if len({f.key for f in definition.functions}) != len(definition.functions):
        yield "Function identifiers must be unique"
    for function in definition.functions:
        seen = set()
        try:
            for assignment in function.assignments:
                prop = properties.get(assignment.field)
                if (
                    definition.source.mode != "none"
                    or not prop
                    or prop.id not in writable
                    or prop.id in definition.primaryKey
                    or prop.id in seen
                ):
                    raise ValueError("Each output must name a distinct writable Modify property")
                seen.add(prop.id)
                op = assignment.operation
                if len(assignment.operands) != (1 if op == "set" else 2):
                    raise ValueError(f"{op} has the wrong number of operands")
                if (
                    op in ("add", "multiply")
                    and prop.type not in ("integer", "number")
                    or op == "concat"
                    and prop.type != "string"
                ):
                    raise ValueError(f"{op} is incompatible with the type of {prop.id}")
                for operand in assignment.operands:
                    if isinstance(operand, Constant):
                        if operand.value is None and op != "set":
                            raise ValueError(f"{op} requires non-null operands")
                        normalize_value(prop, operand.value)
            _parameters(function, properties)
            check_record_json(function.model_dump(mode="json"))
        except (ValueError, HTTPException) as exc:
            message = exc.detail if isinstance(exc, HTTPException) else str(exc)
            yield f"{function.key}: {message}"


def available_functions(definition, actions):
    writable = {field for action in actions if action.kind == "modify" for field in action.fields}
    return [fn for fn in definition.functions if all(a.field in writable for a in fn.assignments)]


def signature(function, properties):
    return {
        "key": function.key,
        "name": function.name,
        "parameters": list(_parameters(function, properties).values()),
    }


def evaluate(function: BusinessFunction, properties, arguments):
    check_record_json(arguments)
    if arguments.keys() != _parameters(function, properties).keys():
        raise HTTPException(422, "Provide exactly the function's declared parameters")
    result = {}
    for assignment in function.assignments:
        prop = properties[assignment.field]
        values = [
            normalize_value(
                prop, term.value if isinstance(term, Constant) else arguments[term.parameter]
            )
            for term in assignment.operands
        ]
        operation = assignment.operation
        if operation != "set" and any(value is None for value in values):
            raise HTTPException(422, f"{operation} requires non-null values")
        if operation == "set":
            value = values[0]
        elif operation == "multiply":
            value = values[0] * values[1]
        else:
            # Validation restricts add to numbers and concat to strings.
            value = values[0] + values[1]
        result[prop.id] = normalize_value(prop, value)
    check_record_json(result)
    return result
