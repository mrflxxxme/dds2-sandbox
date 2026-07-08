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
    boxes: list[WbBox] = []
    pass_driver_first: str | None = None
    pass_driver_last: str | None = None
    pass_driver_phone: str | None = None
    pass_car_model: str | None = None
    pass_car_number: str | None = None
    pass_pallets: int | None = None


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
