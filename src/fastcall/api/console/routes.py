from fastapi import APIRouter, Depends, Query, Request

from fastcall.api.console.auth import (
    ConsoleSession,
    LoginRequest,
    console_session,
    console_write_session,
    login_response,
    logout_response,
)
from fastcall.api.dependencies import get_uow
from fastcall.api.requests import (
    CreateAppRequest,
    DraftRequest,
    EnabledRequest,
    PublishRequest,
    RevisionRequest,
    RollbackRequest,
)
from fastcall.application.publication import PublicationUseCase
from fastcall.application.robot_apps import RobotAppsUseCase
from fastcall.application.validation import ValidateConfigUseCase


router = APIRouter(prefix="/console/api/v1")


def validator(request):
    return ValidateConfigUseCase(request.app.state.catalog)


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    return login_response(payload, request)


@router.post("/logout")
def logout(request: Request):
    return logout_response(request)


@router.get("/session")
def session(session: ConsoleSession = Depends(console_session)):
    return {"actor_id": session.actor, "csrf_token": session.csrf_token}


@router.get("/capabilities/runtime-profiles")
def capabilities(request: Request, session=Depends(console_session)):
    return request.app.state.catalog.data


@router.get("/robot-apps")
def list_apps(uow=Depends(get_uow), session=Depends(console_session)):
    return {"items": RobotAppsUseCase(uow).list()}


@router.post("/robot-apps", status_code=201)
def create_app(payload: CreateAppRequest, uow=Depends(get_uow), session=Depends(console_write_session)):
    return RobotAppsUseCase(uow).create(payload.app_code, payload.name, payload.config, session.actor)


@router.get("/robot-apps/{app_id}")
def get_app(app_id: str, uow=Depends(get_uow), session=Depends(console_session)):
    return RobotAppsUseCase(uow).get(app_id)


@router.put("/robot-apps/{app_id}/draft")
def update_draft(app_id: str, payload: DraftRequest, uow=Depends(get_uow), session=Depends(console_write_session)):
    return RobotAppsUseCase(uow).update_draft(app_id, payload.draft_revision, payload.config, session.actor)


@router.put("/robot-apps/{app_id}/enabled")
def set_enabled(app_id: str, payload: EnabledRequest, uow=Depends(get_uow), session=Depends(console_write_session)):
    return RobotAppsUseCase(uow).set_enabled(app_id, payload.enabled, session.actor)


@router.post("/robot-apps/{app_id}/validate")
def validate(app_id: str, payload: RevisionRequest, request: Request, uow=Depends(get_uow), session=Depends(console_write_session)):
    return RobotAppsUseCase(uow).validate(app_id, payload.draft_revision, validator(request))


@router.post("/robot-apps/{app_id}/publish")
def publish(app_id: str, payload: PublishRequest, request: Request, uow=Depends(get_uow), session=Depends(console_write_session)):
    return PublicationUseCase(uow, validator(request)).publish(
        app_id, payload.draft_revision, payload.config_hash, session.actor
    )


@router.get("/robot-apps/{app_id}/versions")
def versions(app_id: str, uow=Depends(get_uow), session=Depends(console_session)):
    return {"items": PublicationUseCase(uow, None).list_versions(app_id)}


@router.get("/robot-apps/{app_id}/versions/{version_id}")
def version(app_id: str, version_id: str, uow=Depends(get_uow), session=Depends(console_session)):
    return PublicationUseCase(uow, None).get_version(app_id, version_id)


@router.get("/robot-apps/{app_id}/diff")
def diff(app_id: str, from_version: int = Query(alias="from", ge=1), to_version: int = Query(alias="to", ge=1), uow=Depends(get_uow), session=Depends(console_session)):
    return PublicationUseCase(uow, None).diff(app_id, from_version, to_version)


@router.post("/robot-apps/{app_id}/rollback")
def rollback(app_id: str, payload: RollbackRequest, uow=Depends(get_uow), session=Depends(console_write_session)):
    return PublicationUseCase(uow, None).rollback(app_id, payload.robot_app_version_id, session.actor)
