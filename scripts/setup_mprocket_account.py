# ruff: noqa: RUF002, RUF003, T201
"""
Idempotent setup of mprocket («Нитропак», seller.mprocket.ru) credentials.

Регистрирует фулфилмент-провайдера «mprocket» на складе: IntegrationKey(
service="mprocket", label="warehouse:{id}") с паролем под Fernet,
config={"login", "business_id", "host"} и warehouse_id = склад вяткина, к
которому привязан кабинет Нитропака. После этого синк остатков подхватывает
склад автоматически (provider в FF_SERVICES/SYNCABLE_FF_SERVICES).

Источник пароля (по приоритету): --password-stdin | env MPROCKET_PASSWORD | getpass.

Usage:
  # список складов проекта — найти id нужного
  docker compose exec -T backend python scripts/setup_mprocket_account.py --project default --list-warehouses

  MPROCKET_PASSWORD='***' docker compose exec -T backend \
    python scripts/setup_mprocket_account.py --project default \
      --login +79969190097 --business-id 306 --warehouse-id N \
      --validate --commit
  # dry-run (по умолчанию) — печатает план и откатывает.
"""

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from backend.database import get_db
from backend.integrations.mprocket_client import BASE_URL, MprocketClient
from backend.models import IntegrationKey, Warehouse
from backend.models.auth import Project
from backend.utils.crypto import encrypt as _encrypt

SERVICE = "mprocket"


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        pw = sys.stdin.readline().strip()
    else:
        pw = os.environ.get("MPROCKET_PASSWORD") or getpass.getpass("Mprocket password: ")
    if not pw:
        print("ERROR: empty password", file=sys.stderr)
        sys.exit(2)
    return pw


async def _validate(login: str, password: str, business_id: str, host: str) -> None:
    async with MprocketClient(login, password, business_id, host=host) as client:
        ok = await client.test_connection()
        if not ok:
            print("ERROR: Mprocket auth FAILED — проверьте телефон/пароль", file=sys.stderr)
            sys.exit(3)
        stock = await client.fetch_all_products()
    print(f"  ✓ auth OK; остатки выгружены: {len(stock)} позиций")
    for row in stock[:3]:
        print(f"      - {row.get('barcode')}  {row.get('name')}  на складе={row.get('qty_stock')}")


async def _list_warehouses(slug: str) -> int:
    async for db in get_db():
        proj = (
            await db.execute(select(Project).where(Project.slug == slug, Project.is_deleted == False))  # noqa: E712
        ).scalar_one_or_none()
        if proj is None:
            print(f"ERROR: project slug {slug!r} not found", file=sys.stderr)
            return 1
        rows = (
            await db.execute(
                select(Warehouse.id, Warehouse.name).where(
                    Warehouse.project_id == proj.id, Warehouse.is_deleted == False  # noqa: E712
                )
            )
        ).all()
        print(f"Склады проекта {slug!r}:")
        for wid, name in rows:
            print(f"  id={wid:>4}  {name}")
        return 0
    return 1


async def main(
    slug: str,
    login: str,
    password: str,
    business_id: str,
    warehouse_id: int,
    host: str,
    validate: bool,
    commit: bool,
) -> int:
    if validate:
        print("Validating credentials against Mprocket...")
        await _validate(login, password, business_id, host)

    async for db in get_db():
        proj = (
            await db.execute(select(Project).where(Project.slug == slug, Project.is_deleted == False))  # noqa: E712
        ).scalar_one_or_none()
        if proj is None:
            print(f"ERROR: project slug {slug!r} not found", file=sys.stderr)
            return 1

        wh = (
            await db.execute(
                select(Warehouse).where(
                    Warehouse.id == warehouse_id,
                    Warehouse.project_id == proj.id,
                    Warehouse.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()
        if wh is None:
            print(
                f"ERROR: warehouse id={warehouse_id} not found in project {slug!r} (см. --list-warehouses)",
                file=sys.stderr,
            )
            return 1

        # FF-конвенция: один ключ на склад, label="warehouse:{id}"
        label = f"warehouse:{warehouse_id}"
        existing = (
            await db.execute(
                select(IntegrationKey).where(
                    IntegrationKey.project_id == proj.id,
                    IntegrationKey.service == SERVICE,
                    IntegrationKey.label == label,
                )
            )
        ).scalar_one_or_none()

        config = {"login": login, "business_id": str(business_id), "host": host}
        if existing is None:
            db.add(
                IntegrationKey(
                    project_id=proj.id,
                    service=SERVICE,
                    label=label,
                    encrypted_key=_encrypt(password),
                    is_active=True,
                    warehouse_id=warehouse_id,
                    config=config,
                )
            )
            print(f"  + created Mprocket key for {slug!r} (login={login}, business_id={business_id}, warehouse={wh.name!r})")
        else:
            existing.restore()
            existing.encrypted_key = _encrypt(password)
            existing.is_active = True
            existing.warehouse_id = warehouse_id
            existing.config = config
            print(f"  ~ updated Mprocket key (id={existing.id}) for {slug!r} (warehouse={wh.name!r})")

        if commit:
            await db.commit()
            print("\nCOMMITTED.")
        else:
            await db.rollback()
            print("\nDRY-RUN — rolled back. Re-run with --commit to apply.")
        return 0
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Setup Mprocket («Нитропак») credentials")
    ap.add_argument("--project", default="default", help="project slug")
    ap.add_argument("--login", default=os.environ.get("MPROCKET_LOGIN", ""), help="телефон-логин, напр. +79969190097")
    ap.add_argument("--business-id", default=os.environ.get("MPROCKET_BUSINESS_ID", ""), help="id бизнеса в кабинете")
    ap.add_argument("--warehouse-id", type=int, default=0, help="id склада вяткина (см. --list-warehouses)")
    ap.add_argument("--host", default=BASE_URL)
    ap.add_argument("--password-stdin", action="store_true", help="read password from stdin")
    ap.add_argument("--validate", action="store_true", help="live-auth + выгрузка остатков перед сохранением")
    ap.add_argument("--list-warehouses", action="store_true", help="печать складов проекта и выход")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list_warehouses:
        sys.exit(asyncio.run(_list_warehouses(args.project)))

    if not args.login:
        print("ERROR: login required (--login or MPROCKET_LOGIN)", file=sys.stderr)
        sys.exit(2)
    if not args.business_id:
        print("ERROR: business-id required (--business-id or MPROCKET_BUSINESS_ID)", file=sys.stderr)
        sys.exit(2)
    if not args.warehouse_id:
        print("ERROR: warehouse-id required (--warehouse-id; см. --list-warehouses)", file=sys.stderr)
        sys.exit(2)
    pw = _read_password(args.password_stdin)
    rc = asyncio.run(
        main(
            args.project,
            args.login,
            pw,
            args.business_id,
            args.warehouse_id,
            args.host,
            args.validate,
            commit=args.commit,
        )
    )
    sys.exit(rc)
