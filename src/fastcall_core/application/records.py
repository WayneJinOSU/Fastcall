import datetime
from dataclasses import dataclass


@dataclass
class RobotAppRecord:
    id: str
    app_code: str
    name: str
    enabled: bool
    draft_json: dict
    draft_revision: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


@dataclass
class RobotVersionRecord:
    id: str
    app_id: str
    version_no: int
    source_draft_revision: int
    content_json: dict
    config_hash: str
    schema_version: str
    runtime_engine: str
    runtime_adapter: str
    runtime_catalog_version: str
    validation_report_json: dict
    validated_at: datetime.datetime
    created_by: str
    created_at: datetime.datetime


@dataclass
class RobotReleaseRecord:
    id: str
    app_id: str
    robot_app_version_id: str
    updated_by: str
    updated_at: datetime.datetime


@dataclass
class AuditEntry:
    id: str
    actor: str
    action: str
    object_type: str
    object_id: str
    app_version_id: str | None
    config_hash: str | None
    detail_json: dict
    created_at: datetime.datetime
