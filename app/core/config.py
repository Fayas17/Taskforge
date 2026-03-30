from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # App
    APP_NAME: str
    DEBUG: bool

    # CORS
    CORS_ORIGINS: str

    # Frontend Url
    FRONTEND_URL: str

    # Database
    POSTGRES_USER: str
    POSTGRES_PASSWORD: str
    POSTGRES_HOST: str
    POSTGRES_PORT: int
    POSTGRES_DB: str

    # Redis
    REDIS_URL: str
    REDIS_HOST: str
    REDIS_PORT: int

    # File Upload
    UPLOAD_DIR: str = "uploads"

    # Celery
    CELERY_BROKER_URL: str
    CELERY_RESULT_BACKEND: str
    CSV_CHUNK_SIZE: int = 100
    CSV_TASK_SOFT_TIME_LIMIT: int = 600
    CSV_TASK_MAX_RETRIES: int = 3
    CSV_TASK_RETRY_DELAY: int = 60

    # JWT
    SECRET_KEY: str
    ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    REFRESH_TOKEN_EXPIRE_DAYS: int

    # Rate Limits
    RATE_LIMIT_REGISTER: str = "3/minute"
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_REFRESH: str = "10/minute"
    RATE_LIMIT_JOBS_UPLOAD: str = "5/minute"

    # Cookies
    COOKIE_SECURE: bool
    HTTP_ONLY: bool
    COOKIE_SAMESITE: Literal["lax", "strict", "none"]
    ACCESS_TOKEN_COOKIE_MAX_AGE: int
    REFRESH_TOKEN_COOKIE_MAX_AGE: int

    # GOOGLE OAuth
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str

    @property
    def database_url(self) -> str:
        """Async database URL (asyncpg driver) — used by the application."""
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:"
            f"{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def sync_database_url(self) -> str:
        """Sync database URL (psycopg2 driver) — used by Alembic migrations only."""
        return (
            f"postgresql+psycopg2://{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:"
            f"{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
