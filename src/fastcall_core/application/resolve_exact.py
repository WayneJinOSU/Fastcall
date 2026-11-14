import copy

from fastcall_core.domain.errors import RobotConfigError


class ResolveExactVersionUseCase:
    def __init__(self, uow):
        self.uow = uow

    def execute(self, robot_app_version_id):
        version = self.uow.pinned_versions.get_by_id(robot_app_version_id)
        if version is None:
            raise RobotConfigError("ROBOT_VERSION_NOT_FOUND", "Robot version not found")
        return {
            "robot_app_version_id": version.id,
            "version_no": version.version_no,
            "config_hash": version.config_hash,
            "runtime_engine": version.runtime_engine,
            "runtime_adapter": version.runtime_adapter,
            "runtime_catalog_version": version.runtime_catalog_version,
            "app_config": copy.deepcopy(version.content_json),
        }
