# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Одноразовый скрипт: зарегистрировать WB API-ключ с scope «Вопросы и отзывы».

Ключ НЕ хранится в файле — передаётся через переменную окружения WB_KEY_TO_REGISTER:
    docker compose exec -e WB_KEY_TO_REGISTER=... backend \
        python scripts/register_wb_key.py --project-id 1000039

Скрипт шифрует ключ (utils/crypto, AES-256 Fernet) и делает upsert IntegrationKey
(service='wb_feedbacks', label='WB Feedbacks (локалка)') с восстановлением
soft-deleted строки — паттерн integrations_service. Затем прогоняет
check_feedbacks_scope и печатает ok/no_scope/unknown (сам ключ НЕ выводится).
"""

import argparse
import asyncio
import os
import sys

from sqlalchemy import select

from backend.database import AsyncSessionLocal
from backend.models import IntegrationKey
from backend.utils.crypto import encrypt

SERVICE = "wb_feedbacks"
LABEL = "WB Feedbacks (локалка)"


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, required=True)
    args = parser.parse_args()

    api_key = (os.environ.get("WB_KEY_TO_REGISTER") or "").strip()
    if not api_key:
        print("ERROR: передайте ключ через env WB_KEY_TO_REGISTER", file=sys.stderr)
        return 2

    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(IntegrationKey).where(
                    IntegrationKey.project_id == args.project_id,
                    IntegrationKey.service == SERVICE,
                    IntegrationKey.label == LABEL,
                )
            )
        ).scalar_one_or_none()

        encrypted = encrypt(api_key)
        if existing is not None:
            existing.encrypted_key = encrypted
            existing.is_active = True
            if existing.is_deleted:
                existing.restore()
            key = existing
        else:
            key = IntegrationKey(
                project_id=args.project_id,
                service=SERVICE,
                label=LABEL,
                encrypted_key=encrypted,
                is_active=True,
            )
            db.add(key)
        await db.commit()
        print(f"OK: ключ сохранён (integration_key id={key.id}, service={SERVICE}, project_id={args.project_id})")

    # Проверка scope — лёгкий пробник GET /api/v1/feedbacks?take=1
    from backend.integrations.wb_api import check_feedbacks_scope

    scope = await check_feedbacks_scope(api_key)
    print(f"check_feedbacks_scope: {scope}")
    return 0 if scope == "ok" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
