"""FastAPI ASGI entrypoint.

Exposes:
- /api/v1/*    REST API
- /ws/*        websocket gateway
- /health      liveness
- /health/deep readiness
- /metrics     Prometheus
- /docs        Swagger UI
"""

from __future__ import annotations

import contextlib
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware

from .api.v1.router import api_router
from .core.config import settings
from .core.logging import configure_logging, logger
from .websocket.gateway import ws_router

configure_logging()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", environment=settings.ENVIRONMENT)
    # Auto-load operator's Chart Champions methodology in single-user mode.
    # Runs without blocking startup — ingestion happens in background tasks.
    try:
        from .services.bootstrap import autoload_methodology
        await autoload_methodology()
    except Exception as exc:
        logger.warning("methodology autoload skipped: %s", exc)
    yield
    logger.info("shutdown")


app = FastAPI(
    title="CC Trader API",
    description="Chart-Champions-driven AI trading intelligence",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ---- CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- request-id middleware
class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = rid
        import structlog

        with structlog.contextvars.bound_contextvars(request_id=rid, path=request.url.path):
            response = await call_next(request)
        response.headers["x-request-id"] = rid
        return response


app.add_middleware(RequestIDMiddleware)


# ---- generic exception handler
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled_error", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error", "request_id": getattr(request.state, "request_id", None)},
    )


# ---- health
@app.get("/health", tags=["meta"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/deep", tags=["meta"])
async def health_deep() -> dict[str, str]:
    # In a real readiness probe we would ping postgres, redis, chroma here.
    return {"status": "ok"}


@app.get("/metrics", tags=["meta"])
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---- routers
app.include_router(api_router, prefix="/api/v1")
app.include_router(ws_router)
