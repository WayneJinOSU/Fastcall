from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from fastcall_core.domain.errors import RobotConfigError


STATUS_BY_CODE = {
    "UNAUTHORIZED": 401,
    "CONSOLE_UNAUTHORIZED": 401,
    "CONSOLE_CSRF_FAILED": 403,
    "ACTOR_REQUIRED": 400,
    "ROBOT_APP_NOT_FOUND": 404,
    "ROBOT_VERSION_NOT_FOUND": 404,
    "RESOLVE_FAILED": 404,
    "ROBOT_APP_EXISTS": 409,
    "DRAFT_REVISION_CONFLICT": 409,
    "CONFIG_HASH_MISMATCH": 409,
    "ROBOT_NOT_PUBLISHED": 409,
    "CONFIG_VALIDATION_FAILED": 422,
    "UNSUPPORTED_MODEL_RUNTIME": 422,
    "UNSUPPORTED_RUNTIME_ENGINE": 422,
    "RUNTIME_ADAPTER_UNAVAILABLE": 422,
    "ASR_LANGUAGE_UNSUPPORTED": 422,
    "TTS_VOICE_UNSUPPORTED": 422,
    "TTS_VOICE_LANGUAGE_MISMATCH": 422,
    "OUTBOUND_TASK_NOT_FOUND": 404,
    "OUTBOUND_TASK_EXISTS": 409,
    "CONTACT_ID_CONFLICT": 409,
    "INVALID_RETRY_POLICY": 422,
    "VARIABLE_VALIDATION_FAILED": 422,
    "INVALID_PHONE_NUMBER": 422,
    "INVALID_CALLING_WINDOW": 422,
    "INVALID_TASK_SCHEDULE": 422,
    "INVALID_CALLBACK_URL": 422,
    "CONTACTS_REQUIRED": 422,
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
    "ADMIN_REVIEW_UNAVAILABLE": 502,
    "ADMIN_REVIEW_TIMEOUT": 504,
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
