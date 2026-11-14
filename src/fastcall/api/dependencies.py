import hmac

from fastapi import Header, Request

from fastcall_core.domain.errors import RobotConfigError


def _bearer_token(authorization):
    scheme, separator, value = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not value:
        raise RobotConfigError("UNAUTHORIZED", "Bearer token is required")
    return value


def _check_token(provided, expected):
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise RobotConfigError("UNAUTHORIZED", "Invalid token")


def management_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    x_actor_id: str | None = Header(default=None),
):
    _check_token(
        _bearer_token(authorization),
        request.app.state.settings.management_token,
    )
    actor = (x_actor_id or "").strip()
    if not actor:
        raise RobotConfigError("ACTOR_REQUIRED", "X-Actor-Id is required")
    if len(actor) > 128:
        raise RobotConfigError("ACTOR_REQUIRED", "X-Actor-Id must not exceed 128 characters")
    return actor


def resolve_auth(
    request: Request,
    authorization: str | None = Header(default=None),
):
    _check_token(
        _bearer_token(authorization),
        request.app.state.settings.resolve_token,
    )


def openapi_auth(request: Request, authorization: str | None = Header(default=None)):
    _check_token(_bearer_token(authorization), request.app.state.settings.openapi_token)


def admin_actor(
    request: Request,
    authorization: str | None = Header(default=None),
    x_actor_id: str | None = Header(default=None),
):
    _check_token(_bearer_token(authorization), request.app.state.settings.admin_token)
    actor = (x_actor_id or "").strip()
    if not actor:
        raise RobotConfigError("ACTOR_REQUIRED", "X-Actor-Id is required")
    if len(actor) > 128:
        raise RobotConfigError("ACTOR_REQUIRED", "X-Actor-Id must not exceed 128 characters")
    return actor


def adapter_auth(request: Request, authorization: str | None = Header(default=None)):
    _check_token(_bearer_token(authorization), request.app.state.settings.adapter_token)


def agent_auth(request: Request, authorization: str | None = Header(default=None)):
    _check_token(_bearer_token(authorization), request.app.state.settings.agent_token)


def media_auth(request: Request, authorization: str | None = Header(default=None)):
    _check_token(_bearer_token(authorization), request.app.state.settings.media_token)


def get_secret_codec(request: Request):
    return request.app.state.secret_codec


def get_uow(request: Request):
    uow = request.app.state.uow_factory()
    try:
        yield uow
    finally:
        uow.close()
