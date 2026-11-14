from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from fastcall_adapter.api.errors import register_error_handlers
from fastcall_adapter.api.routes import dispatch, hangup, health, media, review, sessions
from fastcall_adapter.infrastructure.crypto import phone_hmac_from_env, secret_codec_from_env
from fastcall_adapter.infrastructure.database import Database
from fastcall_adapter.infrastructure.uow import SqlAlchemyAdapterUnitOfWork
from fastcall_adapter.settings import Settings, result_changed_events_enabled


def _default_uow_factory(app):
    return SqlAlchemyAdapterUnitOfWork(
        app.state.database.session_factory(),
        result_changed_enabled=result_changed_events_enabled,
    )


def create_app(
    settings=None, database=None, uow_factory=None, validate_startup=True,
    secret_codec=None, phone_hmac=None, freeswitch_gateway=None,
    media_runtime=None, dispatcher=None,
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
        app.state.ready = True
        try:
            yield
        finally:
            app.state.ready = False
            if app.state.database is not None:
                app.state.database.close()

    app = FastAPI(title="Fast-Call Adapter", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.ready = False
    app.state.secret_codec = secret_codec
    app.state.phone_hmac = phone_hmac
    app.state.freeswitch_gateway = freeswitch_gateway
    app.state.media_runtime = media_runtime
    app.state.dispatcher = dispatcher
    app.state.uow_factory = uow_factory or partial(_default_uow_factory, app)
    register_error_handlers(app)
    for router in (
        health.router, dispatch.router, sessions.router,
        media.router, review.router, hangup.router,
    ):
        app.include_router(router)
    return app
