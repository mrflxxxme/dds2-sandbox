# ruff: noqa: RUF001, RUF002, RUF003
"""
Pydantic-схемы интеграции заявки-сборки с FBW-поставкой WB (реплей портала).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class WbBoxItem(BaseModel):
    barcode: str
    quantity: int


class WbBox(BaseModel):
    boxcode: str | None = None  # null, пока короб не создан в WB
    items: list[WbBoxItem]


class WbSupplyState(BaseModel):
    """Текущее состояние связи заявки с WB-поставкой."""

    model_config = ConfigDict(from_attributes=True)

    assembly_request_id: int
    sync_status: str
    warehouse_id_wb: int | None = None
    box_type_id: int | None = None
    draft_id: str | None = None
    preorder_id: int | None = None
    supply_id: int | None = None
    barcode_id: int | None = None
    last_error: str | None = None
    last_synced_at: datetime | None = None
    # Живое состояние поставки в кабинете (bulk-синк listSupplies).
    wb_supply_state: str | None = None
    wb_supply_state_id: int | None = None
    wb_state_synced_at: datetime | None = None
    boxes: list[WbBox] = []
    pass_driver_first: str | None = None
    pass_driver_last: str | None = None
    pass_driver_phone: str | None = None
    pass_car_model: str | None = None
    pass_car_number: str | None = None
    pass_pallets: int | None = None
    # Зеркало назначенной машины заявки (read-only) — для префилла пропуска и
    # подсветки расхождений «машина заявки ↔ WB-пропуск» (F3). Заполняется
    # сервисом из AssemblyRequest, в самой таблице wb-поставки не хранится.
    assembly_vehicle_info: str | None = None
    assembly_vehicle_brand: str | None = None
    assembly_driver_phone: str | None = None
    # Локальное число паллет заявки — для баннера «паллеты ≠ WB» (F2).
    assembly_pallets_count: int | None = None


class WbPreorderCreate(BaseModel):
    """Параметры создания преордера. package_type — тип упаковки
    (BOX/MONOPALLET/SUPERSAFE); None → берётся тип заявки."""

    package_type: str | None = None


class WbBoxesUpdate(BaseModel):
    """Локальная правка раскладки коробов (до заноса в WB)."""

    boxes: list[WbBox]


class WbPassUpdate(BaseModel):
    """Локальная правка данных пропуска (до заноса в WB)."""

    driver_first: str | None = None
    driver_last: str | None = None
    driver_phone: str | None = None
    car_model: str | None = None
    car_number: str | None = None
    pallets: int | None = None


class WbSupplyStateBrief(BaseModel):
    """Компактная WB-сводка для строки списка заявок (F1/F2).

    Отдаётся вместе с заявкой в списке, чтобы не дёргать get_state построчно.
    """

    model_config = ConfigDict(from_attributes=True)

    sync_status: str
    wb_supply_state: str | None = None
    supply_id: int | None = None
    preorder_id: int | None = None
    pass_pallets: int | None = None
    wb_state_synced_at: datetime | None = None


class WbBulkSyncResult(BaseModel):
    """Итог bulk-синка WB-состояний заявок проекта (F1)."""

    checked: int  # сколько заявок имели preorder/supply и проверялись
    updated: int  # у скольких статус WB изменился
    supplies_seen: int  # сколько поставок вернул кабинет


class WbCabinetBoxItem(BaseModel):
    """Товар внутри короба кабинета (из ListBarcodesBoxes)."""

    barcode: str
    quantity: int = 0
    imt_name: str | None = None  # название карточки
    img_src: str | None = None  # фото
    brand: str | None = None
    sa_nm: str | None = None  # артикул продавца
    nm_id: int | None = None
    color_name: str | None = None
    volume: float | None = None


class WbCabinetBox(BaseModel):
    """Короб поставки WB с содержимым (вкладка «Упаковка», как в кабинете)."""

    boxcode: str
    quantity: int = 1
    items: list[WbCabinetBoxItem] = []


class WbCabinetBoxes(BaseModel):
    """Короба поставки из кабинета + сводка (шапка «N коробов · X/Y · Z шт»)."""

    boxes: list[WbCabinetBox] = []
    total_boxes: int = 0
    total_barcodes: int = 0
    total_units: int = 0


class WbDriver(BaseModel):
    firstName: str
    lastName: str
    phone: str = ""


class WbPortalSessionSet(BaseModel):
    """Занести/обновить session-токен кабинета WB."""

    authorizev3: str


class WbPortalStatus(BaseModel):
    status: str  # NONE | ACTIVE | EXPIRED
    updated_at: str | None = None
