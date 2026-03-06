"""Configuration settings loaded from environment variables."""

from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Service identity
    node_name: str = "delivery-kid"
    port: int = 3001

    # IPFS
    ipfs_api_url: str = "http://ipfs:5001"
    ipfs_gateway_url: str = "https://ipfs.delivery-kid.cryptograss.live"

    # Pinata backup
    pinata_jwt: str = ""

    # Staging directory for uploads and transcoding
    staging_dir: str = "/staging"

    # Authorized wallets (comma-separated)
    authorized_wallets: str = ""

    # Auth settings
    max_timestamp_drift_seconds: int = 600  # 10 minutes

    # Upload limits
    max_file_size_mb: int = 50000  # 50GB - effectively no limit for albums
    max_files_per_upload: int = 50

    # Draft settings
    draft_ttl_hours: int = 24  # How long drafts live before auto-cleanup
    max_staging_size_gb: int = 10  # Maximum total size of staging directory

    # CORS
    cors_origins: list[str] = [
        "https://cryptograss.live",
        "https://www.cryptograss.live",
    ]

    class Config:
        env_file = ".env"

    @property
    def authorized_wallet_list(self) -> list[str]:
        """Parse comma-separated wallet addresses into a list."""
        if not self.authorized_wallets:
            return []
        return [w.strip().lower() for w in self.authorized_wallets.split(",") if w.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
