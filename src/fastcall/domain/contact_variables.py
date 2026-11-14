import datetime
from decimal import Decimal, InvalidOperation

from fastcall_core.domain.errors import RobotConfigError


def validate_contact_variables(definitions, supplied):
    supplied = supplied or {}
    if not isinstance(supplied, dict):
        raise RobotConfigError("VARIABLE_VALIDATION_FAILED", "variables must be an object")
    definitions = definitions if isinstance(definitions, list) else []
    known = {item.get("key") for item in definitions}
    if set(supplied) - known:
        raise RobotConfigError("VARIABLE_VALIDATION_FAILED", "Unknown contact variable")
    result = {}
    for definition in definitions:
        key = definition["key"]
        value = supplied.get(key, definition.get("default"))
        if value in (None, ""):
            if definition.get("required"):
                raise RobotConfigError("VARIABLE_VALIDATION_FAILED", f"Required variable: {key}")
            result[key] = value
            continue
        kind = definition.get("type", "string")
        valid = True
        if kind == "string":
            valid = isinstance(value, str)
        elif kind == "integer":
            valid = isinstance(value, int) and not isinstance(value, bool)
        elif kind == "boolean":
            valid = isinstance(value, bool)
        elif kind == "decimal":
            try:
                Decimal(str(value))
            except (InvalidOperation, ValueError):
                valid = False
        elif kind == "datetime":
            try:
                datetime.datetime.fromisoformat(str(value))
            except ValueError:
                valid = False
        if not valid:
            raise RobotConfigError("VARIABLE_VALIDATION_FAILED", f"Invalid variable type: {key}")
        if isinstance(value, str) and len(value) > definition.get("max_length", 10000):
            raise RobotConfigError("VARIABLE_VALIDATION_FAILED", f"Variable is too long: {key}")
        result[key] = value
    return result
