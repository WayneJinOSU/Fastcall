import copy

from fastcall_core.application.dto import isoformat, new_id, utcnow
from fastcall_core.application.records import (
    RobotAppRecord,
    RobotReleaseRecord,
    RobotVersionRecord,
)


def app_view(app: RobotAppRecord, release: RobotReleaseRecord | None = None) -> dict:
    return {
        "app_id": app.id,
        "app_code": app.app_code,
        "name": app.name,
        "enabled": app.enabled,
        "draft": copy.deepcopy(app.draft_json),
        "draft_revision": app.draft_revision,
        "created_at": isoformat(app.created_at),
        "updated_at": isoformat(app.updated_at),
        "current_release": None if release is None else {
            "robot_app_version_id": release.robot_app_version_id,
            "updated_by": release.updated_by,
            "updated_at": isoformat(release.updated_at),
        },
    }


def version_view(version: RobotVersionRecord, include_config=False) -> dict:
    result = {
        "robot_app_version_id": version.id,
        "app_id": version.app_id,
        "version_no": version.version_no,
        "source_draft_revision": version.source_draft_revision,
        "config_hash": version.config_hash,
        "schema_version": version.schema_version,
        "runtime_engine": version.runtime_engine,
        "runtime_adapter": version.runtime_adapter,
        "runtime_catalog_version": version.runtime_catalog_version,
        "validation_report": copy.deepcopy(version.validation_report_json),
        "validated_at": isoformat(version.validated_at),
        "created_by": version.created_by,
        "created_at": isoformat(version.created_at),
    }
    if include_config:
        result["app_config"] = copy.deepcopy(version.content_json)
    return result
