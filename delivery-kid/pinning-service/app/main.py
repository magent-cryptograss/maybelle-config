"""FastAPI application for delivery-kid pinning service."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .routes import health, albums

app = FastAPI(
    title="Delivery Kid Pinning Service",
    description="IPFS pinning and album upload service for CryptoGrass",
    version="1.0.0"
)

# Configure CORS
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"]
)

# Include routers
app.include_router(health.router)
app.include_router(albums.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {"service": "delivery-kid-pinning", "status": "running"}
