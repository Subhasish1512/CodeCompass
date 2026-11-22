"""
FastAPI application factory for CodeCompass.
"""

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from codecompass.api.routes import router
from codecompass.config import get_settings

# Timing middleware logger
timing_logger = logging.getLogger("codecompass.timing")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    get_settings()

    app = FastAPI(
        title="CodeCompass API",
        description="Code intelligence engine - search, navigate, and understand codebases",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json"
    )

    # CORS for local development (allow frontend on localhost:5173)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Timing middleware
    @app.middleware("http")
    async def timing_middleware(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        timing_logger.info(f"{request.method} {request.url.path} - {process_time:.3f}s")
        response.headers["X-Process-Time"] = str(process_time)
        return response

    # Include API routes
    app.include_router(router)

    @app.on_event("startup")
    async def startup():
        # Initialize database connection pool
        from codecompass.db import init_db
        init_db()

    @app.on_event("shutdown")
    async def shutdown():
        from codecompass.db import close_db
        close_db()

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "codecompass.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        workers=settings.api_workers,
        reload=False
    )