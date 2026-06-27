# ruff: noqa: RUF002, RUF003, T201
"""
Idempotent setup of migfull-портал (plusvb.migfull.app) credentials for a project.

Сохраняет IntegrationKey(service="migfull_portal") с Fernet-шифрованным паролем,
config={"login", "host"} и warehouse_id = склад «Натали» (гейт: кнопку «Создать
заявку в ФФ Натали» показываем только для сборок с него).

НЕ путать с read-only API-ключом (service="migfull", Bearer-токен).

Источник пароля (по порядку): --password-stdin | env MIGFULL_PORTAL_PASSWORD | getpass.

Usage:
  # список складов проекта, чтобы найти id «Натали»
  docker compose exec -T backend python scripts/setup_migfull_portal_account.py --project default --list-warehouses

  MIGFULL_PORTAL_PASSWORD='***' docker compose exec -T backend \
    python scripts/setup_migfull_portal_account.py --project default \
      --login ohotnikova1010@gmail.com --warehouse-id 1 \
      --host https://plusvb.migfull.app --validate --commit
  # dry-run (по умолчанию) — печатает план и откатывает.
"""

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from backend.database import get_db
from backend.integrations.migfull_portal_client import BASE_URL, MigfullPortalClient
from backend.models import IntegrationKey, Warehouse
from backend.models.auth import Project
from backend.utils.crypto import encrypt as _encrypt

SERVICE = "migfull_portal"


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        pw = sys.stdin.readline().strip()
    else:
        pw = os.environ.get("MIGFULL_PORTAL_PASSWORD") or getpass.getpass("migfull portal password: ")
    if not pw:
        print("ERROR: empty password", file=sys.stderr)
        sys.exit(2)
    return pw


async def _validate(login: str, password: str, host: str) -> None:
    async with MigfullPortalClient(login, password, host=host) as client:
        ok = await client.test_connection()
    if not ok:
        print("ERROR: migfull portal auth FAILED — проверьте логин/пароль", file=sys.stderr)
        sys.exit(3)
    print("  ✓ auth OK (логин в портал прошёл)")


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


async def main(slug: str, login: str, password: str, warehouse_id: int, host: str, validate: bool, commit: bool) -> int:
    if validate:
        print("Validating credentials against migfull portal...")
        await _validate(login, password, host)

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

        existing = (
            await db.execute(
                select(IntegrationKey).where(
                    IntegrationKey.project_id == proj.id,
                    IntegrationKey.service == SERVICE,
                    IntegrationKey.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

        config = {"login": login, "host": host}
        if existing is None:
            db.add(
                IntegrationKey(
                    project_id=proj.id,
                    service=SERVICE,
                    label=login,
                    encrypted_key=_encrypt(password),
                    is_active=True,
                    warehouse_id=warehouse_id,
                    config=config,
                )
            )
            print(f"  + created migfull-portal key for {slug!r} (login={login}, warehouse={wh.name!r})")
        else:
            existing.label = login
            existing.encrypted_key = _encrypt(password)
            existing.is_active = True
            existing.warehouse_id = warehouse_id
            existing.config = config
            print(f"  ~ updated migfull-portal key (id={existing.id}) for {slug!r} (warehouse={wh.name!r})")

        if commit:
            await db.commit()
            print("\nCOMMITTED.")
        else:
            await db.rollback()
            print("\nDRY-RUN — rolled back. Re-run with --commit to apply.")
        return 0
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Setup migfull-портал (plusvb.migfull.app) credentials")
    ap.add_argument("--project", default="default", help="project slug")
    ap.add_argument("--login", default=os.environ.get("MIGFULL_PORTAL_LOGIN", ""))
    ap.add_argument("--warehouse-id", type=int, default=0, help="id склада «Натали» (см. --list-warehouses)")
    ap.add_argument("--host", default=BASE_URL, help="хост портала (напр. https://plusvb.migfull.app)")
    ap.add_argument("--password-stdin", action="store_true", help="read password from stdin")
    ap.add_argument("--validate", action="store_true", help="live-auth against portal before saving")
    ap.add_argument("--list-warehouses", action="store_true", help="печать складов проекта и выход")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list_warehouses:
        sys.exit(asyncio.run(_list_warehouses(args.project)))

    if not args.login:
        print("ERROR: login required (--login or MIGFULL_PORTAL_LOGIN)", file=sys.stderr)
        sys.exit(2)
    if not args.warehouse_id:
        print("ERROR: warehouse-id required (--warehouse-id; см. --list-warehouses)", file=sys.stderr)
        sys.exit(2)
    pw = _read_password(args.password_stdin)
    rc = asyncio.run(main(args.project, args.login, pw, args.warehouse_id, args.host, args.validate, commit=args.commit))
    sys.exit(rc)
