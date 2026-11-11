from contextlib import asynccontextmanager
from functools import partial

from dotenv import load_dotenv
from fastapi import FastAPI

from robot_config_service.api.console.static import register_console_static, verify_console_static
from robot_config_service.api.dependencies import default_uow_factory
from robot_config_service.api.errors import register_error_handlers
from robot_config_service.api.routes import register_routes
from robot_config_service.application.voice_test import VoiceTestSessionStore
from robot_config_service.capabilities.loader import load_catalog
from robot_config_service.infrastructure.database import Database
from robot_config_service.settings import Settings


def create_app(settings=None, database=None, uow_factory=None, validate_startup=True, console_static_dir=None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        settings.validate()
        if app.state.database is None:
            app.state.database = Database.create(settings.database_url)
        if validate_startup:
            app.state.database.verify()
            verify_console_static(app.state.console_static_dir)
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            if app.state.database is not None:
                app.state.database.close()

    app = FastAPI(title="Robot Config Service", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.catalog = load_catalog()
    app.state.ready = False
    app.state.voice_test_sessions = VoiceTestSessionStore()
    app.state.uow_factory = uow_factory or partial(default_uow_factory, app)
    register_error_handlers(app)
    register_routes(app)
    register_console_static(app, console_static_dir)
    return app


load_dotenv()
app = create_app()
