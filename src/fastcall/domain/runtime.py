from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeResolution:
    engine: str
    adapter: str
    catalog_version: str
    remote_supported: bool
    deprecated: bool = False

    def as_dict(self) -> dict:
        return {"engine": self.engine, "adapter": self.adapter}
