from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://nexus:nexus@localhost:5432/nexus"
    REDIS_URL: str = "redis://localhost:6379/0"
    CHROMA_PERSIST_DIR: str = "./chroma_data"

    GROQ_API_KEY: str = ""
    GROQ_MODEL_LARGE: str = "llama-3.3-70b-versatile"
    GROQ_MODEL_SMALL: str = "llama-3.1-8b-instant"

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-1.5-flash"

    TAVILY_API_KEY: str = ""

    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    MAIL_SERVER: str = ""
    MAIL_PORT: int = 587
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = ""

    FREE_TIER_BRIEF_LIMIT: int = 5
    FREE_TIER_DEPTH_LIMIT: str = "standard"

    REPORTS_DIR: str = "/tmp/nexus_reports"
    APP_BASE_URL: str = "http://127.0.0.1:8099"
    LOW_CONFIDENCE_ALERT_THRESHOLD: float = 0.6

    JWT_SECRET: str = "change-me-in-prod"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 60

    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
