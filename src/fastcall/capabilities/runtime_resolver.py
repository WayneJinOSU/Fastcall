from fastcall.capabilities.loader import CapabilityCatalog
from fastcall_core.domain.errors import RobotConfigError
from fastcall.domain.runtime import RuntimeResolution


def resolve_runtime(
    provider: str,
    model: str,
    catalog: CapabilityCatalog,
) -> RuntimeResolution:
    for profile in catalog.data["runtime_profiles"]:
        if profile["provider"] == provider and profile["model"] == model:
            if not profile.get("enabled", False):
                raise RobotConfigError(
                    "UNSUPPORTED_MODEL_RUNTIME",
                    "The exact model runtime is disabled",
                )
            return RuntimeResolution(
                engine=profile["runtime_engine"],
                adapter=profile["runtime_adapter"],
                catalog_version=catalog.version,
                remote_supported=profile["remote_runtime_supported"],
                deprecated=profile.get("deprecated", False),
            )
    raise RobotConfigError(
        "UNSUPPORTED_MODEL_RUNTIME",
        "No exact runtime profile exists for this provider and model",
    )
