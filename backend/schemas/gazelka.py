# ruff: noqa: RUF002, RUF003
"""
Gazelka (gazelka.space) — схемы передачи заявки логиста перевозчику.

Источник заявки в DDS — ``AssemblyRequest`` (готовая сборка, склад «Натали»).
Логист открывает диалог «Отправить в Газельку»: бэкенд снимает справочники ИХ
формы (``GazelkaFormOptions``) и предзаполняет что может (``GazelkaPrefill``),
логист дозаполняет и шлёт (``GazelkaSendRequest``) — это РЕАЛЬНОЕ создание заявки
во внешнем сервисе (необратимо).

Числовые поля Numeric сериализуются Pydantic v2 в JSON СТРОКОЙ — фронт коэрсит
Number() перед formatNumber (см. .claude/rules/learnings.md).
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class GazelkaSelectOption(BaseModel):
    """Опция их select-поля: value уходит в POST, label показывается логисту.

    У складов назначения название неуникально («Волгоград» есть и у Ozon, и у WB) —
    опции различают ``marketplace_id`` + ``place_id``; последний связывает её с графиком.
    """

    value: str
    label: str
    place_id: str | None = None
    marketplace_id: str | None = None


class GazelkaSchedulePlan(BaseModel):
    """График склада: дни погрузки/доставки (0=Вс … 6=Сб) и срок в пути."""

    loading_days: list[int] | None = None  # None — ограничения нет, годится любой день
    delivery_days: list[int] | None = None
    eta_days: int = 1


class GazelkaFormOptions(BaseModel):
    """Справочники формы заявки Газельки (снимаются живьём с /customer/apply)."""

    entities: list[GazelkaSelectOption] = Field(default_factory=list)  # юрлица (entity_id/payer_id)
    price_lists: list[GazelkaSelectOption] = Field(default_factory=list)  # города-источники (price_id)
    marketplaces: list[GazelkaSelectOption] = Field(default_factory=list)  # marketplace_id
    delivery_warehouses: list[GazelkaSelectOption] = Field(default_factory=list)  # delivery_address
    supply_types: list[GazelkaSelectOption] = Field(default_factory=list)  # monomix
    timeslots: list[GazelkaSelectOption] = Field(default_factory=list)  # daily_delivery_timeslot
    # Значения, ВЫБРАННЫЕ порталом в форме (option[selected]). Порядок опций у него
    # произвольный — «первая» ≠ «выбранная»: price_id идёт Симферополь…Иваново, а
    # выбрано Иваново. График зависит от price_id, поэтому дефолт брать только отсюда.
    default_entity_id: str | None = None
    default_price_id: str | None = None
    # Активные направления: ключ «{price_id}-{place_id}». Склад без записи для выбранного
    # прайса недоступен — фронт его прячет, бэк не даёт отправить заявку.
    schedule: dict[str, GazelkaSchedulePlan] = Field(default_factory=dict)
    min_departure_date: date | None = None
    min_delivery_date: date | None = None


class GazelkaPrefill(BaseModel):
    """Предзаполнение из AssemblyRequest — логист правит в диалоге."""

    customer_phone: str | None = None
    delivery_address: str | None = None  # их склад-dropdown, если удалось сматчить
    delivery_address_x2: str | None = None  # свободный адрес/склад текстом
    departure_date: date | None = None
    delivery_date: date | None = None
    delivery_contact: str | None = None
    daily_delivery_timeslot: str | None = None
    supply_id: str | None = None  # № поставки WB
    marketplace_id: str | None = None
    pallets: int = 0
    boxes: int = 0
    weight: Decimal | None = None  # общий вес доставки (кг)
    notes: str | None = None


class GazelkaConfigResponse(BaseModel):
    """Настроена ли интеграция и к какому складу (Натали) привязана.

    Фронт показывает кнопку «Отправить в Газельку» только для отгрузок с этого склада.
    """

    configured: bool
    warehouse_id: int | None = None
    warehouse_name: str | None = None


class GazelkaDraftResponse(BaseModel):
    """Данные для диалога: справочники + предзаполнение + признак уже-отправлено."""

    eligible: bool
    already_sent: bool = False
    sent_ref: str | None = None
    options: GazelkaFormOptions
    prefill: GazelkaPrefill


class GazelkaSendRequest(BaseModel):
    """Поля заявки Газельки, подтверждённые логистом."""

    # обязательные у них
    entity_id: str = Field(min_length=1)  # юрлицо-заказчик
    payer_id: str = Field(min_length=1)  # юрлицо-плательщик
    price_id: str = Field(min_length=1)  # город-источник (прайс)
    # маркетплейс
    is_marketplace: str = Field(default="yes")  # yes | no | ""
    marketplace_id: str | None = None
    supply_id: str | None = None  # № поставки на маркетплейсе
    # адрес/доставка
    delivery_address: str | None = None  # из их dropdown складов
    delivery_address_x2: str | None = None  # свободный адрес
    departure_date: date | None = None
    delivery_date: date | None = None
    delivery_time: str | None = None  # окно времени, свободный текст
    daily_delivery_timeslot: str | None = None  # Утром-Днём | Вечером
    delivery_contact: str | None = None
    customer_phone: str | None = None
    monomix: str | None = None  # тип поставки (Моно/Микс/…)
    # габариты/объём/вес
    pallets: int = Field(default=0, ge=0)
    boxes: int = Field(default=0, ge=0)
    weight2: Decimal | None = Field(default=None, ge=0)  # вес одного места (кг)
    weight: Decimal | None = Field(default=None, ge=0)  # общий вес (кг)
    volume: Decimal | None = Field(default=None, ge=0)  # общий объём (м³)
    length: int = Field(default=60, ge=0)
    height: int = Field(default=40, ge=0)
    width: int = Field(default=40, ge=0)
    palleting: bool = False
    notes: str | None = Field(default=None, max_length=1000)
    # Подтверждение повторной отправки: если для сборки уже есть SENT-заявка,
    # без этого флага send вернёт 409 (защита от двойного реального заказа).
    force_resend: bool = False


class GazelkaSendResult(BaseModel):
    ok: bool
    ref: str | None = None  # номер/идентификатор заявки у Газельки, если распознан
    message: str | None = None
    gazelka_order_id: int | None = None  # id нашей audit-записи GazelkaOrder


# ─── Списки заявок из портала (read) ─────────────────────────────────────────


class GazelkaOrderRow(BaseModel):
    """Строка заявки Газельки (запланированная или активная)."""

    gazelka_id: str
    status: str  # сырой код статуса портала
    status_label: str
    application_date: str | None = None
    departure_date: str | None = None
    departure_time: str | None = None
    departure_address: str | None = None
    delivery_date: str | None = None  # дата сдачи на склад МП
    delivery_time: str | None = None
    delivery_address: str | None = None
    marketplace: str | None = None
    monomix: str | None = None  # Моно/Микс (код)
    pallets: int = 0
    boxes: int = 0
    weight: str | None = None
    supply_id: str | None = None
    rate: str | None = None  # стоимость
    entity: str | None = None
    notes: str | None = None
    editable: bool = False  # можно редактировать (запланированные)
    # Связь с нашей сборкой (если заявку создавали из DDS или сматчили вручную)
    linked_assembly_id: int | None = None
    linked_assembly_number: str | None = None
    linked_assembly_status: str | None = None  # статус нашей сборки («где она находится»)
    # Авто-подсказка для матчинга по № поставки WB (если ещё не связана)
    suggested_assembly_id: int | None = None
    suggested_assembly_number: str | None = None
    # Логистика активной заявки (в маршруте)
    route_number: str | None = None
    route_date: str | None = None
    carrier: str | None = None
    driver_name: str | None = None
    driver_phone: str | None = None
    driver_passport: str | None = None
    vehicle: str | None = None
    finish_time: str | None = None


class GazelkaOrderList(BaseModel):
    items: list[GazelkaOrderRow] = Field(default_factory=list)
    count: int = 0


class GazelkaEditDraft(BaseModel):
    """Данные для редактирования заявки: их справочники + текущие значения."""

    gazelka_id: str
    options: GazelkaFormOptions
    values: GazelkaSendRequest


# ─── Матчинг существующих заявок портала с нашими сборками ────────────────────


class GazelkaMatchRequest(BaseModel):
    assembly_id: int


class GazelkaMatchResult(BaseModel):
    ok: bool
    linked_assembly_id: int | None = None
    linked_assembly_number: str | None = None


class GazelkaMatchCandidate(BaseModel):
    """Наша сборка — кандидат на ручное сопоставление с заявкой Газельки."""

    assembly_id: int
    number: str
    warehouse_name: str | None = None
    wb_supply_id: str | None = None
    delivery_date: date | None = None
    pallets_count: int | None = None
    status: str | None = None
    already_linked_to: str | None = None  # gazelka_id, если уже связана с другой заявкой
