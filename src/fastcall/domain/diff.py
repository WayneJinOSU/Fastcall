import difflib
import json


def diff_configs(source: dict, target: dict, from_label: str, to_label: str) -> str:
    source_lines = json.dumps(
        source, ensure_ascii=False, indent=2, sort_keys=True
    ).splitlines()
    target_lines = json.dumps(
        target, ensure_ascii=False, indent=2, sort_keys=True
    ).splitlines()
    return "\n".join(difflib.unified_diff(
        source_lines,
        target_lines,
        fromfile=from_label,
        tofile=to_label,
        lineterm="",
    ))
