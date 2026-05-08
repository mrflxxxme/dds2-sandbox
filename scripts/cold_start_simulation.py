# ruff: noqa: RUF001, RUF002, RUF003 — hardcoded русские названия складов/округов
"""Cold-start distribution simulation.

Запуск: docker compose exec backend python /app/scripts/cold_start_simulation.py \
    --project-id 4 --bench-from 15 --qty 100 --min-pack 5 --nm-ids 9,5,65

Логика:
1. Bench по ФО = доля заказов проекта за окно. Если своих < threshold,
   fallback на bench соседнего проекта (--bench-from) или статичные WB-доли.
2. Для каждого ФО — главный склад = top-1 по orders в bench-проекте, не в excluded.
3. Распределяем total_qty по ФО → главному складу ФО.
4. Min-pack: ФО с qty < min_pack pool'ятся в крупнейший ФО.
5. Вычитаем уже идущие сборки/транзит на этот склад (existing logic).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict

import psycopg2
import psycopg2.extras

# Импорт маппинга склад→ФО из проекта (тот же справочник что в backend)
sys.path.insert(0, "/app")
from backend.services.warehouse_district import (
    DISTRICT_LABELS,
    DISTRICT_ORDER,
    WAREHOUSE_TO_DISTRICT,
    okrug_to_district,
)

# Общероссийский WB-фолбэк (примерные доли заказов по ФО, если у проекта 0 истории)
FALLBACK_DISTRICT_SHARE: dict[str, float] = {
    "central": 0.30,
    "volga": 0.16,
    "south_caucasus": 0.18,
    "far_east_siberia": 0.13,
    "northwest": 0.09,
    "ural": 0.09,
    "abroad": 0.05,
}

MIN_ORDERS_FOR_OWN_BENCH = 100  # ниже этого — fallback


def db_connect():
    url = os.environ.get("DATABASE_URL_SYNC") or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL_SYNC env not set")
    return psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)


def fetch_district_share(conn, project_id: int, window_days: int) -> tuple[dict[str, float], int]:
    """Возвращает (доли по ФО, всего заказов за окно)."""
    sql = """
        SELECT oblast_okrug_name, country_name, COUNT(*) AS cnt
        FROM wb_orders
        WHERE project_id=%s
          AND order_date >= CURRENT_DATE - (%s || ' days')::interval
          AND is_cancel = false
        GROUP BY oblast_okrug_name, country_name
    """
    with conn.cursor() as cur:
        cur.execute(sql, (project_id, window_days))
        rows = cur.fetchall()
    by_district: dict[str, int] = defaultdict(int)
    total = 0
    for r in rows:
        d = okrug_to_district(r["oblast_okrug_name"], r["country_name"])
        by_district[d] += r["cnt"]
        total += r["cnt"]
    if total == 0:
        return {}, 0
    return {k: v / total for k, v in by_district.items()}, total


def fetch_warehouse_traffic(conn, project_id: int, window_days: int) -> dict[str, int]:
    """orders за окно по каждому WB-складу-источнику."""
    sql = """
        SELECT warehouse_name, COUNT(*) AS cnt
        FROM wb_orders
        WHERE project_id=%s
          AND order_date >= CURRENT_DATE - (%s || ' days')::interval
          AND is_cancel = false
          AND warehouse_name IS NOT NULL
        GROUP BY warehouse_name
    """
    with conn.cursor() as cur:
        cur.execute(sql, (project_id, window_days))
        return {r["warehouse_name"]: r["cnt"] for r in cur.fetchall()}


def fetch_excluded_warehouses(conn, project_id: int) -> set[str]:
    import json

    sql = "SELECT value FROM project_settings WHERE project_id=%s AND key='excluded_warehouses' LIMIT 1"
    with conn.cursor() as cur:
        cur.execute(sql, (project_id,))
        row = cur.fetchone()
    if not row or not row.get("value"):
        return set()
    try:
        data = json.loads(row["value"])
        if isinstance(data, list):
            return {str(x).strip() for x in data}
    except (json.JSONDecodeError, TypeError):
        return set()
    return set()


def fetch_sku(conn, project_id: int, nm_ids: list[int]) -> list[dict]:
    sql = """
        SELECT n.id AS nm_id, n.article_seller, n.subject, n.brand, n.article_wb,
               COALESCE((SELECT SUM(quantity) FROM warehouse_stock ws
                         WHERE ws.nomenclature_id=n.id AND ws.project_id=%s), 0) AS rf_qty,
               COALESCE((SELECT SUM(quantity) FROM wb_warehouse_stocks wws
                         WHERE wws.nm_id=n.article_wb AND wws.project_id=%s
                           AND wws.quantity>0), 0) AS wb_qty
        FROM nomenclature n
        WHERE n.project_id=%s AND n.id = ANY(%s)
    """
    with conn.cursor() as cur:
        cur.execute(sql, (project_id, project_id, project_id, nm_ids))
        return cur.fetchall()


def fetch_active_assemblies_for_sku(conn, project_id: int, nm_id: int) -> dict[str, int]:
    """Сколько уже едет/собрано в каждый WB-склад для этого SKU."""
    sql = """
        SELECT COALESCE(ar.wb_warehouse_name_manual, '?') AS wb_target, SUM(ari.quantity) AS qty
        FROM assembly_request_items ari
        JOIN assembly_requests ar ON ar.id=ari.assembly_request_id
        WHERE ari.project_id=%s
          AND ari.nomenclature_id=%s
          AND ar.is_deleted=false
          AND ar.status IN ('PENDING','READY','SHIPPED','VEHICLE_ASSIGNED','IN_PROGRESS')
        GROUP BY ar.wb_warehouse_name_manual
    """
    with conn.cursor() as cur:
        cur.execute(sql, (project_id, nm_id))
        return {r["wb_target"]: int(r["qty"]) for r in cur.fetchall() if r["wb_target"] != "?"}


def pick_main_warehouse_per_district(warehouse_traffic: dict[str, int], excluded: set[str]) -> dict[str, str]:
    """{district → главный склад этого ФО, не из excluded}."""
    by_district: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for wh, cnt in warehouse_traffic.items():
        d = WAREHOUSE_TO_DISTRICT.get(wh)
        if d is None:
            continue  # склад не в эталонном справочнике
        by_district[d].append((wh, cnt))
    result = {}
    for d, items in by_district.items():
        items.sort(key=lambda x: x[1], reverse=True)
        for wh, _ in items:
            if wh not in excluded:
                result[d] = wh
                break
    return result


def distribute(
    total_qty: int,
    district_share: dict[str, float],
    min_pack: int,
    main_wh_per_district: dict[str, str],
    skip_districts: set[str] | None = None,
) -> dict[str, int]:
    """Главное: разнести total_qty по {warehouse → qty}."""
    skip_districts = skip_districts or {"abroad", "unknown"}
    # 1. Сырое распределение по ФО (округление вниз)
    raw: dict[str, int] = {}
    for d, share in district_share.items():
        if d in skip_districts:
            continue
        raw[d] = int(total_qty * share)
    # Раздаём остаток (от округлений) крупнейшему ФО
    leftover = total_qty - sum(raw.values())
    if raw and leftover > 0:
        biggest = max(raw, key=lambda k: district_share.get(k, 0))
        raw[biggest] += leftover
    # 2. Pool: ФО с qty < min_pack → отдают в крупнейший
    pooled: dict[str, int] = {}
    pool_sum = 0
    biggest = max(raw, key=lambda k: district_share.get(k, 0)) if raw else None
    for d, qty in raw.items():
        if qty < min_pack and d != biggest:
            pool_sum += qty
        else:
            pooled[d] = qty
    if pool_sum > 0 and biggest:
        pooled[biggest] = pooled.get(biggest, 0) + pool_sum
    # 3. ФО → главный склад ФО
    by_warehouse: dict[str, int] = {}
    for d, qty in pooled.items():
        wh = main_wh_per_district.get(d)
        if not wh:
            # нет главного склада в этом ФО (возможно все исключены или нет в bench)
            # → pool в крупнейший ФО, у которого склад есть
            for fallback_d in sorted(pooled, key=lambda k: -district_share.get(k, 0)):
                if main_wh_per_district.get(fallback_d):
                    fallback_wh = main_wh_per_district[fallback_d]
                    by_warehouse[fallback_wh] = by_warehouse.get(fallback_wh, 0) + qty
                    break
        else:
            by_warehouse[wh] = by_warehouse.get(wh, 0) + qty
    return by_warehouse


def simulate(
    conn,
    project_id: int,
    bench_from: int | None,
    window_days: int,
    min_pack: int,
    nm_ids: list[int],
    qty_override: int | None,
):
    excluded = fetch_excluded_warehouses(conn, project_id)
    own_share, own_total = fetch_district_share(conn, project_id, window_days)
    # Если у проекта мало заказов — берём bench соседа или fallback
    use_neighbor = own_total < MIN_ORDERS_FOR_OWN_BENCH
    if use_neighbor and bench_from:
        bench_share, bench_total = fetch_district_share(conn, bench_from, window_days)
        bench_wh_traffic = fetch_warehouse_traffic(conn, bench_from, window_days)
        bench_source = f"проект id={bench_from} ({bench_total} заказов за {window_days}д)"
    elif own_total >= MIN_ORDERS_FOR_OWN_BENCH:
        bench_share = own_share
        bench_wh_traffic = fetch_warehouse_traffic(conn, project_id, window_days)
        bench_source = f"свои данные ({own_total} заказов за {window_days}д)"
    else:
        bench_share = FALLBACK_DISTRICT_SHARE
        bench_wh_traffic = {}  # fallback — нет данных по конкретным складам
        bench_source = "общероссийский WB-фолбэк (своих заказов мало)"

    # Главный склад в каждом ФО (по трафику bench-источника)
    if bench_wh_traffic:
        main_wh = pick_main_warehouse_per_district(bench_wh_traffic, excluded)
    else:
        # Без данных о складах — используем дефолтные центры ФО
        defaults = {
            "central": "Электросталь",
            "volga": "Казань",
            "south_caucasus": "Краснодар",
            "far_east_siberia": "Новосибирск",
            "northwest": "СПБ Шушары",
            "ural": "Екатеринбург - Перспективная 14",
        }
        main_wh = {d: w for d, w in defaults.items() if w not in excluded}

    print("=" * 90)
    print(f"Проект: {project_id}")
    print(f"Бенчмарк: {bench_source}")
    print(f"Окно: {window_days}д   |   Min-pack: {min_pack}   |   Excluded: {len(excluded)} складов")
    print("=" * 90)
    print()
    print("Доли по ФО (бенчмарк):")
    for d in DISTRICT_ORDER:
        if d in bench_share:
            wh = main_wh.get(d, "—")
            print(f"  {DISTRICT_LABELS[d]:<35} {bench_share[d]*100:5.1f}%   главный: {wh}")
    print()

    skus = fetch_sku(conn, project_id, nm_ids)
    if not skus:
        print(f"Нет SKU в проекте {project_id} с id из {nm_ids}")
        return

    for sku in skus:
        nm_id = sku["nm_id"]
        article = sku["article_seller"] or "—"
        rf = int(sku["rf_qty"])
        wb_already = int(sku["wb_qty"])
        qty = qty_override if qty_override is not None else rf  # дефолт: распределить весь ФФ-остаток
        if qty <= 0:
            print(f"⏭  SKU id={nm_id} '{article}' — нет остатка ФФ, пропуск")
            continue

        active_asm = fetch_active_assemblies_for_sku(conn, project_id, nm_id)
        allocation = distribute(qty, bench_share, min_pack, main_wh)

        # Вычесть уже идущие сборки на тот же склад
        final = {}
        for wh, q in allocation.items():
            asm = active_asm.get(wh, 0)
            final[wh] = max(0, q - asm)

        print("─" * 90)
        print(
            f"SKU id={nm_id}  {article}  ({sku['subject']})\n"
            f"   На ФФ: {rf} шт   |   Уже на WB: {wb_already} шт"
            f"   |   В активных сборках: {sum(active_asm.values())} шт"
        )
        print(f"   📦 РАСПРЕДЕЛЯЕМ: {qty} шт")
        print()
        # Pretty table
        rows_to_print = []
        total_out = 0
        for d in DISTRICT_ORDER:
            wh = main_wh.get(d)
            if not wh:
                continue
            allocated = allocation.get(wh, 0)
            already_asm = active_asm.get(wh, 0)
            net = final.get(wh, 0)
            if allocated == 0 and already_asm == 0:
                continue
            rows_to_print.append((DISTRICT_LABELS[d], wh, allocated, already_asm, net))
            total_out += net
        print(f"   {'ФО':<32} {'Склад':<22} {'Расчёт':>8} {'Уже едет':>10} {'К отправке':>12}")
        print(f"   {'-'*32} {'-'*22} {'-'*8} {'-'*10} {'-'*12}")
        for d_label, wh, allocated, already_asm, net in rows_to_print:
            print(f"   {d_label:<32} {wh:<22} {allocated:>8} {already_asm:>10} {net:>12}")
        print(f"   {'':<32} {'ИТОГО:':<22} {qty:>8} {sum(active_asm.values()):>10} {total_out:>12}")
        print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--project-id", type=int, required=True)
    p.add_argument(
        "--bench-from", type=int, default=None, help="ID соседнего проекта-донора bench, если своих заказов мало"
    )
    p.add_argument("--window-days", type=int, default=30)
    p.add_argument("--min-pack", type=int, default=5)
    p.add_argument("--nm-ids", type=str, required=True, help="comma-separated nomenclature.id")
    p.add_argument("--qty", type=int, default=None, help="Сколько штук распределять (дефолт = весь ФФ-остаток SKU)")
    args = p.parse_args()
    nm_ids = [int(x) for x in args.nm_ids.split(",") if x.strip()]
    with db_connect() as conn:
        simulate(conn, args.project_id, args.bench_from, args.window_days, args.min_pack, nm_ids, args.qty)


if __name__ == "__main__":
    main()
