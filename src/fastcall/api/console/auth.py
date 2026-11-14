import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from fastcall_core.domain.errors import RobotConfigError


COOKIE_NAME = "robot_config_console_session"
SESSION_TTL_SECONDS = 8 * 60 * 60


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    actor_id: str
    password: str


@dataclass(frozen=True)
class ConsoleSession:
    actor: str
    csrf_token: str


def hash_password(password: str, salt=None, iterations=310000) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hash_password(password, base64.urlsafe_b64decode(salt), int(iterations))
        return hmac.compare_digest(actual, encoded)
    except (ValueError, TypeError):
        return False


def _encode_part(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode_part(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session_token(actor: str, secret: str, now=None) -> tuple[str, str]:
    csrf = secrets.token_urlsafe(24)
    payload = {
        "actor": actor,
        "csrf": csrf,
        "exp": int((now or time.time()) + SESSION_TTL_SECONDS),
        "nonce": secrets.token_urlsafe(12),
    }
    encoded = _encode_part(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _encode_part(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}", csrf


def decode_session_token(token: str, secret: str) -> ConsoleSession:
    try:
        encoded, signature = token.split(".", 1)
        expected = _encode_part(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(_decode_part(encoded))
        if payload["exp"] <= time.time():
            raise ValueError("expired")
        return ConsoleSession(actor=payload["actor"], csrf_token=payload["csrf"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        raise RobotConfigError("CONSOLE_UNAUTHORIZED", "Console session is invalid or expired")


def console_session(request: Request) -> ConsoleSession:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token:
        raise RobotConfigError("CONSOLE_UNAUTHORIZED", "Console login is required")
    return decode_session_token(token, request.app.state.settings.console_session_secret)


def console_write_session(
    request: Request,
    x_csrf_token: str | None = Header(default=None),
) -> ConsoleSession:
    session = console_session(request)
    if not x_csrf_token or not hmac.compare_digest(x_csrf_token, session.csrf_token):
        raise RobotConfigError("CONSOLE_CSRF_FAILED", "CSRF token is invalid")
    return session


def login_response(payload: LoginRequest, request: Request) -> JSONResponse:
    settings = request.app.state.settings
    actor_valid = hmac.compare_digest(payload.actor_id, settings.console_admin_actor_id)
    password_valid = verify_password(payload.password, settings.console_admin_password_hash)
    if not actor_valid or not password_valid:
        raise RobotConfigError("CONSOLE_UNAUTHORIZED", "Invalid console credentials")
    token, csrf = create_session_token(payload.actor_id, settings.console_session_secret)
    response = JSONResponse({"actor_id": payload.actor_id, "csrf_token": csrf})
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.app_env == "prod",
        samesite="lax",
        path="/",
    )
    return response


def logout_response(request: Request) -> JSONResponse:
    console_write_session(request, request.headers.get("x-csrf-token"))
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response
