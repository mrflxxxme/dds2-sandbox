# ruff: noqa: RUF002, RUF003
"""Расчётный вес товаров сборки из per-SKU веса номенклатуры.

`goods_weight_kg = Σ(item.quantity × Nomenclature.weight_kg[item.barcode])` —
нетто-вес самого товара (без тары/паллет). Источник — `Nomenclature.weight_kg`
(тот же справочник, что ведёт таблица «Вес (кг)» в настройках). Позиции без веса
собираются в `missing_barcodes` — оператор дозаполняет их в настройках.

НЕ перезаписывает ручной `pallet_weight_kg` (тара) — показывается отдельным полем.
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.assembly import AssemblyRequest
from backend.models.cost import Nomenclature

# Точность нетто-веса товара для показа (кг, до грамма).
_WEIGHT_Q = Decimal("0.001")


async def compute_goods_weight(
    db: AsyncSession,
    project_id: int,
    request: AssemblyRequest,
    *,
    weight_by_barcode: dict[str, Decimal | None] | None = None,
) -> tuple[Decimal | None, list[str]]:
    """Посчитать нетто-вес товаров заявки и собрать ШК без веса.

    Резолв веса — батчем по barcode одним `SELECT ... WHERE barcode IN (...)`
    (без N+1). В list-контексте вызывающий передаёт уже готовый
    `weight_by_barcode` (из prefetch), тогда запрос не делается вовсе.

    Возвращает `(goods_weight_kg | None, missing_barcodes)`:
      - вес — сумма `quantity × weight_kg` по позициям с известным весом;
        None, если вес не задан НИ у одной позиции (нечего показывать);
      - missing_barcodes — уникальные ШК позиций без веса (отсортированы).
    """
    items = list(request.items or [])
    if not items:
        return None, []

    if weight_by_barcode is None:
        barcodes = sorted({it.barcode for it in items if it.barcode})
        weight_by_barcode = {}
        if barcodes:
            rows = await db.execute(
                select(Nomenclature.barcode, Nomenclature.weight_kg).where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.barcode.in_(barcodes),
                )
            )
            weight_by_barcode = {bc: w for bc, w in rows.all()}

    total = Decimal("0")
    any_weight = False
    missing: list[str] = []
    seen_missing: set[str] = set()
    for it in items:
        w = weight_by_barcode.get(it.barcode)
        if w is not None and w > 0:
            total += Decimal(str(w)) * it.quantity
            any_weight = True
        elif it.barcode not in seen_missing:
            seen_missing.add(it.barcode)
            missing.append(it.barcode)

    goods_weight = total.quantize(_WEIGHT_Q) if any_weight else None
    return goods_weight, sorted(missing)
