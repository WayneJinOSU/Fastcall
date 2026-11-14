from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fastcall_core.domain.errors import RobotConfigError


STATUS_BY_CODE = {
    "UNAUTHORIZED": 401,
    "ACTOR_REQUIRED": 400,
    "ROBOT_VERSION_NOT_FOUND": 404,
    "OUTBOUND_TASK_NOT_FOUND": 404,
    "TASK_CONTACT_NOT_FOUND": 404,
    "OUTBOUND_CALL_NOT_FOUND": 404,
    "STATE_VERSION_CONFLICT": 409,
    "CONTACT_RESERVATION_CONFLICT": 409,
    "ACTIVE_CALL_MISMATCH": 409,
    "STALE_CLAIM_GENERATION": 409,
    "SESSION_FAILED": 409,
    "SESSION_NOT_RECOVERABLE": 409,
    "SESSION_NOT_CLAIMABLE": 409,
    "EVENT_ID_CONFLICT": 409,
    "EVENT_ID_MISMATCH": 400,
    "CONFIG_HASH_MISMATCH": 409,
}


def error_content(code, message, details=None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


def register_error_handlers(app: FastAPI):
    @app.exception_handler(RobotConfigError)
    async def handle_domain_error(request, exc: RobotConfigError):
        return JSONResponse(
            status_code=STATUS_BY_CODE.get(exc.code, 400),
            content=error_content(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_error(request, exc: RequestValidationError):
        details = [
            {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_content("INVALID_REQUEST", "Request validation failed", details),
        )
