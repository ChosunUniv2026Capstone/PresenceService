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

    dummy_snapshot_path: Path = Field(
        default=Path(__file__).resolve().parent / "dummy_data" / "classroom_snapshots.json",
        alias="DUMMY_SNAPSHOT_PATH",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
