import re

from fastcall.domain.contract import add_issue


VARIABLE_REFERENCE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
VARIABLE_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SECRET_KEY = re.compile(r"(?:api[_-]?key|secret|password|access[_-]?token)", re.I)
SECRET_VALUE = re.compile(r"(?:sk-(?:or-v1-|car-)?[A-Za-z0-9_-]{12,}|Bearer\s+\S+)", re.I)
VARIABLE_TYPES = {"string", "decimal", "integer", "boolean", "datetime"}
VARIABLE_FIELDS = {"key", "type", "required", "sensitive", "max_length", "default", "format"}


def walk_strings(value, path=""):
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield from walk_strings(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_strings(item, f"{path}[{index}]")
    elif isinstance(value, str):
        yield path, value


def validate_variables(config, issues: list) -> set[str]:
    variables = config.get("variables")
    if not isinstance(variables, list):
        add_issue(issues, "variables", "Must be an array")
        return set()
    keys = set()
    for index, variable in enumerate(variables):
        path = f"variables[{index}]"
        if not isinstance(variable, dict):
            add_issue(issues, path, "Must be an object")
            continue
        unknown = set(variable) - VARIABLE_FIELDS
        if unknown:
            add_issue(issues, path, f"Unknown fields: {sorted(unknown)}")
        key = variable.get("key")
        if not isinstance(key, str) or not VARIABLE_KEY.fullmatch(key):
            add_issue(issues, f"{path}.key", "Invalid variable key")
            continue
        if key in keys:
            add_issue(issues, f"{path}.key", "Duplicate variable key")
        keys.add(key)
        if variable.get("type") not in VARIABLE_TYPES:
            add_issue(issues, f"{path}.type", "Unsupported variable type")
        if not isinstance(variable.get("required"), bool):
            add_issue(issues, f"{path}.required", "Must be boolean")
        if "sensitive" in variable and not isinstance(variable["sensitive"], bool):
            add_issue(issues, f"{path}.sensitive", "Must be boolean")
        max_length = variable.get("max_length")
        if max_length is not None and (
            not isinstance(max_length, int)
            or isinstance(max_length, bool)
            or max_length <= 0
        ):
            add_issue(issues, f"{path}.max_length", "Must be a positive integer")
        if variable.get("required") is True and "default" in variable:
            add_issue(issues, f"{path}.default", "Required variables cannot have defaults")
        if variable.get("type") == "decimal" and variable.get("format") not in {None, "money"}:
            add_issue(issues, f"{path}.format", "Unsupported decimal format")
    return keys


def validate_variable_references(config, variable_keys: set[str], issues: list) -> None:
    for path, value in walk_strings({"prompt": config.get("prompt"), "flow": config.get("flow")}):
        for key in VARIABLE_REFERENCE.findall(value):
            if key not in variable_keys:
                add_issue(issues, path, f"Undefined variable reference: {key}")


def validate_no_secrets(config, issues: list) -> None:
    def walk(value, path=""):
        if isinstance(value, dict):
            for key, item in value.items():
                child = f"{path}.{key}" if path else str(key)
                if SECRET_KEY.search(str(key)):
                    add_issue(issues, child, "Secret material is not allowed")
                walk(item, child)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")
        elif isinstance(value, str) and SECRET_VALUE.search(value):
            add_issue(issues, path, "Suspected API key or token")

    walk(config)
