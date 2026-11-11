from fastapi import FastAPI

from robot_config_service.api import health, management, resolve
from robot_config_service.api.console import routes as console


def register_routes(app: FastAPI):
    app.include_router(health.router)
    app.include_router(management.router)
    app.include_router(resolve.router)
    app.include_router(console.router)
