import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class CapabilityCatalog:
    data: dict

    @property
    def version(self) -> str:
        return self.data["catalog_version"]


@lru_cache(maxsize=1)
def load_catalog() -> CapabilityCatalog:
    path = Path(__file__).with_name("catalog.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"catalog_version", "runtime_profiles", "asr", "tts", "realtime", "required_blocks"}
    missing = required - set(data)
    if missing:
        raise RuntimeError(f"Capability catalog is incomplete: {sorted(missing)}")
    return CapabilityCatalog(data=data)
