# ruff: noqa: RUF001, RUF002, RUF003
"""Раскладка коробов по паллетам: сохранение манифеста + экспорт в Excel.

Манифест (`AssemblyRequest.pallet_manifest`, JSONB) — ручная перетасовка коробов
по паллетам оператором. NULL = «авто» (раскладка считается на лету геометрией на
фронте). Строгий инвариант сохранения: суммарно по всем паллетам каждая позиция
разложена ПОЛНОСТЬЮ — `Σ(box_count · box_qty + loose_units) == quantity`.

Экспорт:
  - internal — покоробочно с паллетами (для кладовщика);
  - wb       — по образцу загрузки в WB: строка = короб (Баркод | Кол-во | ШК
               короба | Срок годности). ШК короба выдаёт WB (у нас не хранится →
               всегда пусто), срок годности — всегда пусто (одежда).
"""

from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.models.assembly import AssemblyRequest
from backend.models.cost import Nomenclature
from backend.schemas.assembly import PalletManifest
from backend.services.assembly.crud import get_assembly_request
from backend.services.box_multiplicity_service import resolve_box_qty_map

XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class PalletManifestConflict(Exception):
    """Инвариант раскладки нарушен (Σ ≠ quantity) — роутер отдаёт 409."""


async def _box_qty_map_for(db: AsyncSession, request: AssemblyRequest) -> dict[str, int | None]:
    """Кратность короба (шт/короб) по всем ШК позиций на складе-источнике заявки."""
    barcodes = sorted({it.barcode for it in (request.items or []) if it.barcode})
    if not barcodes:
        return {}
    return await resolve_box_qty_map(db, request.project_id, request.warehouse_id, barcodes)


def _item_qty_map(request: AssemblyRequest) -> dict[str, int]:
    """Сколько штук каждого ШК в заявке (агрегат по позициям)."""
    out: dict[str, int] = {}
    for it in request.items or []:
        out[it.barcode] = out.get(it.barcode, 0) + it.quantity
    return out


async def update_pallet_manifest(
    db: AsyncSession,
    project_id: int,
    request_id: int,
    manifest: PalletManifest | None,
) -> AssemblyRequest:
    """Сохранить ручную раскладку или сбросить к «авто» (manifest=None/пустой).

    Валидация:
      1. Все ШК манифеста — из позиций заявки (иначе ValueError → 400).
      2. Строгий инвариант: каждая позиция разложена полностью
         `Σ(box_count·box_qty + loose_units) == quantity` (иначе
         PalletManifestConflict → 409). ШК без заданной кратности можно класть
         только россыпью (loose_units) — короба без кратности дают 0 штук.
    """
    request = await get_assembly_request(db, project_id, request_id)
    if request is None:
        raise ValueError("Assembly request not found")

    if manifest is None or not manifest.pallets:
        request.pallet_manifest = None
        await db.commit()
        await _invalidate(project_id)
        await db.refresh(request, ["items", "warehouse", "wb_fbo_supply"])
        return request

    item_qty = _item_qty_map(request)
    manifest_barcodes = {b.barcode for p in manifest.pallets for b in p.boxes}
    unknown = sorted(manifest_barcodes - set(item_qty))
    if unknown:
        raise ValueError(f"ШК не из состава заявки: {', '.join(unknown)}")

    box_qty_map = await _box_qty_map_for(db, request)

    placed: dict[str, int] = {}
    for pallet in manifest.pallets:
        for box in pallet.boxes:
            if box.box_count < 0 or box.loose_units < 0:
                raise ValueError("Отрицательное количество коробов/россыпи")
            bq = box_qty_map.get(box.barcode) or 0
            placed[box.barcode] = placed.get(box.barcode, 0) + box.box_count * bq + box.loose_units

    mismatches = [
        f"{bc}: разложено {placed.get(bc, 0)} из {qty}"
        for bc, qty in sorted(item_qty.items())
        if placed.get(bc, 0) != qty
    ]
    if mismatches:
        raise PalletManifestConflict("Раскладка не сходится с составом — " + "; ".join(mismatches))

    request.pallet_manifest = [p.model_dump() for p in manifest.pallets]
    await db.commit()
    await _invalidate(project_id)
    await db.refresh(request, ["items", "warehouse", "wb_fbo_supply"])
    return request


async def _invalidate(project_id: int) -> None:
    """Инвалидация доменных кэшей сборки (как у прочих мутаций домена, iron rule 7)."""
    await invalidate_cache("reports:assembly_flow")
    await invalidate_cache("reports:assembly_link_anomalies")
    await invalidate_cache("reports:warehouse_need")


# ─── Excel-экспорт ──────────────────────────────────────────────────────────


def _pallets_or_auto(
    request: AssemblyRequest, box_qty_map: dict[str, int | None]
) -> list[dict]:
    """Раскладка для экспорта: ручной манифест либо авто (одна псевдо-паллета).

    Авто-раскладка не считает геометрию (это фронт) — раскрывает каждую позицию
    в целые короба по кратности + хвост-россыпь на одной паллете «—». Достаточно
    для покоробочного WB-файла; для внутреннего = список коробов без ручного
    распределения по паллетам.
    """
    if request.pallet_manifest:
        return request.pallet_manifest
    boxes: list[dict] = []
    for bc, qty in _item_qty_map(request).items():
        bq = box_qty_map.get(bc) or 0
        if bq > 0:
            boxes.append({"barcode": bc, "box_count": qty // bq, "loose_units": qty % bq})
        else:
            boxes.append({"barcode": bc, "box_count": 0, "loose_units": qty})
    return [{"pallet_no": 0, "boxes": boxes}]  # pallet_no=0 → «не распределено»


async def _names_by_barcode(
    db: AsyncSession, project_id: int, barcodes: list[str]
) -> dict[str, Nomenclature]:
    if not barcodes:
        return {}
    rows = await db.execute(
        select(Nomenclature).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(sorted(set(barcodes))),
        )
    )
    return {n.barcode: n for n in rows.scalars().all()}


async def build_pallet_layout_xlsx(
    db: AsyncSession,
    project_id: int,
    request: AssemblyRequest,
    fmt: str = "internal",
) -> bytes:
    """Собрать .xlsx раскладки (`internal`|`wb`). Возвращает байты файла."""
    box_qty_map = await _box_qty_map_for(db, request)
    pallets = _pallets_or_auto(request, box_qty_map)
    all_barcodes = [b["barcode"] for p in pallets for b in p["boxes"]]
    nom_map = await _names_by_barcode(db, project_id, all_barcodes)

    if fmt == "wb":
        return _build_wb_xlsx(pallets, box_qty_map)
    return _build_internal_xlsx(request, pallets, box_qty_map, nom_map)


def _build_wb_xlsx(pallets: list[dict], box_qty_map: dict[str, int | None]) -> bytes:
    """Покоробочный файл загрузки в WB: строка = один короб.

    ШК короба — выдаёт WB (у нас не хранится) → пусто. Срок годности — пусто.
    Хвост-россыпь (loose_units) выгружается отдельной строкой-коробом.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Короба"
    ws.append(["Баркод товара", "Кол-во товаров", "ШК короба", "Срок годности"])
    for pallet in pallets:
        for box in pallet["boxes"]:
            bc = box["barcode"]
            bq = box_qty_map.get(bc) or 0
            for _ in range(int(box.get("box_count", 0))):
                ws.append([bc, bq, "", ""])
            loose = int(box.get("loose_units", 0))
            if loose > 0:
                ws.append([bc, loose, "", ""])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_internal_xlsx(
    request: AssemblyRequest,
    pallets: list[dict],
    box_qty_map: dict[str, int | None],
    nom_map: dict[str, Nomenclature],
) -> bytes:
    """Внутренняя раскладка для кладовщика: покоробочно, сгруппировано по паллетам."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Раскладка"
    headers = ["Паллета", "Баркод", "Артикул", "Товар", "Коробов", "Россыпь, шт", "Всего, шт"]
    ws.append(headers)

    grand_units = 0
    grand_boxes = 0
    for pallet in pallets:
        pallet_no = pallet.get("pallet_no", 0)
        pallet_label = str(pallet_no) if pallet_no else "—"
        pallet_units = 0
        pallet_boxes = 0
        for box in pallet["boxes"]:
            bc = box["barcode"]
            nom = nom_map.get(bc)
            bq = box_qty_map.get(bc) or 0
            box_count = int(box.get("box_count", 0))
            loose = int(box.get("loose_units", 0))
            total = box_count * bq + loose
            ws.append(
                [
                    pallet_label,
                    bc,
                    (nom.article_seller if nom else None) or "",
                    (nom.subject if nom else None) or "",
                    box_count,
                    loose,
                    total,
                ]
            )
            pallet_units += total
            pallet_boxes += box_count
        ws.append([f"Паллета {pallet_label} — итого", "", "", "", pallet_boxes, "", pallet_units])
        grand_units += pallet_units
        grand_boxes += pallet_boxes

    ws.append(["ИТОГО", "", "", "", grand_boxes, "", grand_units])

    # Тара (ручной вес паллеты) — справочно, чтобы кладовщик видел вес отгрузки.
    pallets_count = request.pallets_count or 0
    pallet_weight = request.pallet_weight_kg or Decimal("0")
    ws.append([])
    ws.append(["Паллет (ручной)", pallets_count])
    ws.append(["Вес паллеты, кг", float(pallet_weight)])
    ws.append(["Вес отгрузки, кг", float(Decimal(str(pallets_count)) * pallet_weight)])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def apply_goods_weight(
    db: AsyncSession,
    project_id: int,
    request_id: int,
) -> AssemblyRequest:
    """Проставить расчётный вес товаров в ручной `pallet_weight_kg` (÷ паллеты).

    Удобная кнопка: `pallet_weight_kg = goods_weight_kg / pallets_count`. Если у
    заявки нет позиций с весом — ValueError (→ 400)."""
    from backend.services.assembly.weight import compute_goods_weight

    request = await get_assembly_request(db, project_id, request_id)
    if request is None:
        raise ValueError("Assembly request not found")

    goods_weight, _missing = await compute_goods_weight(db, project_id, request)
    if goods_weight is None:
        raise ValueError("Ни у одной позиции нет веса — заполните справочник веса в настройках")

    pallets = request.pallets_count or 1
    per_pallet = (goods_weight / Decimal(pallets)).quantize(Decimal("0.01"))
    request.pallet_weight_kg = per_pallet
    await db.commit()
    await _invalidate(project_id)
    await invalidate_cache("reports:logistics_analytics_v2")  # вес паллеты → тоталы аналитики
    await db.refresh(request, ["items", "warehouse", "wb_fbo_supply"])
    return request
