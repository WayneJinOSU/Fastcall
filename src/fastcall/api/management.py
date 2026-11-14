from fastapi import APIRouter, Depends, Query, Request

from fastcall.api.dependencies import get_uow, management_actor
from fastcall.api.requests import (
    CreateAppRequest,
    DraftRequest,
    EnabledRequest,
    PublishRequest,
    RevisionRequest,
    RollbackRequest,
)
from fastcall.api.responses import ItemsResponse
from fastcall.application.publication import PublicationUseCase
from fastcall.application.robot_apps import RobotAppsUseCase
from fastcall.application.validation import ValidateConfigUseCase


router = APIRouter()


def validator(request):
    return ValidateConfigUseCase(request.app.state.catalog)


@router.get("/api/v1/capabilities/runtime-profiles")
def capabilities(request: Request, actor=Depends(management_actor)):
    return request.app.state.catalog.data


@router.get("/api/v1/robot-apps", response_model=ItemsResponse)
def list_apps(uow=Depends(get_uow), actor=Depends(management_actor)):
    return {"items": RobotAppsUseCase(uow).list()}


@router.post("/api/v1/robot-apps", status_code=201)
def create_app(payload: CreateAppRequest, uow=Depends(get_uow), actor=Depends(management_actor)):
    return RobotAppsUseCase(uow).create(payload.app_code, payload.name, payload.config, actor)


@router.get("/api/v1/robot-apps/{app_id}")
def get_app(app_id: str, uow=Depends(get_uow), actor=Depends(management_actor)):
    return RobotAppsUseCase(uow).get(app_id)


@router.put("/api/v1/robot-apps/{app_id}/draft")
def update_draft(app_id: str, payload: DraftRequest, uow=Depends(get_uow), actor=Depends(management_actor)):
    return RobotAppsUseCase(uow).update_draft(app_id, payload.draft_revision, payload.config, actor)


@router.put("/api/v1/robot-apps/{app_id}/enabled")
def set_enabled(app_id: str, payload: EnabledRequest, uow=Depends(get_uow), actor=Depends(management_actor)):
    return RobotAppsUseCase(uow).set_enabled(app_id, payload.enabled, actor)


@router.post("/api/v1/robot-apps/{app_id}/validate")
def validate(app_id: str, payload: RevisionRequest, request: Request, uow=Depends(get_uow), actor=Depends(management_actor)):
    return RobotAppsUseCase(uow).validate(app_id, payload.draft_revision, validator(request))


@router.post("/api/v1/robot-apps/{app_id}/publish")
def publish(app_id: str, payload: PublishRequest, request: Request, uow=Depends(get_uow), actor=Depends(management_actor)):
    return PublicationUseCase(uow, validator(request)).publish(
        app_id, payload.draft_revision, payload.config_hash, actor
    )


@router.get("/api/v1/robot-apps/{app_id}/versions", response_model=ItemsResponse)
def versions(app_id: str, uow=Depends(get_uow), actor=Depends(management_actor)):
    return {"items": PublicationUseCase(uow, None).list_versions(app_id)}


@router.get("/api/v1/robot-apps/{app_id}/versions/{version_id}")
def version(app_id: str, version_id: str, uow=Depends(get_uow), actor=Depends(management_actor)):
    return PublicationUseCase(uow, None).get_version(app_id, version_id)


@router.get("/api/v1/robot-apps/{app_id}/diff")
def diff(
    app_id: str,
    from_version: int = Query(alias="from", ge=1),
    to_version: int = Query(alias="to", ge=1),
    uow=Depends(get_uow),
    actor=Depends(management_actor),
):
    return PublicationUseCase(uow, None).diff(app_id, from_version, to_version)


@router.post("/api/v1/robot-apps/{app_id}/rollback")
def rollback(app_id: str, payload: RollbackRequest, uow=Depends(get_uow), actor=Depends(management_actor)):
    return PublicationUseCase(uow, None).rollback(app_id, payload.robot_app_version_id, actor)
