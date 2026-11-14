from fastcall.capabilities.loader import CapabilityCatalog
from fastcall.capabilities.runtime_resolver import resolve_runtime
from fastcall.domain.validation import ValidationReport, validate_app_config


class ValidateConfigUseCase:
    def __init__(self, catalog: CapabilityCatalog):
        self.catalog = catalog

    def execute(self, config: dict) -> ValidationReport:
        return validate_app_config(config, self.catalog, resolve_runtime)
