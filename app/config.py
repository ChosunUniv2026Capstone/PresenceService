from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "presence-service"
    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8001

    redis_host: str = Field(default="redis", alias="REDIS_HOST")
    redis_port: int = Field(default=6379, alias="REDIS_PORT")
    redis_db: int = Field(default=0, alias="REDIS_DB")
    snapshot_ttl_seconds: int = Field(default=60, alias="SNAPSHOT_TTL_SECONDS")
    refresh_lock_seconds: int = Field(default=15, alias="REFRESH_LOCK_SECONDS")
    collector_push_enabled: bool = Field(default=True, alias="COLLECTOR_PUSH_ENABLED")
    collector_offline_after_seconds: int = Field(default=10, alias="COLLECTOR_OFFLINE_AFTER_SECONDS")
    collector_timestamp_window_seconds: int = Field(default=60, alias="COLLECTOR_TIMESTAMP_WINDOW_SECONDS")
    registry_cache_ttl_seconds: int = Field(default=5, alias="REGISTRY_CACHE_TTL_SECONDS")
    backend_service_url: str = Field(default="http://backend:8000", alias="BACKEND_SERVICE_URL")
    presence_internal_token: str = Field(default="smart-class-dev-internal-token", alias="PRESENCE_INTERNAL_TOKEN")
    ap_token_hash_secret: str = Field(default="smart-class-dev-ap-token-pepper", alias="AP_TOKEN_HASH_SECRET")

    dummy_snapshot_path: Path = Field(
        default=Path(__file__).resolve().parent / "dummy_data" / "classroom_snapshots.json",
        alias="DUMMY_SNAPSHOT_PATH",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
