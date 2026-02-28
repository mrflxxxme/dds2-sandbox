from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://dds:dds_secret@localhost:5432/dds_db"
    DATABASE_URL_SYNC: str = "postgresql://dds:dds_secret@localhost:5432/dds_db"

    # Auth
    SECRET_KEY: str = "change-me-in-production-use-a-strong-random-key"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 hours

    # CORS
    CORS_ORIGINS: str = "http://localhost:8501"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO (S3-compatible storage)
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "dds-files"
    MINIO_SECURE: bool = False

    class Config:
        env_file = ".env"


settings = Settings()
