from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://dds:dds_secret@localhost:5432/dds_db"
    DATABASE_URL_SYNC: str = "postgresql://dds:dds_secret@localhost:5432/dds_db"

    # Auth
    SECRET_KEY: str = "change-me-in-production-use-a-strong-random-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours

    # CORS
    CORS_ORIGINS: str = "http://localhost:8501"

    class Config:
        env_file = ".env"


settings = Settings()
