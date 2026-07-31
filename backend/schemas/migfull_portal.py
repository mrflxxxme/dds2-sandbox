# ruff: noqa: RUF002, RUF003
"""
migfull-портал (plusvb.migfull.app) — схемы создания заявки на отгрузку у ФФ «Натали».

Источник заявки в DDS — ``AssemblyRequest`` (готовая сборка, склад «Натали»).
Пользователь открывает модалку «Создать заявку в ФФ»: бэкенд строит превью —
шапку (``MigfullShipmentPrefill``) и строки описи (``MigfullOpisLine``) из состава
сборки + «Сопоставления». Пользователь проверяет, дозаполняет тип доставки и шлёт
(``MigfullSendRequest``) — это РЕАЛЬНОЕ создание заявки на портале (необратимо).

НЕ путать с read-only API (``migfull_client.py``, service="migfull").
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

# Тип доставки у migfull (поле filter_delivery_type) — фиксированный справочник
DeliveryType = Literal["direct", "transit", "pickup"]

DELIVERY_TYPE_LABELS: dict[str, str] = {
    "direct": "Прямая поставка",
    "transit": "Транзит",
    "pickup": "Самовывоз",
}


class MigfullPortalConfigResponse(BaseModel):
    """Настроена ли интеграция и к какому складу (Натали) привязана.

    Фронт показывает кнопку «Создать заявку в ФФ» только для сборок с этого склада.
    """

    configured: bool
    warehouse_id: int | None = None
    warehouse_name: str | None = None


class MigfullDeliveryTypeOption(BaseModel):
    value: str  # direct | transit | pickup
    label: str


class MigfullOpisLine(BaseModel):
    """Строка описи: превью в модалке + источник для .xlsx.

    Для коробов: ``barcode`` = ШК короба (ITF14), ``quantity`` = число коробов.
    Для россыпи: ``barcode`` = ШК товара (EAN13), ``quantity`` = число штук.
    """

    barcode: str
    name: str | None = None
    size: str | None = None
    color: str | None = None
    quantity: int  # коробов (короб) или штук (россыпь) — колонка «Кол-во» описи
    is_box: bool = False
    units_per_box: int = 1
    pieces: int  # всего штук (для сверки/инфо)


class MigfullShipmentPrefill(BaseModel):
    """Предзаполнение шапки из AssemblyRequest — пользователь правит в модалке."""

    number: str | None = None  # № поставки WB (= wb_fbo_supply.wb_supply_id)
    shipment_date: date | None = None
    # Самовывоз — портал migfull персистит склад назначения ТОЛЬКО при pickup
    # (при direct/transit значение молча роняется); реальные заявки Натали — самовывоз.
    filter_delivery_type: DeliveryType = "pickup"
    notes: str | None = None
    wb_warehouse_name: str | None = None  # инфо: куда отгрузка (WB-склад назначения, наше имя)
    destination_name: str | None = None  # распознанный склад назначения в ФФ (показ в модалке)
    destination_matched: bool = False  # удалось ли сматчить склад → выставим при создании
    assembly_number: str | None = None


class MigfullDraftResponse(BaseModel):
    """Данные для модалки: справочник доставки + предзаполнение + превью описи."""

    eligible: bool
    already_sent: bool = False
    sent_guid: str | None = None
    sent_number: str | None = None
    prefill: MigfullShipmentPrefill
    delivery_types: list[MigfullDeliveryTypeOption] = Field(default_factory=list)
    opis_lines: list[MigfullOpisLine] = Field(default_factory=list)
    total_boxes: int = 0
    total_pieces: int = 0
    warnings: list[str] = Field(default_factory=list)  # напр. кол-во не кратно коробу


class MigfullSendRequest(BaseModel):
    """Поля заявки, подтверждённые пользователем в модалке."""

    filter_delivery_type: DeliveryType = "pickup"
    number: str | None = Field(default=None, max_length=100)
    shipment_date: date | None = None
    notes: str | None = Field(default=None, max_length=1000)
    # Подтверждение повторной отправки: если у сборки уже есть SENT-заявка или
    # связанная FulfillmentRequest(migfull), без флага send вернёт 409 — защита от
    # двойного НЕОБРАТИМОГО создания (портал не даёт удалить/отменить заявку).
    force_resend: bool = False


class MigfullSendResult(BaseModel):
    ok: bool
    shipment_guid: str | None = None
    shipment_number: str | None = None
    message: str | None = None
    order_id: int | None = None  # id нашей audit-записи MigfullShipmentOrder


# ─── Поставка (приёмка) на склад Натали из нашей приёмки машины ──────────────
# Источник в DDS — InboundReceipt (приёмка машины V-… на склад «Натали»).
# На портале это зеркальный ресурс /app/submissions (в read-API — submissions,
# kind=inbound в зеркале FulfillmentRequest).


# Источник кратности строки состава: карта Натали (короб в зеркале ФФ) →
# наша кратность отгрузки (box multiplicity: строки машины / per-ФФ / SKU-дефолт) → нет.
PackSource = Literal["natali", "ours", "none"]

# Откуда ШК короба: карта Натали (реальный короб в зеркале ФФ) либо
# выведенный нами GTIN-14 из EAN13 товара (ean13_to_itf14).
BoxBarcodeSource = Literal["natali", "derived"]


class MigfullInboundItem(BaseModel):
    """Строка СОСТАВА приёмки для редактируемой модалки (по-SKU, до нарезки на короба).

    ``units_per_box`` — prefill кратности по цепочке natali → ours → none
    (``pack_source``); пользователь правит в модалке и шлёт per-line packing.
    ``box_barcode`` — ШК короба (ITF14): из карты Натали либо выведенный GTIN-14
    (``box_barcode_source``). ``units_natali``/``units_ours`` — ОБЕ стороны
    кратности, когда известны (модалка показывает нестыковки: наша ≠ у Натали).
    """

    barcode: str  # ШК товара (EAN13)
    name: str | None = None
    article_seller: str | None = None  # наш артикул (Nomenclature по barcode)
    qty: int  # всего штук в приёмке
    units_per_box: int | None = None  # prefill: шт в коробе (None — кратность неизвестна)
    pack_source: PackSource = "none"
    box_barcode: str | None = None
    box_barcode_source: BoxBarcodeSource | None = None
    units_natali: int | None = None  # кратность из карты Натали (если известна)
    units_ours: int | None = None  # НАША кратность (строки машины / per-ФФ / SKU-дефолт)


class MigfullPackingLine(BaseModel):
    """Per-line packing из модалки: как везём строку — коробом или россыпью.

    ``units_per_box``: None/1 — россыпь; >=2 — короба по N шт, некратный
    остаток уходит россыпью. Сервис строит опись ПО ЭТИМ строкам, не пересчитывая.

    ``boxes`` — ЯВНЫЙ сплит: сколько КОРОБОВ отправить (остаток qty − boxes×units
    едет россыпью БЕЗ warning — осознанный выбор в модалке); 0 — вся строка
    россыпью даже при заданной кратности; None — прежнее поведение (максимум
    коробов, некратный остаток с warning).
    """

    barcode: str = Field(min_length=1, max_length=100)
    qty: int  # >0 (валидация в сервисе → 400, не 422)
    units_per_box: int | None = None  # >=1 (валидация в сервисе)
    boxes: int | None = None  # >=0, boxes×units_per_box <= qty (валидация в сервисе)


class MigfullInboundPrefill(BaseModel):
    """Предзаполнение шапки поставки из приёмки/машины — пользователь правит в модалке."""

    number: str | None = None  # наш номер для оператора (V-… машины / № приёмки)
    submission_date: date | None = None  # плановая дата поставки
    notes: str | None = None
    vehicle_order_no: str | None = None  # инфо: машина-источник (V-…)
    receipt_number: str | None = None  # инфо: наша приёмка (IN-…)


class MigfullInboundDraftResponse(BaseModel):
    """Данные для confirm-модалки: предзаполнение + превью описи (позиции/штуки/короба)."""

    eligible: bool
    already_sent: bool = False
    sent_guid: str | None = None
    sent_number: str | None = None
    prefill: MigfullInboundPrefill
    # Редактируемый состав по-SKU (prefill кратности + источник) — модалка строит
    # из него per-line packing; opis_lines — превью описи по дефолтному packing.
    items: list[MigfullInboundItem] = Field(default_factory=list)
    opis_lines: list[MigfullOpisLine] = Field(default_factory=list)
    total_boxes: int = 0
    total_pieces: int = 0
    warnings: list[str] = Field(default_factory=list)


class MigfullInboundSendRequest(BaseModel):
    """Поля поставки, подтверждённые пользователем в модалке."""

    number: str | None = Field(default=None, max_length=100)
    submission_date: date | None = None
    notes: str | None = Field(default=None, max_length=1000)
    # Per-line packing из модалки (коробом/россыпью + шт в коробе). None —
    # legacy/дефолт: сервис строит опись по цепочке natali → ours → россыпь.
    packing: list[MigfullPackingLine] | None = None
    # Подтверждение повторной отправки: если у приёмки уже есть SENT-поставка или
    # связанная FulfillmentRequest(migfull, kind=inbound), без флага send вернёт 409 —
    # создание на портале НЕОБРАТИМО (нет delete/cancel).
    force_resend: bool = False
