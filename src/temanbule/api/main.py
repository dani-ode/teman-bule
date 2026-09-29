"""FastAPI application composition root (FND-01/FND-02)."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from temanbule.api.middleware import BodySizeLimitMiddleware, RequestIdMiddleware
from temanbule.api.routers import auth as auth_router
from temanbule.api.routers import billing as billing_router
from temanbule.api.routers import byok as byok_router
from temanbule.api.routers import calls as calls_router
from temanbule.api.routers import google_auth as google_auth_router
from temanbule.api.routers import health as health_router
from temanbule.api.routers import internal_runtime as internal_runtime_router
from temanbule.api.routers import learning as learning_router
from temanbule.api.routers import media as media_router
from temanbule.api.routers import plans as plans_router
from temanbule.api.routers import podcasts as podcasts_router
from temanbule.api.routers import practice as practice_router
from temanbule.api.routers import profile as profile_router
from temanbule.api.routers import toefl as toefl_router
from temanbule.api.routers import vocabulary as vocabulary_router
from temanbule.api.routers import webhooks as webhooks_router
from temanbule.modules.billing.xendit_adapter import build_xendit_checkout
from temanbule.modules.calls.livekit_adapter import build_livekit_token_factory
from temanbule.modules.catalog.credential_verifier import build_credential_verifier
from temanbule.modules.media.s3_adapter import (
    build_download_url_factory,
    build_upload_url_factory,
)
from temanbule.platform.db.engine import create_engine, create_session_factory
from temanbule.platform.errors import AppError
from temanbule.platform.logging import configure_logging, get_request_id
from temanbule.platform.settings import Settings, load_settings

logger = logging.getLogger(__name__)


def _error_envelope(
    code: str, message: str, details: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "request_id": get_request_id(),
            "details": details or [],
        }
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings(validate=True)
    configure_logging(settings.app_log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        engine = create_engine(settings)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        logger.info("app_started")
        yield
        await engine.dispose()
        logger.info("app_stopped")

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        docs_url="/docs" if settings.app_enable_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.app_enable_docs else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Adapter konkret (DEC-08/DEC-14/DEC-15); dipasang kondisional per feature
    # flag — router gagal eksplisit bila adapter belum terpasang.
    if settings.feature_advance_enabled:
        app.state.credential_verifier = build_credential_verifier(settings)
    if settings.feature_media_enabled:
        app.state.media_upload_url_factory = build_upload_url_factory(settings)
        app.state.media_download_url_factory = build_download_url_factory(settings)
    if settings.feature_realtime_call_enabled:
        app.state.livekit_token_factory = build_livekit_token_factory(settings)
    if settings.feature_billing_enabled:
        app.state.xendit_checkout = build_xendit_checkout(settings)

    # Middleware
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.app_max_request_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Exception handlers
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_envelope(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        details = [
            {
                "field": ".".join(str(loc) for loc in err.get("loc", [])),
                "message": err.get("msg", ""),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_error_envelope("VALIDATION_FAILED", "Input tidak valid.", details),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content=_error_envelope("INTERNAL", "Terjadi kesalahan internal."),
        )

    # Routers
    app.include_router(health_router.router)
    app.include_router(auth_router.router)
    app.include_router(google_auth_router.router)
    app.include_router(profile_router.router)
    app.include_router(plans_router.router)
    app.include_router(billing_router.router)
    app.include_router(byok_router.router)
    app.include_router(practice_router.router)
    app.include_router(vocabulary_router.router)
    app.include_router(learning_router.router)
    app.include_router(toefl_router.router)
    app.include_router(media_router.router)
    app.include_router(calls_router.router)
    app.include_router(podcasts_router.router)
    app.include_router(webhooks_router.router)
    app.include_router(internal_runtime_router.router)

    return app


app = create_app()
