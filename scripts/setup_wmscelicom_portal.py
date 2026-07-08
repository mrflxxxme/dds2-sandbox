"""Завести портал-креды кабинета «Целиком» в существующие wmscelicom-ключи.

Кабинет живёт на том же хосте, что и API (`config.api_base_url`). Скрипт логинится,
авто-определяет клиента (`UpParent`) и WB-договор (`TargetId`) на КАЖДОМ инстансе
(через обратимый черновик: create → read TargetId → delete) и сохраняет в config:
`portal_login`, `portal_password` (шифрованно), `portal_client_id`, `portal_target_id`.
После этого создание заявки на сборку у «Целиком» будет проставлять склад сдачи.

Логин/пароль — из env WMS_PORTAL_LOGIN / WMS_PORTAL_PASSWORD.
Опц. --warehouse-id N — ограничить одним складом. --dry-run — только показать.

Запуск на проде:
  docker compose exec -T -e WMS_PORTAL_LOGIN=... -e WMS_PORTAL_PASSWORD=... backend \
      python scripts/setup_wmscelicom_portal.py
"""
import argparse
import asyncio
import json
import os
import re

import httpx
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from backend.database import AsyncSessionLocal
from backend.integrations.wmscelicom_client import normalize_base_url
from backend.models.integrations import IntegrationKey
from backend.utils.crypto import encrypt as _encrypt

_CRC_RE = re.compile(r'name="IntegrityCRC"[^>]*value="([^"]*)"', re.I)


async def _discover(host: str, login: str, password: str) -> tuple[str, str]:
    """(client_id UpParent, target_id WB-договор) для инстанса. Черновик удаляется."""
    base = normalize_base_url(host)
    hdr = {"User-Agent": "Mozilla/5.0 (DDS setup)", "X-Requested-With": "XMLHttpRequest",
           "Referer": f"{base}/office/dispatchorders/"}
    async with httpx.AsyncClient(base_url=base, timeout=30, follow_redirects=False, headers=hdr) as c:
        await c.get("/office/")
        await c.post("/nohead/auth/login/", data={"login": login, "password": password})
        home = await c.get("/office/dispatchorders/")
        if "logout" not in home.text and "Выход" not in home.text:
            raise SystemExit(f"[{host}] логин не прошёл — проверьте WMS_PORTAL_LOGIN/PASSWORD")

        # UpParent (клиент): getajaxlist без контекста
        up = json.loads((await c.post(
            "/nohead/dispatchorders/admincontent/?action=getajaxlist&id=&key=UpParent",
            data={"search": "", "page": 0, "loadlength": 50})).text)
        clients = [o for o in up if isinstance(o, dict) and o.get("id") is not None]
        if not clients:
            raise SystemExit(f"[{host}] не удалось определить клиента (UpParent пуст)")
        client_id = str(clients[0]["id"])

        # TargetId (WB-договор): нужен контекст заявки → обратимый черновик
        form = (await c.post("/nohead/dispatchorders/admincontent/?action=edit&id=0")).text
        crc = (_CRC_RE.search(form) or [None, ""])[1] if _CRC_RE.search(form) else ""
        draft = json.loads((await c.post(
            "/nohead/dispatchorders/admincontent/?action=save&id=0",
            data={"IntegrityCRC": crc, "UpParent": client_id, "IsRejectState": "0",
                  "Target": "1", "Transportation": "-"})).text)
        draft_id = str(draft.get("id") or "")
        try:
            tl = json.loads((await c.post(
                f"/nohead/dispatchorders/admincontent/?action=getajaxlist&id={draft_id}&key=TargetId",
                data={"search": "", "page": 0, "loadlength": 50})).text)
            wb = [o for o in tl if isinstance(o, dict) and o.get("id") is not None]
            if not wb:
                raise SystemExit(f"[{host}] не удалось определить WB-договор (TargetId пуст)")
            target_id = str(wb[0]["id"])
        finally:
            if draft_id:
                crc2 = (_CRC_RE.search((await c.post(
                    f"/nohead/dispatchorders/admincontent/?action=edit&id={draft_id}")).text) or [None, ""])
                await c.post("/nohead/dispatchorders/admincontent/?action=delete",
                             data={"id": draft_id, "IntegrityCRC": crc2[1] if crc2 else ""})
        return client_id, target_id


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--warehouse-id", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    login = os.environ.get("WMS_PORTAL_LOGIN", "").strip()
    password = os.environ.get("WMS_PORTAL_PASSWORD", "").strip()
    if not login or not password:
        raise SystemExit("Задайте WMS_PORTAL_LOGIN и WMS_PORTAL_PASSWORD в окружении")

    async with AsyncSessionLocal() as db:
        q = select(IntegrationKey).where(
            IntegrationKey.service == "wmscelicom",
            IntegrationKey.is_active == True,  # noqa: E712
        )
        if args.warehouse_id is not None:
            q = q.where(IntegrationKey.warehouse_id == args.warehouse_id)
        keys = (await db.execute(q)).scalars().all()
        print(f"active wmscelicom keys: {len(keys)}")

        for key in keys:
            host = str((key.config or {}).get("api_base_url") or "")
            if not host:
                print(f"  project={key.project_id} wh={key.warehouse_id}: нет api_base_url — пропуск")
                continue
            client_id, target_id = await _discover(host, login, password)
            print(f"  project={key.project_id} wh={key.warehouse_id} host={host}: "
                  f"UpParent={client_id} TargetId={target_id}")
            if args.dry_run:
                continue
            cfg = dict(key.config or {})
            cfg["portal_login"] = login
            cfg["portal_password"] = _encrypt(password)
            cfg["portal_client_id"] = client_id
            cfg["portal_target_id"] = target_id
            key.config = cfg
            flag_modified(key, "config")
        if not args.dry_run:
            await db.commit()
            print("saved.")
        else:
            print("dry-run — ничего не сохранено.")


asyncio.run(main())
