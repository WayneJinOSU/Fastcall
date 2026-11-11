from sqlalchemy import func, select

from robot_config_service.application.ports import (
    AuditEntry,
    RobotAppRecord,
    RobotReleaseRecord,
    RobotVersionRecord,
)
from robot_config_service.infrastructure.orm import (
    RobotAppModel,
    RobotAppVersionModel,
    RobotConfigAuditLogModel,
    RobotReleaseModel,
)


def _app_record(model):
    return RobotAppRecord(
        id=model.id, app_code=model.app_code, name=model.name, enabled=model.enabled,
        draft_json=model.draft_json, draft_revision=model.draft_revision,
        created_at=model.created_at, updated_at=model.updated_at,
    )


def _version_record(model):
    return RobotVersionRecord(
        id=model.id, app_id=model.app_id, version_no=model.version_no,
        source_draft_revision=model.source_draft_revision,
        content_json=model.content_json, config_hash=model.config_hash,
        schema_version=model.schema_version, runtime_engine=model.runtime_engine,
        runtime_adapter=model.runtime_adapter,
        runtime_catalog_version=model.runtime_catalog_version,
        validation_report_json=model.validation_report_json,
        validated_at=model.validated_at, created_by=model.created_by,
        created_at=model.created_at,
    )


def _release_record(model):
    return RobotReleaseRecord(
        id=model.id, app_id=model.app_id,
        robot_app_version_id=model.robot_app_version_id,
        updated_by=model.updated_by, updated_at=model.updated_at,
    )


class SqlAlchemyRobotAppRepository:
    def __init__(self, session):
        self.session = session

    def list(self):
        models = self.session.execute(
            select(RobotAppModel).order_by(RobotAppModel.app_code)
        ).scalars().all()
        return [_app_record(model) for model in models]

    def get(self, app_id):
        model = self.session.get(RobotAppModel, app_id)
        return _app_record(model) if model else None

    def get_for_update(self, app_id):
        model = self.session.execute(
            select(RobotAppModel).where(RobotAppModel.id == app_id).with_for_update()
        ).scalar_one_or_none()
        return _app_record(model) if model else None

    def get_by_code(self, app_code):
        model = self.session.execute(
            select(RobotAppModel).where(RobotAppModel.app_code == app_code)
        ).scalar_one_or_none()
        return _app_record(model) if model else None

    def add(self, record):
        self.session.add(RobotAppModel(**record.__dict__))
        self.session.flush()

    def save(self, record):
        model = self.session.get(RobotAppModel, record.id)
        for field in ("name", "enabled", "draft_json", "draft_revision", "updated_at"):
            setattr(model, field, getattr(record, field))
        self.session.flush()


class SqlAlchemyRobotVersionRepository:
    def __init__(self, session):
        self.session = session

    def list(self, app_id):
        models = self.session.execute(
            select(RobotAppVersionModel)
            .where(RobotAppVersionModel.app_id == app_id)
            .order_by(RobotAppVersionModel.version_no.desc())
        ).scalars().all()
        return [_version_record(model) for model in models]

    def get(self, app_id, version_id):
        model = self.session.execute(
            select(RobotAppVersionModel).where(
                RobotAppVersionModel.app_id == app_id,
                RobotAppVersionModel.id == version_id,
            )
        ).scalar_one_or_none()
        return _version_record(model) if model else None

    def get_by_no(self, app_id, version_no):
        model = self.session.execute(
            select(RobotAppVersionModel).where(
                RobotAppVersionModel.app_id == app_id,
                RobotAppVersionModel.version_no == version_no,
            )
        ).scalar_one_or_none()
        return _version_record(model) if model else None

    def get_by_hash(self, app_id, config_hash):
        model = self.session.execute(
            select(RobotAppVersionModel).where(
                RobotAppVersionModel.app_id == app_id,
                RobotAppVersionModel.config_hash == config_hash,
            )
        ).scalar_one_or_none()
        return _version_record(model) if model else None

    def next_version_no(self, app_id):
        current = self.session.execute(
            select(func.max(RobotAppVersionModel.version_no)).where(
                RobotAppVersionModel.app_id == app_id
            )
        ).scalar()
        return (current or 0) + 1

    def add(self, record):
        self.session.add(RobotAppVersionModel(**record.__dict__))
        self.session.flush()


class SqlAlchemyRobotReleaseRepository:
    def __init__(self, session):
        self.session = session

    def get(self, app_id):
        model = self.session.execute(
            select(RobotReleaseModel).where(RobotReleaseModel.app_id == app_id)
        ).scalar_one_or_none()
        return _release_record(model) if model else None

    def get_for_update(self, app_id):
        model = self.session.execute(
            select(RobotReleaseModel).where(RobotReleaseModel.app_id == app_id).with_for_update()
        ).scalar_one_or_none()
        return _release_record(model) if model else None

    def add(self, record):
        self.session.add(RobotReleaseModel(**record.__dict__))
        self.session.flush()

    def save(self, record):
        model = self.session.get(RobotReleaseModel, record.id)
        model.robot_app_version_id = record.robot_app_version_id
        model.updated_by = record.updated_by
        model.updated_at = record.updated_at
        self.session.flush()


class SqlAlchemyAuditLogRepository:
    def __init__(self, session):
        self.session = session

    def append(self, entry: AuditEntry):
        self.session.add(RobotConfigAuditLogModel(**entry.__dict__))
        self.session.flush()
