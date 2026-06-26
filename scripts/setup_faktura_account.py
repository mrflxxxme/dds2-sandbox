# ruff: noqa: RUF002, RUF003, T201
"""
Idempotent setup of Faktura.ru (ВБ Банк) statement-sync credentials for a project.

Stores an IntegrationKey(service="faktura") with the password Fernet-encrypted and
config={"login": ..., "host": ...}. The 4×/day scheduler job then picks it up.

Password source (in order): --password-stdin | env FAKTURA_PASSWORD | interactive getpass.
Login source:               --login | env FAKTURA_LOGIN.

Usage:
  FAKTURA_LOGIN=vyatkin_vv1508 FAKTURA_PASSWORD='***' \
    docker compose exec -T backend python scripts/setup_faktura_account.py \
      --project default --validate --commit
  # dry-run (default) just prints what it would do and rolls back.
"""

import argparse
import asyncio
import getpass
import os
import sys

from sqlalchemy import select

from backend.database import get_db
from backend.integrations.faktura_api import DEFAULT_HOST, FakturaApiClient
from backend.models import IntegrationKey
from backend.models.auth import Project
from backend.utils.crypto import encrypt as _encrypt

SERVICE = "faktura"


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        pw = sys.stdin.readline().strip()
    else:
        pw = os.environ.get("FAKTURA_PASSWORD") or getpass.getpass("Faktura password: ")
    if not pw:
        print("ERROR: empty password", file=sys.stderr)
        sys.exit(2)
    return pw


async def _validate(login: str, password: str, host: str) -> None:
    async with FakturaApiClient(login, password, host) as client:
        await client.authenticate()
        accounts = await client.get_accounts()
    print(f"  ✓ auth OK, {len(accounts)} account(s) visible")
    for a in accounts:
        print(f"      - id={a.get('id')} {a.get('number')} ({(a.get('bank') or {}).get('name')})")


async def main(slug: str, login: str, password: str, host: str, validate: bool, commit: bool) -> int:
    if validate:
        print("Validating credentials against Faktura...")
        await _validate(login, password, host)

    async for db in get_db():
        proj = (
            await db.execute(select(Project).where(Project.slug == slug, Project.is_deleted == False))  # noqa: E712
        ).scalar_one_or_none()
        if proj is None:
            print(f"ERROR: project slug {slug!r} not found", file=sys.stderr)
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
                    config=config,
                )
            )
            print(f"  + created Faktura key for project {slug!r} (login={login}, host={host})")
        else:
            existing.label = login
            existing.encrypted_key = _encrypt(password)
            existing.is_active = True
            existing.config = config
            print(f"  ~ updated Faktura key (id={existing.id}) for project {slug!r}")

        if commit:
            await db.commit()
            print("\nCOMMITTED.")
        else:
            await db.rollback()
            print("\nDRY-RUN — rolled back. Re-run with --commit to apply.")
        return 0
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Setup Faktura.ru statement-sync credentials")
    ap.add_argument("--project", default="default", help="project slug")
    ap.add_argument("--login", default=os.environ.get("FAKTURA_LOGIN", ""))
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--password-stdin", action="store_true", help="read password from stdin")
    ap.add_argument("--validate", action="store_true", help="live-auth against Faktura before saving")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.login:
        print("ERROR: login required (--login or FAKTURA_LOGIN)", file=sys.stderr)
        sys.exit(2)
    pw = _read_password(args.password_stdin)
    rc = asyncio.run(main(args.project, args.login, pw, args.host, args.validate, commit=args.commit))
    sys.exit(rc)
