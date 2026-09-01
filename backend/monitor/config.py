from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    radar_token: str = ""
    timezone: str = "Europe/Warsaw"
    allowed_origins: tuple[str, ...] = ("http://localhost:3180", "http://127.0.0.1:3180")
    worker_lease_seconds: int = 900
    data_dir: str = "/app/data"

    @classmethod
    def from_env(cls) -> "Settings":
        url = os.getenv("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL is required. Use scripts/manage.py to configure local services.")
        return cls(
            database_url=url,
            radar_token=os.getenv("CLOUDFLARE_RADAR_TOKEN", ""),
            timezone=os.getenv("MONITOR_TIMEZONE", "Europe/Warsaw"),
            data_dir=os.getenv("MONITOR_DATA_DIR", "/app/data"),
        )
