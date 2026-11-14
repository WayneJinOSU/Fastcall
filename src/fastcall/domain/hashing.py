import hashlib
import json

from fastcall.domain.runtime import RuntimeResolution


def canonical_json(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def calculate_config_hash(app_config: dict, runtime: RuntimeResolution) -> str:
    payload = {
        "app_config": app_config,
        "runtime_resolution": runtime.as_dict(),
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
