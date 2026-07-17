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

    # ЦБ РФ справочник БИК (ED807) — БИК→корр.счёт банка получателя для платёжек
    CBR_BIK_URL: str = "https://www.cbr.ru/s/newbik"  # отдаёт актуальный ED807-ZIP напрямую

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
    # ── Фулфилмент-синк (skladbot/wmscelicom/migfull) — тированное расписание ──
    # Один и тот же тиринг работает и в основном scheduler (прод), и в
    # FF-only scheduler (локалка). Три контура по складам:
    #   FAST  — приоритетные склады (CSV id) @ FAST_INTERVAL.
    #   SLOW  — отдельные склады (CSV id) @ SLOW_INTERVAL (пересечение с FAST
    #           исключается, чтобы склад не синкался дважды).
    #   DEFAULT — все остальные склады @ INTERVAL (= прежнее «раз в час»).
    #           На локалке (FF-only) DEFAULT-контур НЕ поднимается: БД там —
    #           копия прода (sync-prod) с ~2k замаскированных токенов, синк
    #           всех = шторм ошибок. Пустые FAST/SLOW → один DEFAULT для всех
    #           (прежнее прод-поведение, без изменений).
    FULFILLMENT_SYNC_INTERVAL_MINUTES: int = 60  # DEFAULT-контур (остальные склады)
    # Прод-приоритет (источник истины — здесь, не в compose-env): склады с
    # активной FF-интеграцией, которым нужен частый синк. id глобально уникальны:
    #   1 = натали (migfull), 2 = wms (wmscelicom), 12 = wms 2 (wmscelicom),
    #   5 = Газпром (skladbot). Все четыре синкаются каждые 10 минут.
    FULFILLMENT_SYNC_FAST_WAREHOUSE_IDS: str = "5,1,2,12"
    FULFILLMENT_SYNC_FAST_INTERVAL_MINUTES: int = 10
    FULFILLMENT_SYNC_SLOW_WAREHOUSE_IDS: str = ""
    FULFILLMENT_SYNC_SLOW_INTERVAL_MINUTES: int = 30
    # Поднять FF-only scheduler (только FF-джобы, без тяжёлых WB/Telegram),
    # когда основной выключен (SCHEDULER_ENABLED=false) — для локалки.
    # На проде игнорируется (там основной scheduler уже крутит тот же тиринг).
    FULFILLMENT_SYNC_ENABLED: bool = False

    # Автоподача жалоб на отзывы через WB API (POST /api/v1/feedbacks/actions).
    # WB ВРЕМЕННО отключил этот метод — жалобы подаются только через ЛК продавца.
    # Клиент готов; включать, когда WB объявит возврат метода. Учти лимит 1 rps.
    WB_FEEDBACK_COMPLAINTS_API: bool = False

    # Admin panel IP whitelist (comma-separated, empty = no restriction)
    ADMIN_ALLOWED_IPS: str = ""  # e.g. "1.2.3.4,2a01:e5c0:6dac::2"

    # Container role: api = HTTP serving, worker = scheduler + background tasks
    DDS_ROLE: str = "api"  # api | worker

    # Database pool (tuned for PgBouncer)
    DB_POOL_SIZE: int = 10  # connections per worker (2 workers × 10 = 20 base)
    DB_MAX_OVERFLOW: int = 15  # burst connections (2 workers × 25 = 50 max)

    # Rate limiting
    LOGIN_RATE_LIMIT: int = 10  # max attempts per minute per IP
    REGISTER_ENABLED: bool = False  # secure default: включать только явно в dev .env

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
        if os.getenv("CI") or os.getenv("TESTING") or os.getenv("DDS_ENV") == "testing":
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
