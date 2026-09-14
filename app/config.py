from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mongodb_uri: str = ""
    mongodb_db: str = "untangled_its"
    port: int = 10000
    frontend_url: str = "*"
    node_env: str = "production"
    dashboard_cache_ms: int = 15_000
    mongodb_max_pool_size: int = 20


@lru_cache
def get_settings() -> Settings:
    return Settings()
