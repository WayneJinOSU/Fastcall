import copy

from fastcall_core.application.dto import new_id, utcnow
from fastcall.application.dto import version_view
from fastcall.application.ports import (
    RobotConfigUnitOfWork,
    RobotReleaseRecord,
    RobotVersionRecord,
)
from fastcall.application.robot_apps import _audit, _get_app
from fastcall.application.validation import ValidateConfigUseCase
from fastcall.domain.diff import diff_configs
from fastcall_core.domain.errors import RobotConfigError


class PublicationUseCase:
    def __init__(self, uow: RobotConfigUnitOfWork, validator: ValidateConfigUseCase):
        self.uow = uow
        self.validator = validator

    def publish(self, app_id, revision, config_hash, actor):
        try:
            app = _get_app(self.uow, app_id, lock=True)
            if app.draft_revision != revision:
                raise RobotConfigError("DRAFT_REVISION_CONFLICT", "Draft revision does not match")
            report = self.validator.execute(app.draft_json)
            if not report.valid:
                code = report.errors[0].code if report.errors else "CONFIG_VALIDATION_FAILED"
                raise RobotConfigError(code, "Configuration validation failed", details=report.as_dict())
            if report.config_hash != config_hash:
                raise RobotConfigError("CONFIG_HASH_MISMATCH", "Config hash does not match the current draft")
            version = self.uow.versions.get_by_hash(app.id, config_hash)
            if version is None:
                now = utcnow()
                version = RobotVersionRecord(
                    id=new_id("appver"),
                    app_id=app.id,
                    version_no=self.uow.versions.next_version_no(app.id),
                    source_draft_revision=revision,
                    content_json=copy.deepcopy(app.draft_json),
                    config_hash=config_hash,
                    schema_version=app.draft_json["schema_version"],
                    runtime_engine=report.runtime_resolution.engine,
                    runtime_adapter=report.runtime_resolution.adapter,
                    runtime_catalog_version=report.catalog_version,
                    validation_report_json=report.as_dict(),
                    validated_at=now,
                    created_by=actor,
                    created_at=now,
                )
                self.uow.versions.add(version)
            release = self.uow.releases.get_for_update(app.id)
            if release is None:
                self.uow.releases.add(RobotReleaseRecord(
                    id=new_id("rel"),
                    app_id=app.id,
                    robot_app_version_id=version.id,
                    updated_by=actor,
                    updated_at=utcnow(),
                ))
            else:
                release.robot_app_version_id = version.id
                release.updated_by = actor
                release.updated_at = utcnow()
                self.uow.releases.save(release)
            _audit(
                self.uow, actor, "version.publish", app.id,
                {"draft_revision": revision, "version_no": version.version_no},
                version_id=version.id, config_hash=version.config_hash,
            )
            self.uow.commit()
            return version_view(version, include_config=True)
        except Exception:
            self.uow.rollback()
            raise

    def list_versions(self, app_id):
        _get_app(self.uow, app_id)
        return [version_view(version) for version in self.uow.versions.list(app_id)]

    def get_version(self, app_id, version_id):
        _get_app(self.uow, app_id)
        version = self.uow.versions.get(app_id, version_id)
        if version is None:
            raise RobotConfigError("ROBOT_VERSION_NOT_FOUND", "Robot version not found")
        return version_view(version, include_config=True)

    def diff(self, app_id, from_version, to_version):
        _get_app(self.uow, app_id)
        source = self.uow.versions.get_by_no(app_id, from_version)
        target = self.uow.versions.get_by_no(app_id, to_version)
        if source is None or target is None:
            raise RobotConfigError("ROBOT_VERSION_NOT_FOUND", "Robot version not found")
        return {
            "from": version_view(source),
            "to": version_view(target),
            "unified_diff": diff_configs(
                source.content_json,
                target.content_json,
                str(from_version),
                str(to_version),
            ),
        }

    def rollback(self, app_id, version_id, actor):
        try:
            app = _get_app(self.uow, app_id, lock=True)
            version = self.uow.versions.get(app_id, version_id)
            if version is None:
                raise RobotConfigError("ROBOT_VERSION_NOT_FOUND", "Robot version not found")
            release = self.uow.releases.get_for_update(app_id)
            if release is None:
                raise RobotConfigError("ROBOT_NOT_PUBLISHED", "Robot app has no release")
            if release.robot_app_version_id == version.id:
                self.uow.rollback()
                return version_view(version, include_config=True)
            release.robot_app_version_id = version.id
            release.updated_by = actor
            release.updated_at = utcnow()
            self.uow.releases.save(release)
            _audit(
                self.uow, actor, "release.rollback", app.id,
                {"version_no": version.version_no},
                version_id=version.id, config_hash=version.config_hash,
            )
            self.uow.commit()
            return version_view(version, include_config=True)
        except Exception:
            self.uow.rollback()
            raise
