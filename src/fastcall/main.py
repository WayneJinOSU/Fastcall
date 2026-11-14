from contextlib import asynccontextmanager
from functools import partial

from dotenv import load_dotenv
from fastapi import FastAPI

from fastcall.api.console.static import register_console_static, verify_console_static
from fastcall.api.errors import register_error_handlers
from fastcall.api.routes import register_routes
from fastcall.application.voice_test import VoiceTestSessionStore
from fastcall.application.webhooks import result_changed_events_enabled
from fastcall.capabilities.loader import load_catalog
from fastcall.infrastructure.database import Database
from fastcall.infrastructure.hangup_client import AdapterHangupClient
from fastcall.infrastructure.review_client import AdapterReviewClient
from fastcall.infrastructure.security import phone_hmac_from_env, secret_codec_from_env
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall.settings import Settings


def _default_uow_factory(app):
    return SqlAlchemyUnitOfWork(
        app.state.database.session_factory(),
        result_changed_enabled=result_changed_events_enabled,
    )


def create_app(
    settings=None, database=None, uow_factory=None, validate_startup=True,
    console_static_dir=None, secret_codec=None, media_runtime=None,
    phone_hmac=None, freeswitch_gateway=None, dispatcher=None, verify_console_assets=True,
    hangup_client=None, review_client=None,
):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        settings.validate()
        if app.state.database is None:
            app.state.database = Database.create(settings.database_url)
        if app.state.secret_codec is None:
            app.state.secret_codec = secret_codec_from_env()
        if app.state.phone_hmac is None:
            app.state.phone_hmac = phone_hmac_from_env()
        if validate_startup:
            app.state.database.verify()
            if verify_console_assets:
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
    app.state.secret_codec = secret_codec
    app.state.media_runtime = media_runtime
    app.state.phone_hmac = phone_hmac
    app.state.freeswitch_gateway = freeswitch_gateway
    app.state.dispatcher = dispatcher
    app.state.hangup_client = hangup_client or AdapterHangupClient(
        settings.adapter_internal_url, settings.adapter_token
    )
    app.state.review_client = review_client or AdapterReviewClient(
        settings.adapter_internal_url, settings.admin_token
    )
    app.state.uow_factory = uow_factory or partial(_default_uow_factory, app)
    register_error_handlers(app)
    register_routes(app)
    register_console_static(app, console_static_dir)
    return app


load_dotenv()
app = create_app()
