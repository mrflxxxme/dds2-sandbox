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
    filter_delivery_type: DeliveryType = "direct"
    notes: str | None = None
    wb_warehouse_name: str | None = None  # инфо: куда отгрузка (WB-склад назначения)
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

    filter_delivery_type: DeliveryType = "direct"
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
