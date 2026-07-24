# ruff: noqa: RUF002, RUF003, T201
"""
Idempotent setup of VTB «Интеграционный Банк-Клиент» (H2H) statement-sync credentials.

Stores an IntegrationKey(service="vtb_ibk") with:
  encrypted_key = Fernet-encrypted PIN ключа (может быть пустым, если ключ без PIN);
  config = {
    "custid":           "917361722",         # Идентификатор организации в СДБО
    "endpoint":         "<prod|test url>",
    "cert_thumbprint":  "<отпечаток серта в КриптоПро на сервере>",
    "cert_serial":      "<серийный номер серта = UID в запросах>",
    "kbopid":           "0",                  # уточняется на тест-стенде
    "accounts": [ {"account": "...", "bic": "...", "currency": "RUB"}, ... ],
  }

Источники (флаг | env): --custid VTB_CUSTID, --cert-thumbprint VTB_CERT_THUMBPRINT,
--cert-serial VTB_CERT_SERIAL, --kbopid VTB_KBOPID, --accounts VTB_ACCOUNTS (JSON),
--pin / VTB_PIN (опц.), --test (тест-стенд вместо прода).

Usage:
  VTB_CUSTID=917361722 VTB_CERT_THUMBPRINT=... VTB_CERT_SERIAL=... \
  VTB_ACCOUNTS='[{"account":"40702810...","bic":"044525411","currency":"RUB"}]' \
    docker compose exec -T -e PYTHONPATH=/app backend python scripts/setup_vtb_account.py \
      --project вяткин-<slug> --test --commit
  # dry-run (default) печатает план и откатывает.
"""

import argparse
import asyncio
import json
import os
import sys

from sqlalchemy import select

from backend.database import get_db
from backend.integrations.vtb_ibk_client import ENDPOINT_PROD, ENDPOINT_TEST
from backend.models import IntegrationKey
from backend.models.auth import Project
from backend.utils.crypto import encrypt as _encrypt

SERVICE = "vtb_ibk"


def _parse_accounts(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: --accounts не парсится как JSON: {e}", file=sys.stderr)
        sys.exit(2)
    if not isinstance(data, list) or not all(isinstance(a, dict) and a.get("account") for a in data):
        print("ERROR: --accounts должен быть JSON-списком объектов с полем 'account'", file=sys.stderr)
        sys.exit(2)
    return data


async def main(slug: str, config: dict, pin: str, commit: bool) -> int:
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

        label = f"ИБК custid={config['custid']}"
        if existing is None:
            db.add(
                IntegrationKey(
                    project_id=proj.id,
                    service=SERVICE,
                    label=label,
                    encrypted_key=_encrypt(pin),
                    is_active=True,
                    config=config,
                )
            )
            print(f"  + created VTB ИБК key for project {slug!r} ({label}, endpoint={config['endpoint']})")
        else:
            existing.label = label
            existing.encrypted_key = _encrypt(pin)
            existing.is_active = True
            existing.config = config
            print(f"  ~ updated VTB ИБК key (id={existing.id}) for project {slug!r}")

        print(f"    accounts: {[a.get('account') for a in config['accounts']]}")
        print("    ⚠ live-проверка не запускалась (нужен серверный сертификат + тест-стенд).")

        if commit:
            await db.commit()
            print("\nCOMMITTED.")
        else:
            await db.rollback()
            print("\nDRY-RUN — rolled back. Re-run with --commit to apply.")
        return 0
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Setup VTB ИБК statement-sync credentials")
    ap.add_argument("--project", required=True, help="project slug (напр. вяткин-<...>)")
    ap.add_argument("--custid", default=os.environ.get("VTB_CUSTID", ""))
    ap.add_argument("--cert-thumbprint", default=os.environ.get("VTB_CERT_THUMBPRINT", ""))
    ap.add_argument("--cert-serial", default=os.environ.get("VTB_CERT_SERIAL", ""))
    ap.add_argument("--kbopid", default=os.environ.get("VTB_KBOPID", "0"))
    ap.add_argument("--accounts", default=os.environ.get("VTB_ACCOUNTS", ""))
    ap.add_argument("--pin", default=os.environ.get("VTB_PIN", ""))
    ap.add_argument("--test", action="store_true", help="использовать тест-стенд вместо прода")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.custid:
        print("ERROR: custid required (--custid или VTB_CUSTID)", file=sys.stderr)
        sys.exit(2)
    if not args.cert_thumbprint:
        print("ERROR: cert-thumbprint required (--cert-thumbprint или VTB_CERT_THUMBPRINT)", file=sys.stderr)
        sys.exit(2)
    if not args.accounts:
        print("ERROR: accounts required (--accounts или VTB_ACCOUNTS, JSON)", file=sys.stderr)
        sys.exit(2)

    cfg = {
        "custid": args.custid,
        "endpoint": ENDPOINT_TEST if args.test else ENDPOINT_PROD,
        "cert_thumbprint": args.cert_thumbprint,
        "cert_serial": args.cert_serial,
        "kbopid": args.kbopid,
        "accounts": _parse_accounts(args.accounts),
    }
    rc = asyncio.run(main(args.project, cfg, args.pin, commit=args.commit))
    sys.exit(rc)
