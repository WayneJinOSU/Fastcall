import datetime
from dataclasses import dataclass
from typing import Protocol


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


class RobotAppRepository(Protocol):
    def list(self) -> list[RobotAppRecord]: ...
    def get(self, app_id: str) -> RobotAppRecord | None: ...
    def get_for_update(self, app_id: str) -> RobotAppRecord | None: ...
    def get_by_code(self, app_code: str) -> RobotAppRecord | None: ...
    def add(self, record: RobotAppRecord) -> None: ...
    def save(self, record: RobotAppRecord) -> None: ...


class RobotVersionRepository(Protocol):
    def list(self, app_id: str) -> list[RobotVersionRecord]: ...
    def get_by_id(self, version_id: str) -> RobotVersionRecord | None: ...
    def get(self, app_id: str, version_id: str) -> RobotVersionRecord | None: ...
    def get_by_no(self, app_id: str, version_no: int) -> RobotVersionRecord | None: ...
    def get_by_hash(self, app_id: str, config_hash: str) -> RobotVersionRecord | None: ...
    def next_version_no(self, app_id: str) -> int: ...
    def add(self, record: RobotVersionRecord) -> None: ...


class RobotReleaseRepository(Protocol):
    def get(self, app_id: str) -> RobotReleaseRecord | None: ...
    def get_for_update(self, app_id: str) -> RobotReleaseRecord | None: ...
    def add(self, record: RobotReleaseRecord) -> None: ...
    def save(self, record: RobotReleaseRecord) -> None: ...


class AuditLogRepository(Protocol):
    def append(self, entry: AuditEntry) -> None: ...


class RobotConfigUnitOfWork(Protocol):
    apps: RobotAppRepository
    versions: RobotVersionRepository
    releases: RobotReleaseRepository
    audits: AuditLogRepository

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
