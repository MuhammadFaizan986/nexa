"""
NEXA API entry point.

    uvicorn app.main:app --reload

Interactive API docs (generated from the code by FastAPI): /docs
"""

from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import access, admin, auth, chat, collections, documents, health, search
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.db.session import engine
from app.services.storage import get_storage

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    configure_logging()
    settings = get_settings()
    get_storage()  # creates the upload directory if needed
    get_logger("app").info(
        "app.started",
        environment=settings.environment,
        embedding_provider=settings.embedding_provider,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm_model if settings.llm_provider != "fake" else "fake-llm",
    )
    yield
    # Shutdown: close pooled database connections cleanly.
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="NEXA API",
        description="Multi-tenant AI knowledge base (RAG) with citations and permissions.",
        version="0.4.0",  # matches the plan's week number
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    # The web UI (Week 5) runs on another origin and needs to read the streaming
    # response and our own headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    api = APIRouter(prefix=API_PREFIX)
    for module in (health, auth, access, admin, collections, documents, search, chat):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
