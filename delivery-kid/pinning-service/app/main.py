"""FastAPI application for delivery-kid pinning service."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routes import health, albums, drafts, content
from .services import cleanup

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown tasks.

    On startup:
    - Run initial cleanup of expired drafts
    - Start periodic cleanup background task

    On shutdown:
    - Cancel background cleanup task
    """
    settings = get_settings()
    staging_dir = Path(settings.staging_dir)

    # Ensure staging directory exists
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "drafts").mkdir(exist_ok=True)

    # Run startup cleanup
    cleanup.startup_cleanup(staging_dir)

    # Start periodic cleanup task
    cleanup_task = asyncio.create_task(
        cleanup.periodic_cleanup(staging_dir, interval_seconds=3600)
    )

    logger.info("Delivery Kid pinning service started")
    yield

    # Shutdown: cancel cleanup task
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass
    logger.info("Delivery Kid pinning service stopped")


app = FastAPI(
    title="Delivery Kid Pinning Service",
    description="IPFS pinning and album upload service for CryptoGrass",
    version="1.0.0",
    lifespan=lifespan
)

# Configure CORS
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS", "DELETE"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Include routers
app.include_router(health.router)
app.include_router(albums.router)
app.include_router(drafts.router)
app.include_router(content.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {"service": "delivery-kid-pinning", "status": "running"}
