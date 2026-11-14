import copy

from fastcall_core.application.dto import new_id, utcnow
from fastcall.application.dto import app_view
from fastcall.application.ports import (
    AuditEntry,
    RobotAppRecord,
    RobotConfigUnitOfWork,
)
from fastcall.domain.contract import SCHEMA_VERSION
from fastcall_core.domain.errors import RobotConfigError


def _get_app(uow, app_id: str, lock=False) -> RobotAppRecord:
    app = uow.apps.get_for_update(app_id) if lock else uow.apps.get(app_id)
    if app is None:
        raise RobotConfigError("ROBOT_APP_NOT_FOUND", "Robot app not found")
    return app


def _audit(uow, actor, action, app_id, detail, version_id=None, config_hash=None):
    uow.audits.append(AuditEntry(
        id=new_id("audit"),
        actor=actor,
        action=action,
        object_type="robot_app",
        object_id=app_id,
        app_version_id=version_id,
        config_hash=config_hash,
        detail_json=detail,
        created_at=utcnow(),
    ))


def _check_identity(config, app_code, name):
    if not isinstance(config, dict) or not isinstance(config.get("app"), dict):
        raise RobotConfigError("CONFIG_VALIDATION_FAILED", "config.app must be an object")
    app = config["app"]
    if set(app) != {"code", "name"} or app.get("code") != app_code or app.get("name") != name:
        raise RobotConfigError(
            "CONFIG_VALIDATION_FAILED",
            "config.app must contain only the current app code and name",
        )


class RobotAppsUseCase:
    def __init__(self, uow: RobotConfigUnitOfWork):
        self.uow = uow

    def list(self):
        return [app_view(app, self.uow.releases.get(app.id)) for app in self.uow.apps.list()]

    def get(self, app_id):
        app = _get_app(self.uow, app_id)
        return app_view(app, self.uow.releases.get(app.id))

    def create(self, app_code, name, config, actor):
        app_code = (app_code or "").strip() or new_id("robot")
        name = name.strip()
        if self.uow.apps.get_by_code(app_code):
            raise RobotConfigError("ROBOT_APP_EXISTS", "app_code already exists")
        draft = copy.deepcopy(config) if config is not None else {
            "schema_version": SCHEMA_VERSION,
            "app": {"code": app_code, "name": name},
            "variables": [],
        }
        _check_identity(draft, app_code, name)
        now = utcnow()
        app = RobotAppRecord(
            id=new_id("app"),
            app_code=app_code,
            name=name,
            enabled=True,
            draft_json=draft,
            draft_revision=1,
            created_at=now,
            updated_at=now,
        )
        try:
            self.uow.apps.add(app)
            _audit(self.uow, actor, "app.create", app.id, {"draft_revision": 1})
            self.uow.commit()
        except Exception:
            self.uow.rollback()
            raise
        return app_view(app)

    def update_draft(self, app_id, revision, config, actor):
        try:
            app = _get_app(self.uow, app_id, lock=True)
            if app.draft_revision != revision:
                raise RobotConfigError("DRAFT_REVISION_CONFLICT", "Draft revision does not match")
            _check_identity(config, app.app_code, app.name)
            app.draft_json = copy.deepcopy(config)
            app.draft_revision += 1
            app.updated_at = utcnow()
            self.uow.apps.save(app)
            _audit(self.uow, actor, "draft.update", app.id, {"draft_revision": app.draft_revision})
            self.uow.commit()
        except Exception:
            self.uow.rollback()
            raise
        return app_view(app)

    def set_enabled(self, app_id, enabled, actor):
        try:
            app = _get_app(self.uow, app_id, lock=True)
            app.enabled = enabled
            app.updated_at = utcnow()
            self.uow.apps.save(app)
            _audit(self.uow, actor, "app.enabled", app.id, {"enabled": enabled})
            self.uow.commit()
        except Exception:
            self.uow.rollback()
            raise
        return app_view(app)

    def validate(self, app_id, revision, validate_use_case):
        app = _get_app(self.uow, app_id)
        if app.draft_revision != revision:
            raise RobotConfigError("DRAFT_REVISION_CONFLICT", "Draft revision does not match")
        return validate_use_case.execute(app.draft_json).as_dict()
