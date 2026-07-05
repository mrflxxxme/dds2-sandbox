# ruff: noqa: RUF002, RUF003
"""Расчётный вес товаров сборки из per-SKU веса.

Вес за 1 шт по barcode резолвится ЦЕПОЧКОЙ (зеркалит `get_missing_weight_barcodes`):
  1. `Nomenclature.weight_kg` (справочник), если задан и > 0;
  2. иначе — репрезентативный ненулевой `CostOrderItem.weight_kg` (наполнение
     машины). На локалке у большинства SKU вес указан только в машине — только
     справочник дал бы им 0.

`goods_weight_kg = Σ(item.quantity × unit_weight[barcode])` — нетто-вес товара
(без тары/паллет). ШК без веса нигде (справочник И машина) собираются в
`missing_barcodes` — оператор дозаполняет их в настройках (заполнение проливается
в машину). НЕ перезаписывает ручной `pallet_weight_kg` (тара).
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest
from backend.models.cost import CostOrderItem, Nomenclature

# Точность нетто-веса товара для показа (кг, до грамма).
_WEIGHT_Q = Decimal("0.001")


async def resolve_unit_weights(
    db: AsyncSession,
    project_id: int,
    barcodes: list[str],
) -> dict[str, Decimal | None]:
    """Вес за 1 шт по цепочке `Nomenclature.weight_kg` → машина (`CostOrderItem`).

    Ровно два батч-запроса (без N+1): справочник по всем ШК, затем машинный
    fallback только для ШК без справочного веса. Репрезентативный вес из машины =
    `MAX(weight_kg)` по ненулевым строкам (у SKU вес одинаков; MAX детерминирован).
    Возвращает {barcode: unit_weight | None}; None — веса нет нигде.
    """
    uniq = sorted({bc for bc in barcodes if bc})
    if not uniq:
        return {}

    # 1. Справочник.
    nom_rows = await db.execute(
        select(Nomenclature.barcode, Nomenclature.weight_kg).where(
            Nomenclature.project_id == project_id,
            Nomenclature.barcode.in_(uniq),
        )
    )
    nom_w: dict[str, Decimal | None] = {bc: w for bc, w in nom_rows.all()}

    # 2. Машина — только для ШК без справочного веса.
    def _has_ref(bc: str) -> bool:
        w = nom_w.get(bc)
        return w is not None and w > 0

    need_machine = [bc for bc in uniq if not _has_ref(bc)]
    machine_w: dict[str, Decimal | None] = {}
    if need_machine:
        m_rows = await db.execute(
            select(CostOrderItem.barcode, func.max(CostOrderItem.weight_kg))
            .where(
                CostOrderItem.project_id == project_id,
                CostOrderItem.is_deleted == False,  # noqa: E712
                CostOrderItem.weight_kg.isnot(None),
                CostOrderItem.weight_kg > 0,
                CostOrderItem.barcode.in_(need_machine),
            )
            .group_by(CostOrderItem.barcode)
        )
        machine_w = {bc: w for bc, w in m_rows.all()}

    out: dict[str, Decimal | None] = {}
    for bc in uniq:
        nw = nom_w.get(bc)
        out[bc] = nw if (nw is not None and nw > 0) else machine_w.get(bc)
    return out


async def compute_goods_weight(
    db: AsyncSession,
    project_id: int,
    request: AssemblyRequest,
    *,
    weight_by_barcode: dict[str, Decimal | None] | None = None,
) -> tuple[Decimal | None, list[str]]:
    """Посчитать нетто-вес товаров заявки и собрать ШК без веса.

    В list-контексте вызывающий передаёт уже резолвнутый `weight_by_barcode`
    (из prefetch, 0 запросов); одиночный вызов резолвит цепочку сам
    (`resolve_unit_weights`, 2 батч-запроса).

    Возвращает `(goods_weight_kg | None, missing_barcodes)`:
      - вес — Σ `quantity × unit_weight` по позициям с известным весом;
        None, если веса нет НИ у одной позиции;
      - missing_barcodes — уникальные ШК без веса нигде (отсортированы).
    """
    items = list(request.items or [])
    if not items:
        return None, []

    if weight_by_barcode is None:
        weight_by_barcode = await resolve_unit_weights(
            db, project_id, [it.barcode for it in items]
        )

    total = Decimal("0")
    any_weight = False
    missing: list[str] = []
    seen_missing: set[str] = set()
    for it in items:
        w = weight_by_barcode.get(it.barcode)
        if w is not None and w > 0:
            total += w * it.quantity
            any_weight = True
        elif it.barcode not in seen_missing:
            seen_missing.add(it.barcode)
            missing.append(it.barcode)

    goods_weight = total.quantize(_WEIGHT_Q) if any_weight else None
    return goods_weight, sorted(missing)


# ─── Коробочная тара: число коробов и расчётный вес отгрузки ──────────────────
#
# Итоговый вес отгрузки = нетто товаров + `box_weight_kg` × число коробов. Вес
# паллеты (деревянной тары) НЕ учитываем (решение пользователя). Кратность коробки
# резолвится per (склад заявки, barcode) — тем же движком, что раскладка/манифест
# (`resolve_box_qty_map`: machine → manual → override → none).


async def resolve_box_qty_by_warehouse(
    db: AsyncSession,
    project_id: int,
    wh_to_barcodes: dict[int, list[str]],
) -> dict[tuple[int, str], int | None]:
    """Кратность коробки per (warehouse_id, barcode) для НАБОРА складов сборок.

    Делегирует в `resolve_box_qty_map_multi` (один батч на все склады, ≈4 запроса
    total, не O(строк) и не O(складов×4)) — держим бюджет запросов списка без N+1.
    """
    from backend.services.box_multiplicity_service import resolve_box_qty_map_multi

    return await resolve_box_qty_map_multi(db, project_id, wh_to_barcodes)


def compute_boxes_count(
    request: AssemblyRequest,
    box_qty_by_wh_bc: dict[tuple[int, str], int | None],
) -> int:
    """Число ФИЗИЧЕСКИХ коробов заявки = Σ ⌈кол-во / кратность⌉ по позициям.

    Хвост < короба считается отдельным (неполным) коробом — физически это тоже
    коробка с тарой. Позиции без заданной кратности короба в счёт не идут.
    """
    wh_id = request.warehouse_id
    total = 0
    for it in request.items or []:
        bq = box_qty_by_wh_bc.get((wh_id, it.barcode))
        qty = int(it.quantity or 0)
        if bq and bq > 0 and qty > 0:
            total += -(-qty // bq)  # ceil(qty / bq)
    return total


def compute_suggested_total_weight(
    goods_weight_kg: Decimal | None,
    boxes_count: int,
    box_weight_kg: Decimal | None,
) -> Decimal | None:
    """Расчётный вес отгрузки = нетто товаров + вес_коробки × коробов.

    None, если нетто-веса нет (нечего предлагать — оператор заполнит справочник
    веса). Коробочная тара прибавляется только когда задан `box_weight_kg`.
    """
    if goods_weight_kg is None:
        return None
    total = goods_weight_kg
    if box_weight_kg is not None and box_weight_kg > 0 and boxes_count > 0:
        total = total + box_weight_kg * boxes_count
    return total.quantize(_WEIGHT_Q)
