# ruff: noqa: RUF001, RUF002, RUF003
"""
Фабрика клиента WB Marketplace API: `IntegrationKey` → `WbFbsClient`.

Единственная точка получения FBS-клиента — сервисы и джобы не лезут в
`IntegrationKey` сами.

Ключ выбирается ПО РЕЖИМУ контура (`settings.WB_FBS_MODE`):
  • `safe` / `prod` → `wb_marketplace` (боевой токен категории «Маркетплейс»);
  • `sandbox`       → `wb_marketplace_sandbox` (отдельный токен scope Test).
Фолбэка «нет ключа песочницы → возьмём боевой» НЕТ намеренно: боевой токен в
песочнице всё равно даст 401, а тихая подмена контура — ровно тот риск, от
которого режим и защищает.
"""

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import WB_FBS_MODE_SANDBOX
from backend.integrations.wb_fbs_api import WbFbsClient, current_mode
from backend.models import IntegrationKey
from backend.utils.crypto import decrypt

logger = structlog.get_logger("dds.wb_fbs")

# Категория токена WB «Маркетплейс» (боевой кабинет).
FBS_SERVICE = "wb_marketplace"
# Токен песочницы WB (scope Test) — отдельный контур, отдельные данные.
FBS_SANDBOX_SERVICE = "wb_marketplace_sandbox"


class FbsNotConfigured(Exception):
    """У проекта нет активного ключа под текущий режим (или он не расшифровывается)."""


def service_for_mode(mode: str | None = None) -> str:
    """Какой `IntegrationKey.service` кормит клиент в текущем режиме."""
    return FBS_SANDBOX_SERVICE if (mode or current_mode()) == WB_FBS_MODE_SANDBOX else FBS_SERVICE


async def get_fbs_api_key(db: AsyncSession, project_id: int, *, service: str | None = None) -> str | None:
    """Расшифрованный ключ FBS проекта (по умолчанию — соответствующий режиму).

    Скоуп как у `funnel.wb_api_client.get_wb_key`: сначала ключ самого проекта,
    затем глобальный (`project_id IS NULL`). Soft-deleted и выключенные — мимо.
    """
    target = service or service_for_mode()
    result = await db.execute(
        select(IntegrationKey)
        .where(
            or_(
                IntegrationKey.project_id == project_id,
                IntegrationKey.project_id.is_(None),
            ),
            IntegrationKey.service == target,
            IntegrationKey.is_active == True,  # noqa: E712
            IntegrationKey.is_deleted == False,  # noqa: E712
        )
        .order_by(IntegrationKey.project_id.desc().nullslast())
        .limit(1)
    )
    key = result.scalar_one_or_none()
    if not key:
        return None
    try:
        return decrypt(key.encrypted_key)
    except ValueError:
        # Ключ зашифрован другим SECRET_KEY (например, после sync-prod) — не наш случай
        # для ретрая; наружу это «не настроено», а не 500. Сам ключ не логируем.
        logger.warning("wb_fbs.key_decrypt_failed", project_id=project_id, key_id=key.id)
        return None


def _not_configured(project_id: int, service: str) -> FbsNotConfigured:
    """Внятный текст «нет ключа» — он уходит пользователю как есть (роутер → 409)."""
    if service == FBS_SANDBOX_SERVICE:
        return FbsNotConfigured(
            f"Режим «песочница»: у проекта {project_id} нет активного ключа "
            f"«{FBS_SANDBOX_SERVICE}». Песочница WB — отдельный контур со своими "
            "данными, нужен токен категории «Маркетплейс» со scope Test. Боевой ключ "
            "здесь не подойдёт (WB ответит 401), поэтому подставлять его молча нельзя. "
            "Добавьте ключ песочницы в Настройках → Интеграции."
        )
    return FbsNotConfigured(
        f"У проекта {project_id} нет активного ключа WB «Маркетплейс» "
        f"(интеграция «{service}»). Добавьте ключ в разделе «Интеграции»."
    )


async def get_fbs_client(db: AsyncSession, project_id: int) -> WbFbsClient:
    """Готовый клиент WB FBS под текущий режим. `FbsNotConfigured`, если ключа нет."""
    service = service_for_mode()
    api_key = await get_fbs_api_key(db, project_id, service=service)
    if not api_key:
        raise _not_configured(project_id, service)
    return WbFbsClient(api_key=api_key, project_id=project_id)


__all__ = [
    "FBS_SERVICE",
    "FBS_SANDBOX_SERVICE",
    "FbsNotConfigured",
    "service_for_mode",
    "get_fbs_api_key",
    "get_fbs_client",
]
