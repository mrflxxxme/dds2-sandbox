import secrets
import warnings

from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://dds:dds_secret@localhost:5432/dds_db"
    DATABASE_URL_SYNC: str = "postgresql://dds:dds_secret@localhost:5432/dds_db"

    # Auth
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30  # 30 min (short-lived, refresh extends session)
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30  # 30 days
    MIN_PASSWORD_LENGTH: int = 8

    # Environment
    DDS_ENV: str = "development"  # development | staging | production
    SENTRY_DSN: str = ""  # Empty = Sentry disabled

    # Telegram alerts
    TELEGRAM_BOT_TOKEN: str = ""  # From @BotFather
    TELEGRAM_CHAT_ID: str = ""  # Chat/group ID for alerts

    # Anthropic (Claude AI)
    ANTHROPIC_API_KEY: str = ""  # Claude API key for AI agent
    AI_MEMORY_DIR: str = "/data/ai_memory"  # persistent AI memory storage

    # Telegram analytics bot
    TELEGRAM_BOT_TOKEN_ANALYTICS: str = ""  # @dds_analytics_bot token
    TELEGRAM_WEBHOOK_SECRET: str = ""  # X-Telegram-Bot-Api-Secret-Token
    TELEGRAM_USE_POLLING: bool = False  # True for local dev (no public URL)
    TELEGRAM_PROXY: str = ""  # HTTP proxy for Telegram API (e.g. http://user:pass@host:port)

    # CORS
    CORS_ORIGINS: str = "http://localhost:8501,http://localhost:3000"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # MinIO (S3-compatible storage)
    MINIO_ENDPOINT: str = "localhost:9000"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "dds-files"
    MINIO_SECURE: bool = False

    # Scheduler
    SCHEDULER_ENABLED: bool = True  # False = no background sync (for dev when server is also running)

    # Admin panel IP whitelist (comma-separated, empty = no restriction)
    ADMIN_ALLOWED_IPS: str = ""  # e.g. "1.2.3.4,2a01:e5c0:6dac::2"

    # Container role: api = HTTP serving, worker = scheduler + background tasks
    DDS_ROLE: str = "api"  # api | worker

    # Database pool (tuned for PgBouncer)
    DB_POOL_SIZE: int = 10  # connections per worker (2 workers × 10 = 20 base)
    DB_MAX_OVERFLOW: int = 15  # burst connections (2 workers × 25 = 50 max)

    # Rate limiting
    LOGIN_RATE_LIMIT: int = 10  # max attempts per minute per IP
    REGISTER_ENABLED: bool = True  # disable open registration

    # File upload limits
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_UPLOAD_EXTENSIONS: str = ".xlsx,.xls,.csv,.pdf"

    # Feature flags (toggle per environment)
    FEATURE_DEMO_MODE: bool = False  # Show demo banner, enable seed data
    FEATURE_WB_SYNC: bool = True  # WB API sync enabled
    FEATURE_EXPORT_PDF: bool = False  # PDF export (beta)

    @field_validator("MINIO_ACCESS_KEY", "MINIO_SECRET_KEY")
    @classmethod
    def validate_minio_credentials(cls, v: str, info) -> str:
        import os

        # Skip validation in CI/test environments
        if os.getenv("CI") or os.getenv("DDS_ENV") == "testing":
            return v
        if not v or v in ("", "minioadmin"):
            raise ValueError(
                f"{info.field_name} не задан или использует дефолтное значение 'minioadmin'! "
                f"Укажите безопасное значение в .env. "
                f'Для генерации: python3 -c "import secrets; print(secrets.token_urlsafe(24))"'
            )
        return v

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        import os

        env = os.getenv("DDS_ENV", "development")
        if not v or v in (
            "",
            "change-me-in-production-use-a-strong-random-key",
            "change-me-to-a-random-64-char-string",
        ):
            if env == "production":
                raise ValueError(
                    "SECRET_KEY MUST be set in production! "
                    'Generate with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
                )
            generated = secrets.token_urlsafe(48)
            warnings.warn(
                f"\n⚠️  SECRET_KEY не задан! Сгенерирован временный ключ.\n"
                f"   Добавьте в .env: SECRET_KEY={generated}\n"
                f"   Без фиксированного ключа JWT-токены будут недействительны при перезапуске!",
                UserWarning,
                stacklevel=2,
            )
            return generated
        if len(v) < 32:
            warnings.warn(
                "⚠️  SECRET_KEY слишком короткий (< 32 символов). Используйте длинный случайный ключ.",
                UserWarning,
                stacklevel=2,
            )
        return v

    class Config:
        env_file = ".env"


settings = Settings()
