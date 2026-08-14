from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import settings
from app.db.session import engine
from app.models import Base  # noqa: F401 — import all models to register them


def create_app() -> FastAPI:
    application = FastAPI(
        title="Incident Commander",
        description="Real-time AI-powered incident response platform",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routes ────────────────────────────────────────────────────────────────
    from app.api.health import router as health_router

    application.include_router(health_router, prefix="/health", tags=["health"])

    return application


app = create_app()
