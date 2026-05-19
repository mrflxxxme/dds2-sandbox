# ruff: noqa: RUF002, RUF003
"""Pydantic schemas for box-multiplicity (кратность коробки) feature.

Кратность резолвится ПЕР пару (товар barcode, ФФ-склад). Источники, в порядке
приоритета (первый сработавший побеждает):
  1. machine — последняя ACCEPTED-приёмка товара на этот ФФ → её машина →
     наполняемость строки (qty-weighted mode). Ячейка заблокирована.
  2. manual  — `BoxQtyPerWarehouse.box_qty` для пары (barcode, warehouse_id).
  3. default — `Nomenclature.box_qty_override` (SKU-уровневый дефолт).
  4. none    — ничего не задано.
"""

from pydantic import BaseModel, Field


class BoxMultiplicityPerWarehouseRow(BaseModel):
    """Резолв кратности для одной пары (SKU, ФФ-склад)."""

    warehouse_id: int
    warehouse_name: str
    box_qty: int | None = None  # резолвленная кратность; null = не определена
    box_size: str | None = None  # размер коробки (если известен из машины)
    source: str = "none"  # machine | manual | default | none
    editable: bool = True  # false только когда source == "machine"
    use_box_multiplicity: bool = True  # per-ФФ флаг (default true)
    rf_stock: int = 0  # сток конкретно на этом ФФ (для UI)
    machine_order_no: str | None = None  # order_no машины-источника (source=machine)
    machine_received_at: str | None = None  # ISO date приёмки (source=machine)


class BoxMultiplicityRow(BaseModel):
    """One SKU row: SKU-уровневые поля + per-ФФ резолв кратности."""

    nm_id: int
    vendor_code: str | None = None
    barcode: str
    brand: str | None = None
    subject: str | None = None
    box_qty_override: int | None = None  # Nomenclature.box_qty_override (manual SKU-level)
    use_box_multiplicity: bool = True  # SKU-level флаг (default для ФФ без per-ФФ override)
    has_machine_data: bool = False  # есть хотя бы один ФФ с машинным резолвом
    # ─── stock metrics for filters ────────────────────────────────────────
    rf_stock: int = 0  # total qty across active fulfillment warehouses
    in_assembly: int = 0  # reserved in PENDING/IN_PROGRESS/READY/VEHICLE_ASSIGNED
    in_transit: int = 0  # SHIPPED assemblies en-route to WB
    wb_stock: int = 0  # total qty across all WB warehouses
    # ─── per-ФФ резолв ─────────────────────────────────────────────────────
    per_warehouse: list[BoxMultiplicityPerWarehouseRow] = []  # one entry per active ФФ


class BoxMultiplicityResponse(BaseModel):
    items: list[BoxMultiplicityRow]
    total: int


class BoxMultiplicityUpdate(BaseModel):
    """PATCH SKU-уровня. `box_qty_override=null` сбрасывает дефолт; положительное
    число задаёт его. `use_box_multiplicity` — SKU-уровневый toggle.
    """

    box_qty_override: int | None = Field(default=None, ge=1, le=10000)
    use_box_multiplicity: bool | None = None


class BoxMultiplicityPerWarehouseUpdate(BaseModel):
    """PATCH per-ФФ ручной кратности/размера. Partial: все поля опциональные."""

    box_qty: int | None = Field(default=None, ge=1, le=10000)
    box_size: str | None = Field(default=None, max_length=50)
    use_box_multiplicity: bool | None = None


class BoxMultiplicityBulkItem(BaseModel):
    """One paste row: match by barcode, partial update.

    Если `warehouse_id` задан — апдейтим строку в `box_qty_per_warehouse`
    (per-ФФ ручная кратность). Иначе — апдейтим SKU-level поля в `Nomenclature`.
    """

    barcode: str = Field(min_length=1, max_length=50)
    warehouse_id: int | None = None
    box_qty_override: int | None = Field(default=None, ge=1, le=10000)
    box_size: str | None = Field(default=None, max_length=50)
    use_box_multiplicity: bool | None = None


class BoxMultiplicityBulkRequest(BaseModel):
    items: list[BoxMultiplicityBulkItem] = Field(default_factory=list, max_length=5000)


class BoxMultiplicityBulkResponse(BaseModel):
    updated: list[BoxMultiplicityRow]  # rows actually changed
    not_found: list[str]  # barcodes that don't exist in this project
    matched_count: int  # how many barcodes matched (some may have had no diff)
    locked: list[str] = []  # barcodes/ФФ machine-locked — box_qty не применён


class BoxMultiplicitySourceRow(BaseModel):
    """Один источник снабжения SKU для drill-down (read-only история):
    строка машины (cost order) либо строка заказа на фабрику (factory order).
    """

    source_type: str  # "vehicle" | "factory"
    order_no: str  # машина order_no либо factory order_number
    warehouse_name: str | None = None  # ФФ-склад назначения (только vehicle)
    qty: int = 0
    box_qty: int | None = None  # наполняемость строки
    box_size: str | None = None  # размер коробки (mix-вариант если позиция в миксе)
    date: str | None = None  # ISO date — прибытие машины / дата заказа
    status: str | None = None  # статус машины; None для factory
    accepted: bool = False  # true если у машины есть ACCEPTED-приёмка


class BoxMultiplicitySourcesResponse(BaseModel):
    items: list[BoxMultiplicitySourceRow]


class BoxMultiplicityChangeRow(BaseModel):
    """Одна запись «Истории изменений» кратности/размера коробки."""

    id: int
    warehouse_id: int | None = None  # None = изменение SKU-уровня
    warehouse_name: str | None = None  # имя ФФ (для per-ФФ записи)
    field: str  # box_qty_override | box_qty | box_size | use_box_multiplicity
    old_value: str | None = None  # None = было «не задано»
    new_value: str | None = None  # None = стало «не задано»
    change_source: str = "manual"  # manual | bulk | revert
    created_at: str  # ISO datetime


class BoxMultiplicityChangesResponse(BaseModel):
    items: list[BoxMultiplicityChangeRow]
