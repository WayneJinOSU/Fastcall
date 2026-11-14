from fastapi import FastAPI

from fastcall.api import health, management, resolve
from fastcall.api.console import routes as console
from fastcall.api.console import voice_test
from fastcall.api.openapi import outbound
from fastcall.api.adminapi import calls


def register_routes(app: FastAPI):
    app.include_router(health.router)
    app.include_router(management.router)
    app.include_router(resolve.router)
    app.include_router(console.router)
    app.include_router(voice_test.router)
    app.include_router(outbound.router)
    app.include_router(calls.router)
