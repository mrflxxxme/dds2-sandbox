from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://dds:dds_secret@localhost:5432/dds_db"
    DATABASE_URL_SYNC: str = "postgresql://dds:dds_secret@localhost:5432/dds_db"

    class Config:
        env_file = ".env"


settings = Settings()
