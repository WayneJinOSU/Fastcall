import re
from dataclasses import asdict, dataclass


SCHEMA_VERSION = "robot-app/v1"
TOP_LEVEL_FIELDS = {
    "schema_version", "app", "prompt", "variables", "asr", "llm", "tts",
    "realtime", "flow", "call_policy",
}


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


def add_issue(issues: list, path: str, message: str, code="CONFIG_VALIDATION_FAILED"):
    issues.append(ValidationIssue(code=code, path=path, message=message))


def is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_object_fields(
    config: dict,
    block: str,
    specs: dict,
    issues: list,
) -> None:
    value = config.get(block)
    if not isinstance(value, dict):
        add_issue(issues, block, "Must be an object")
        return
    unknown = set(value) - set(specs)
    if unknown:
        add_issue(issues, block, f"Unknown fields: {sorted(unknown)}")
    for field, expected in specs.items():
        path = f"{block}.{field}"
        if field not in value:
            add_issue(issues, path, "Field is required")
            continue
        field_value = value[field]
        validators = {
            "string": lambda item: isinstance(item, str) and bool(item.strip()),
            "number": is_number,
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "boolean": lambda item: isinstance(item, bool),
            "object": lambda item: isinstance(item, dict),
        }
        if not validators[expected](field_value):
            add_issue(issues, path, f"Must be {expected}")


def validate_base_contract(config, issues: list) -> None:
    if not isinstance(config, dict):
        add_issue(issues, "", "Configuration must be an object")
        return
    unknown = set(config) - TOP_LEVEL_FIELDS
    if unknown:
        add_issue(issues, "", f"Unknown top-level fields: {sorted(unknown)}")
    if config.get("schema_version") != SCHEMA_VERSION:
        add_issue(issues, "schema_version", f"Must be {SCHEMA_VERSION}")
    app = config.get("app")
    if not isinstance(app, dict):
        add_issue(issues, "app", "Must be an object")
        return
    if set(app) != {"code", "name"}:
        add_issue(issues, "app", "Must contain only code and name")
    for field in ("code", "name"):
        if not isinstance(app.get(field), str) or not app[field].strip():
            add_issue(issues, f"app.{field}", "Must be a non-empty string")
        elif field == "code" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", app[field]):
            add_issue(issues, "app.code", "Contains unsupported characters")
