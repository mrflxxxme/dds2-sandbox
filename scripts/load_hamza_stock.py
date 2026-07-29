"""
Загрузка фактических остатков склада «Хамза» из инвентаризации (Excel).

Приводит остаток (quantity) склада «Хамза» к снимку из файла:
  * 127 SKU из файла  → quantity = число из файла;
  * любой другой SKU склада с остатком > 0 → quantity = 0 (в файле его нет).
Итого по складу становится ровно 16 329 шт. На проде резерв активных заявок = 0,
поэтому «Доступно» = «Кол-во» = 16 329.

Меняется ТОЛЬКО quantity, через штатный сервис create_adjustment (пишет
StockAdjustment + StockMovement в аудит). defect_quantity / in_transit /
cost_price и сами заявки НЕ трогаются.

Usage (запускать как модуль, чтобы импортировался пакет backend):
  docker compose exec backend python -m scripts.load_hamza_stock --project-slug default --dry-run
  docker compose exec backend python -m scripts.load_hamza_stock --project-slug default --commit
"""

import argparse
import asyncio
import sys

from sqlalchemy import select

from backend.database import get_db
from backend.models.auth import Project
from backend.models.warehouse import Warehouse, WarehouseStock, WarehouseType
from backend.services.warehouse_stock_engine import _resolve_barcodes_batch, create_adjustment

REASON = "Инвентаризация Хамза 29.07.2026 (Excel-загрузка фактических остатков)"

# (barcode, target_qty) — фактический остаток из файла. Сумма = 16 329, 127 позиций.
FILE_ITEMS: list[tuple[str, int]] = [
    ("2044777276297", 80),  # RADYGA_210x90_160x90_молочный
    ("2044145314996", 18),  # ZEBRA_210x90_160x90_темно-серый
    ("2042072507740", 160),  # DIVANDEK_210x90_160x90_темно-серый
    ("2044294174618", 32),  # POLOSKA_210x90_160x90_бежевый
    ("2042072507733", 212),  # DIVANDEK_210x90_160x90_коричневый
    ("2043712296017", 20),  # POKRIVALO_бежевый
    ("2045407872872", 10),  # POKRIVALO_черный
    ("2044778115847", 40),  # VELYR_210x90_160x90_бежевый
    ("2045407872858", 10),  # POKRIVALO_коричневый
    ("2043712295966", 10),  # POKRIVALO_серый
    ("2043160330608", 144),  # KOSIHKA_210x90_160x90_бежевый
    ("2043160830351", 598),  # NAKIDKA_210x90_светло-серый
    ("2044294122275", 176),  # POLOSKA_210x90_160x90_коричневый
    ("2044294174632", 176),  # POLOSKA_210x90_160x90_светло-серый
    ("2043300615237", 208),  # KREST_210x90_160x90_бежевый
    ("2046391968879", 36),  # DIVANDEK_160x90_коричневый
    ("2043160630135", 64),  # DOROGOY_210x90_160x90_бежевый
    ("2043160630111", 32),  # DOROGOY_210x90_160x90_светло-серый
    ("2044145373825", 18),  # ZEBRA_210x90_160x90_молочный
    ("2045407591469", 20),  # KREST_210x90_160x90_темно-коричневый
    ("2045409330257", 20),  # LISTIK_210x90_160x90_зеленый
    ("2043740032052", 144),  # DIVANDEK_210x90_160x90_кофе
    ("2042072507771", 512),  # DIVANDEK_210x90_160x90_светло-серый
    ("2043300615220", 80),  # KREST_210x90_160x90_коричневый
    ("2043160830375", 52),  # NAKIDKA_210x90_бежевый
    ("2045409830153", 20),  # LOZA_210x90_160x90_темно-серый
    ("2045409824213", 20),  # LABIRINT_210x90_160x90_темно-серый
    ("2045409824190", 40),  # LABIRINT_210x90_160x90_коричневый
    ("2044831049096", 1515),  # ZBR-6/30x60
    ("2044830795659", 825),  # YY-1020/30x60
    ("2044830949991", 105),  # BZ-YY1063/30x60
    ("2044831044923", 1320),  # D-903/30x30
    ("2044830847082", 48),  # YY-1018/30x30
    ("2044830848768", 30),  # YY-1018/30x60
    ("2044831046071", 1425),  # WB-52/30x60
    ("2044830795376", 48),  # YY-1020/30x30
    ("2044830793327", 255),  # YY-1101/30x60
    ("2044830851676", 555),  # YY-1092/30x60
    ("2044388693001", 7),  # 150х200_трава
    ("2044388704714", 7),  # 160х200_трава
    ("2044388618738", 8),  # 160х200_белыйоднотон
    ("2043788816553", 214),  # 150х200_серый
    ("2043788824176", 1461),  # 160х200_серый
    ("2047114211029", 2),  # 80х160_синий
    ("2047114210978", 4),  # 80х160_зеленый
    ("2045932869361", 8),  # 120х170_молочный
    ("2047213311729", 2),  # 80х200_молочный
    ("2044388651087", 2),  # 120х160_коричневыйоднотон
    ("2045933030982", 7),  # 150х200_черныйблек
    ("2043788818984", 6),  # 150х200_бежевый
    ("2043788833116", 4),  # 160х200_бежевый
    ("2044388594698", 76),  # 160х230_белыйоднотон
    ("2045932869163", 9),  # 160х230_молочный
    ("2044388704776", 7),  # 160х200_коричневыйоднотон
    ("2047114210466", 1),  # 80х160_черныйблек
    ("2044388693032", 5),  # 150х200_коричневыйоднотон
    ("2044388587676", 15),  # 160х230_коричневыйоднотон
    ("2044388469361", 9),  # 150х200_серыйоднотонн
    ("2045933030920", 9),  # 160х230_черныйблек
    ("2044388618721", 137),  # 150х200_белыйоднотон
    ("2044388647257", 10),  # 160х200_бежевыйоднотон
    ("2044388647233", 1),  # 150х200_бежевыйоднотон
    ("2047110740899", 7),  # 160х230_ромбтемносерый
    ("2047111198019", 4),  # 160х200_ромбсерый
    ("2047111197869", 5),  # 160х230_ромбсерый
    ("2047111368603", 5),  # 150х200_пепельный
    ("2044388693025", 8),  # 150х200_коричневй
    ("2047110228250", 13),  # 160х200_зеленый
    ("2045932869491", 11),  # 150х200_молочный
    ("2045932869521", 2),  # 160х200_молочный
    ("2047213093069", 1),  # 80х160_молочный
    ("2045932869446", 1),  # 120х160_молочный
    ("2044388401804", 51),  # 160х230_серыйоднотон
    ("2044388468623", 16),  # 160х200_серыйоднотон
    ("2047110228175", 4),  # 150х200_зеленый
    ("2044388587669", 2),  # 160х230_пони
    ("2044388704745", 4),  # 160х200_пони
    ("2044388693018", 1),  # 150х200_пони
    ("2043788747611", 314),  # 160х230_серый
    ("2047110489378", 5),  # 150х200_синий
    ("2047111197920", 8),  # 200х300_ромбсерый
    ("2043788810315", 2),  # 200х300_розовый
    ("2044388564813", 36),  # 200х300_черныйоднотон
    ("2047110740950", 1),  # 200х300_ромбтемносерый
    ("2043788827245", 8),  # 160х200_черный
    ("2043788817697", 3),  # 150х200_черный
    ("2044388564998", 3),  # 150х200_черныйоднотон
    ("2044388576984", 1),  # 160х200_черныйоднотон
    ("2047110228113", 1),  # 120х170_зеленый
    ("2044388618707", 1),  # 200х300_белыйоднотон
    ("2044388605288", 1),  # 120х170_белыйоднотон
    ("2044388587683", 2),  # 160х230_коричневый
    ("2044388704752", 3),  # 160х200_коричневый
    ("2047110740967", 4),  # 150х200_ромбтемносерый
    ("2044388467336", 2),  # 160х230_вишня
    ("2043788839248", 1),  # 160х200_розовый
    ("2047111197937", 32),  # 150х200_ромбсерый
    ("2047114287949", 1),  # 80х160_ромбсерый
    ("2047114287895", 1),  # 80х160_ромбтемносерый
    ("2047111692807", 1),  # 80х160_черный
    ("2047114210312", 46),  # 80х160_черныйоднотон
    ("2043788818076", 1),  # 150х200_розовый
    ("2043788760580", 1),  # 160х230_бежевый
    ("2043788761945", 3),  # 160х230_черный
    ("2049499420058", 40),  # 200х300_винтажцветы
    ("2045932869477", 9),  # 200х300_молочный
    ("2049985175424", 10),  # 150x200_винтажкрасныйяркий
    ("2045933030975", 45),  # 200х300_черныйблек
    ("2049909296686", 15),  # 200х300_зебра_молочный
    ("2049909296082", 15),  # 200х300_лоза_молочный
    ("2049985175820", 10),  # 150x200_винтажсеро-белый
    ("2048337184893", 8),  # 160х230_винтажсерый
    ("2049499362402", 8),  # 160х230_винтажсинийромб
    ("2049909295504", 5),  # 200х300_лабиринт_черный
    ("2047114210275", 336),  # 80х160_серыйоднотон
    ("2044388405345", 190),  # 120х170_серыйоднотон
    ("2047114210282", 302),  # 80х200_серыйоднотон
    ("2047114210398", 220),  # 80х200_белыйоднотон
    ("2047114210381", 504),  # 80х160_белыйоднотон
    ("2044388607152", 20),  # 120х160_белыйоднотон
    ("2047114210442", 110),  # 80х200_бежевыйоднотон
    ("2047114210411", 312),  # 80х160_бежевыйоднотон
    ("2043788796855", 60),  # 120х160_серый
    ("2043788808268", 1630),  # 200х300_серый
    ("2047110740981", 24),  # 160х200_ромбтемносерый
    ("2047111538457", 705),  # 80х200_серый
    ("2047111692814", 45),  # 80х200_черный
]

TARGET_TOTAL = 16329


async def main(project_id: int | None, project_slug: str | None, commit: bool) -> int:
    file_target: dict[str, int] = {bc: q for bc, q in FILE_ITEMS}
    if len(file_target) != len(FILE_ITEMS):
        print("ERROR: дубли штрихкодов в FILE_ITEMS", file=sys.stderr)
        return 1
    if sum(file_target.values()) != TARGET_TOTAL:
        print(f"ERROR: сумма файла {sum(file_target.values())} != {TARGET_TOTAL}", file=sys.stderr)
        return 1

    async for db in get_db():
        # ── проект ──────────────────────────────────────────────────────────
        if project_id is None:
            assert project_slug is not None
            row = (
                await db.execute(
                    select(Project.id, Project.name).where(
                        Project.slug == project_slug,
                        Project.is_deleted == False,  # noqa: E712
                    )
                )
            ).first()
            if row is None:
                print(f"ERROR: проект slug={project_slug!r} не найден", file=sys.stderr)
                return 1
            project_id, proj_name = row
            print(f"Проект: id={project_id} slug={project_slug!r} name={proj_name!r}")

        # ── склад Хамза ─────────────────────────────────────────────────────
        warehouses = list(
            (
                await db.execute(
                    select(Warehouse).where(
                        Warehouse.project_id == project_id,
                        Warehouse.name.ilike("%хамза%"),
                        Warehouse.is_deleted == False,  # noqa: E712
                    )
                )
            )
            .scalars()
            .all()
        )
        if not warehouses:
            print(f"ERROR: склад 'хамза' не найден в проекте {project_id}", file=sys.stderr)
            return 1
        if len(warehouses) > 1:
            print("ERROR: несколько складов подходят — уточни:", file=sys.stderr)
            for w in warehouses:
                print(f"  id={w.id} name={w.name!r} type={w.warehouse_type}", file=sys.stderr)
            return 1
        wh = warehouses[0]
        print(f"Склад: id={wh.id} name={wh.name!r} type={wh.warehouse_type}")
        if wh.warehouse_type != WarehouseType.FULFILLMENT.value:
            print(f"ERROR: тип склада {wh.warehouse_type}, ожидался FULFILLMENT", file=sys.stderr)
            return 1

        # ── резолв штрихкодов файла ─────────────────────────────────────────
        try:
            bmap = await _resolve_barcodes_batch(db, project_id, list(file_target.keys()))
        except ValueError as e:
            print(f"ERROR: штрихкод из файла не найден в номенклатуре: {e}", file=sys.stderr)
            return 1
        file_nom_ids = {n.id for n in bmap.values()}

        # ── текущий остаток склада ──────────────────────────────────────────
        rows = list(
            (
                await db.execute(
                    select(WarehouseStock).where(
                        WarehouseStock.project_id == project_id,
                        WarehouseStock.warehouse_id == wh.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        cur_by_nom = {r.nomenclature_id: r for r in rows}

        # ── план корректировок: target по каждому SKU ───────────────────────
        # файловые → target из файла; прочие с остатком > 0 → 0.
        updates: list[tuple[str, int, int, int]] = []  # (barcode, cur, target, delta)

        for barcode, target in file_target.items():
            nom = bmap[barcode]
            cur_row = cur_by_nom.get(nom.id)
            cur = cur_row.quantity if cur_row else 0
            if cur != target:
                updates.append((barcode, cur, target, target - cur))

        zeroed: list[tuple[str, int]] = []  # (barcode, cur) — обнуляемые не из файла
        for r in rows:
            if r.nomenclature_id in file_nom_ids:
                continue
            if r.quantity != 0:
                zeroed.append((r.barcode, r.quantity))
                updates.append((r.barcode, r.quantity, 0, -r.quantity))

        # ── отчёт ───────────────────────────────────────────────────────────
        cur_total = sum(r.quantity for r in rows)
        print(f"\nТекущий остаток склада (quantity): {cur_total}")
        print(f"Целевой остаток склада (файл):     {TARGET_TOTAL}")
        print(f"Позиций в файле: {len(file_target)} | строк на складе сейчас: {len(rows)}")

        print(f"\n{'Штрихкод':<16} {'Было':>8} {'Станет':>8} {'Δ':>8}")
        print("-" * 46)
        for barcode, cur, target, delta in updates:
            print(f"{barcode:<16} {cur:>8} {target:>8} {delta:>+8}")
        print("-" * 46)
        print(f"Корректировок: {len(updates)}")
        print(f"Обнуляется (нет в файле): {len(zeroed)} SKU, {sum(q for _, q in zeroed)} шт")

        # проверка инварианта: после применения sum(quantity) == TARGET_TOTAL
        after_total = cur_total + sum(d for _, _, _, d in updates)
        print(f"Остаток склада после применения:   {after_total}")
        if after_total != TARGET_TOTAL:
            print(
                f"ERROR: инвариант нарушен — после будет {after_total}, ожидалось {TARGET_TOTAL}",
                file=sys.stderr,
            )
            return 1

        if not commit:
            print("\n[DRY-RUN] Изменений не внесено. Перезапусти с --commit.")
            return 0

        # ── применение ──────────────────────────────────────────────────────
        for barcode, _cur, _target, delta in updates:
            await create_adjustment(
                db,
                project_id,
                wh.id,
                {"barcode": barcode, "delta": delta, "reason": REASON},
            )
        print(f"\nГотово: применено {len(updates)} корректировок. Остаток склада = {after_total}.")
        return 0

    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    proj_g = parser.add_mutually_exclusive_group(required=True)
    proj_g.add_argument("--project-id", type=int)
    proj_g.add_argument("--project-slug", type=str)
    mode_g = parser.add_mutually_exclusive_group(required=True)
    mode_g.add_argument("--dry-run", action="store_true")
    mode_g.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    sys.exit(
        asyncio.run(
            main(
                project_id=args.project_id,
                project_slug=args.project_slug,
                commit=args.commit,
            )
        )
    )
