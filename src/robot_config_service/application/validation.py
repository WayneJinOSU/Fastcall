from robot_config_service.capabilities.loader import CapabilityCatalog
from robot_config_service.capabilities.runtime_resolver import resolve_runtime
from robot_config_service.domain.validation import ValidationReport, validate_app_config


class ValidateConfigUseCase:
    def __init__(self, catalog: CapabilityCatalog):
        self.catalog = catalog

    def execute(self, config: dict) -> ValidationReport:
        return validate_app_config(config, self.catalog, resolve_runtime)
