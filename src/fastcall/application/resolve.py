import copy

from fastcall.application.ports import RobotConfigUnitOfWork
from fastcall_core.domain.errors import RobotConfigError


class ResolveUseCase:
    def __init__(self, uow: RobotConfigUnitOfWork):
        self.uow = uow

    def execute(self, robot_code):
        failure = RobotConfigError(
            "RESOLVE_FAILED",
            "Unable to resolve robot runtime configuration",
        )
        app = self.uow.apps.get_by_code(robot_code)
        if app is None or not app.enabled:
            raise failure
        release = self.uow.releases.get(app.id)
        if release is None:
            raise failure
        version = self.uow.versions.get(app.id, release.robot_app_version_id)
        if version is None:
            raise failure
        return {
            "robot_app_version_id": version.id,
            "version_no": version.version_no,
            "config_hash": version.config_hash,
            "runtime_engine": version.runtime_engine,
            "runtime_adapter": version.runtime_adapter,
            "runtime_catalog_version": version.runtime_catalog_version,
            "app_config": copy.deepcopy(version.content_json),
        }
