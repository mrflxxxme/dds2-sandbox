# ruff: noqa: RUF001, RUF002, RUF003, E701
"""One-shot: реальная доступность WB-складов для одного баркода СЕЙЧАС.

Источники:
  • POST /api/v1/acceptance/options    — техническая возможность (canBox/canMonopallet)
  • GET  /api/tariffs/v1/acceptance/coefficients — публичные слоты на 14 дней
                                                   (free/paid/closed + isSortingCenter)

Реально доступен = options.can* == true И в ближайшие 14 дней есть хотя бы 1 день
с allowUnload=true И coefficient != -1.

Использование (внутри backend container):
    PYTHONPATH=/app python /app/scripts/check_wb_warehouses_for_sku.py
"""

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, "/app")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.integrations.wb_api import WBApiClient
from backend.services.funnel.wb_api_client import get_wb_key

PROJECT_ID = 4  # Default
BARCODE = "2046196817693"  # одеяло_193х203_9кг (nm_id=537051578)
QTY = 1

# 2026-05-10: реверс из реальных tariffs/v1/acceptance/coefficients —
# boxTypeID=5 = МОНО (тарифы совпадают с экраном кабинета), 6 = КОРОБ, 2 = «Суперсейф».
_BOX_TYPE_NAME = {2: "SUPERSAFE", 5: "MONOPALLET", 6: "BOX"}


async def main() -> None:
    db_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://dds:dds_secret@db:5432/dds_db")
    engine = create_async_engine(db_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        api_key = await get_wb_key(db, PROJECT_ID, "wb")
        if not api_key:
            print(f"ERR: no WB key for project_id={PROJECT_ID}")
            return

    client = WBApiClient(api_key=api_key, project_id=PROJECT_ID)

    print("[1/3] GET /api/v1/warehouses (id → name)…")
    wh_raw = await client.get_fbw_warehouses()
    wh_id_to_name = {int(w["ID"]): w["name"] for w in wh_raw if w.get("ID") and w.get("name")}
    print(f"      {len(wh_id_to_name)} складов в кабинете.\n")

    print(f"[2/3] POST /api/v1/acceptance/options (barcode={BARCODE}, qty={QTY})…")
    res = await client.get_acceptance_options([{"barcode": BARCODE, "quantity": QTY}])
    result = res.get("result") or []
    if not result or result[0].get("error"):
        print(f"ERR: {result[0].get('error') if result else 'empty result'}")
        return
    raw_warehouses = result[0].get("warehouses") or []

    # options[wid] = {"box": bool, "mono": bool, "super": bool}
    options: dict[int, dict[str, bool]] = {}
    for w in raw_warehouses:
        wid = w.get("warehouseID")
        if wid is None:
            continue
        options[int(wid)] = {
            "box": bool(w.get("canBox")),
            "mono": bool(w.get("canMonopallet")),
            "super": bool(w.get("canSupersafe")),
        }
    print(f"      WB вернул {len(options)} складов с per-barcode флагами.\n")

    print("[3/3] GET /api/tariffs/v1/acceptance/coefficients (next 14 days)…")
    coefs = await client.get_acceptance_coefficients()
    print(f"      {len(coefs)} записей (warehouse × type × date).\n")

    # ─── свод per (warehouse_id, type) ──────────────────────────────────────
    # data[(wid, type)] = {"name", "is_sc", "free", "paid", "closed", "min_coef", "min_coef_date"}
    data: dict[tuple[int, str], dict] = {}
    for e in coefs:
        wid = e.get("warehouseID")
        if wid is None:
            continue
        t = _BOX_TYPE_NAME.get(e.get("boxTypeID"))
        if t is None:
            continue
        key = (int(wid), t)
        rec = data.setdefault(
            key,
            {
                "name": e.get("warehouseName") or wh_id_to_name.get(int(wid), f"<id={wid}>"),
                "is_sc": bool(e.get("isSortingCenter")),
                "free": 0,
                "paid": 0,
                "closed": 0,
                "min_coef": None,
                "min_coef_date": None,
            },
        )
        coef = e.get("coefficient")
        allow = bool(e.get("allowUnload"))
        date = (e.get("date") or "")[:10]
        if coef == -1 or not allow:
            rec["closed"] += 1
            continue
        if coef is None:
            continue
        if coef <= 1:
            rec["free"] += 1
        else:
            rec["paid"] += 1
        if rec["min_coef"] is None or coef < rec["min_coef"]:
            rec["min_coef"] = coef
            rec["min_coef_date"] = date

    # ─── фильтр исключений (как в кабинете для обычной FBO-поставки) ────────
    EXCLUDE_TOKENS = (" СГТ", ":Питание", ": Питание", ":Горючее", ": Горючее")

    def _is_excluded(name: str) -> tuple[bool, str]:
        if name.startswith("СЦ "):
            return True, "СЦ (сортировочный центр)"
        if name.startswith("Склад Шушары"):
            return True, "не для обычной FBO"
        for tok in EXCLUDE_TOKENS:
            if tok in name:
                return True, "СГТ/спец-склад (Питание/Горючее)"
        return False, ""

    # rows: всё что options.can* = true и склад НЕ исключён
    rows: list[dict] = []
    excluded_seen: list[tuple[str, str]] = []
    for wid, opt in options.items():
        name = wh_id_to_name.get(wid, f"<id={wid}>")
        excluded, why = _is_excluded(name)
        if excluded:
            if opt["box"] or opt["mono"] or opt["super"]:
                excluded_seen.append((name, why))
            continue
        for t_key, t_label in [("box", "BOX"), ("mono", "MONOPALLET"), ("super", "SUPERSAFE")]:
            if not opt.get(t_key):
                continue
            rec = data.get((wid, t_label))
            free = rec["free"] if rec else 0
            paid = rec["paid"] if rec else 0
            closed = rec["closed"] if rec else 0
            min_coef = rec["min_coef"] if rec else None
            min_date = rec["min_coef_date"] if rec else None
            if rec is None:
                tariff = "нет данных в /coefficients"
            elif free + paid == 0:
                tariff = f"публичных слотов нет (closed={closed}/14) → кабинет даёт по индивид. квоте"
            elif free > 0 and paid == 0:
                tariff = f"бесплатно {free}/14 дн (×{min_coef})"
            elif free == 0 and paid > 0:
                tariff = f"только платно {paid}/14 дн (мин ×{min_coef})"
            else:
                tariff = f"бесплатно {free}/14 + платно {paid}/14 (мин ×{min_coef})"
            rows.append(
                {
                    "wid": wid,
                    "name": name,
                    "type": t_label,
                    "free": free,
                    "paid": paid,
                    "closed": closed,
                    "min_coef": min_coef,
                    "min_coef_date": min_date,
                    "tariff": tariff,
                }
            )

    # сортировка: бесплатно > платно > без публичных слотов; потом free_days desc; потом имя
    def _bucket(r: dict) -> int:
        if r["free"] > 0:
            return 0
        if r["paid"] > 0:
            return 1
        return 2

    rows.sort(
        key=lambda r: (
            _bucket(r),
            -r["free"],
            r["min_coef"] if r["min_coef"] is not None else 999,
            r["name"],
            r["type"],
        )
    )

    print("\n=== ДОСТУПНЫЕ СКЛАДЫ ДЛЯ ЭТОГО БАРКОДА (источник: /acceptance/options = как кабинет) ===")
    print(f"    Исключены: СЦ + СГТ + Питание + Горючее. Всего строк: {len(rows)}")
    print()
    print(f"{'СКЛАД':<40} {'ТИП':<11}  ТАРИФ (по /coefficients за 14 дн)")
    print("─" * 100)
    for r in rows:
        print(f"{r['name']:<40} {r['type']:<11}  {r['tariff']}")

    # ─── свод по складам ────────────────────────────────────────────────────
    by_name_types = defaultdict(set)
    for r in rows:
        by_name_types[r["name"]].add(r["type"])

    both = [n for n, ts in by_name_types.items() if "BOX" in ts and "MONOPALLET" in ts]
    only_box = [n for n, ts in by_name_types.items() if ts == {"BOX"}]
    only_mono = [n for n, ts in by_name_types.items() if ts == {"MONOPALLET"}]

    print("\n=== СВОД (без СЦ/СГТ/Питание/Горючее) ===")
    print(f"  Всего: {len(by_name_types)} складов")
    print(f"    • ОБА типа (короб + моно): {len(both)}")
    for n in sorted(both):
        print(f"        – {n}")
    print(f"    • Только КОРОБ: {len(only_box)}")
    for n in sorted(only_box):
        print(f"        – {n}")
    print(f"    • Только МОНО: {len(only_mono)}")
    for n in sorted(only_mono):
        print(f"        – {n}")

    print(f"\n  Исключённые из вывода ({len(excluded_seen)}, для информации):")
    for n, why in sorted(set(excluded_seen)):
        print(f"        – {n}  [{why}]")


if __name__ == "__main__":
    asyncio.run(main())
