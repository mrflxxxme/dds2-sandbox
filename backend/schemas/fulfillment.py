# ruff: noqa: RUF002, RUF003
"""
Pydantic schemas: fulfillment integration (skladbot, migfull).

API contract for /warehouse/{warehouse_id}/fulfillment/* endpoints.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── Connection ──────────────────────────────────────────────────────────────


class FulfillmentConnectPayload(BaseModel):
    provider: Literal["skladbot", "wmscelicom", "migfull"] = "skladbot"
    token: str = Field(min_length=20, max_length=4096)
    # wmscelicom: адрес клиентского инстанса ({client}.wmscelicom.ru)
    base_url: str | None = Field(None, max_length=200)
    # migfull («Натали»): GUID кабинета клиента (хост фиксированный — migfull.app)
    tenant_guid: str | None = Field(None, max_length=64)
    # skladbot: id кабинета клиента. Обязателен, когда токен видит >1 клиента
    # (FF-operator токен видит весь tenant) — иначе заявки/остатки уйдут не тому.
    # Для селлер-токена (1 клиент) можно не указывать.
    customer_id: int | None = None


class FulfillmentStatus(BaseModel):
    connected: bool
    provider: str | None = None
    key_preview: str | None = None  # "***xxxx", сам токен назад не отдаём
    customer_id: int | None = None
    customer_name: str | None = None
    token_expires_at: datetime | None = None
    api_base_url: str | None = None  # wmscelicom: инстанс, на который ходим
    tenant_guid: str | None = None  # migfull: GUID кабинета
    last_sync_at: datetime | None = None
    # Складом управляет оператор ФФ-портала (Хамза): сборки видны ему в /ff/*
    # автоматически — отдельная «заявка ФФ» не нужна (интеграции может не быть).
    has_portal_operator: bool = False


# ─── Stocks ──────────────────────────────────────────────────────────────────


class FfStockRow(BaseModel):
    barcode: str
    name: str | None = None
    vendor_code: str | None = None
    nomenclature_id: int | None = None
    article_seller: str | None = None  # наш артикул (если товар сматчен)
    subject: str | None = None  # предмет из номенклатуры (если сматчен)
    brand: str | None = None  # бренд из номенклатуры (если сматчен)
    ff_good: int = 0
    ff_reserve: int = 0
    ff_reserve_ready: int = 0  # migfull: часть резерва под активные отгрузки (собрано)
    ff_inbound_locked: int = 0  # migfull: часть резерва под свежий приход (EXPECTED-приёмки)
    ff_defect: int = 0  # migfull: расч. = ff_reserve − собрано − В приёмке; прочие — из API
    ff_nominal: int = 0
    ff_box_units: int = 0  # из ff_good пришло коробами (в штуках россыпи)
    ff_box_count: int = 0  # сколько коробов годного сведено в этот товар
    ff_logistics: int = 0  # досчитано к ff_good: товар в стадии списания логистики ФФ, ещё на складе
    #: Вычтено ИЗ ff_good: отгружено по FBS у нас (движения FBS_ORDER), а
    #: провайдер выбытие не отразил — ни один из трёх WMS остаток под FBS не
    #: снимает (сверка 29.07.2026). ff_good приходит уже ЗА ВЫЧЕТОМ, поле —
    #: расшифровка, симметрично ff_logistics. Кап: не больше сырого ff_good.
    ff_fbs: int = 0
    our_quantity: int = 0
    our_defect: int = 0
    diff: int = 0  # прочие: ff_good − our_quantity; migfull: ff_good − (our_quantity + our_defect)


class FfStockTotals(BaseModel):
    ff_good: int = 0
    ff_reserve: int = 0
    ff_reserve_ready: int = 0  # migfull: резерв под активные отгрузки (собрано)
    ff_inbound_locked: int = 0  # migfull: резерв под свежий приход (EXPECTED-приёмки)
    ff_defect: int = 0
    ff_box_units: int = 0  # сколько штук годного пришло коробами
    ff_logistics: int = 0  # досчитано к ff_good: товар в стадии списания логистики ФФ
    ff_fbs: int = 0  # вычтено из ff_good: отгружено по FBS, провайдер ещё не списал
    our_quantity: int = 0
    diff: int = 0
    unmatched: int = 0  # строк ФФ без нашей номенклатуры


class FfStocksResponse(BaseModel):
    rows: list[FfStockRow]
    totals: FfStockTotals
    synced_at: datetime | None = None
    subjects: list[str] = Field(default_factory=list)  # distinct предметы для фильтра
    brands: list[str] = Field(default_factory=list)  # distinct бренды для фильтра


class FfBoxPack(BaseModel):
    """Строка сопоставления короб→россыпь (авто-вывод при синке)."""

    box_barcode: str  # ШК короба (ITF14)
    base_barcode: str | None = None  # ШК россыпи (EAN13); None — короб ещё не сопоставлен
    units_per_box: int  # штук россыпи в коробе («короб N шт.» из названия)
    name: str | None = None  # название коробной карточки у ФФ
    nomenclature_id: int | None = None
    article_seller: str | None = None  # наш артикул (если сматчен)
    subject: str | None = None
    box_qty: int = 0  # остаток в коробах
    units_qty: int = 0  # = box_qty × units_per_box (в штуках россыпи)
    matched: bool = False  # сматчен ли короб с нашей номенклатурой
    source: str = "auto"  # auto — авто-вывод | manual — ручной override | unmapped — не сопоставлен


class FfBoxOverridePayload(BaseModel):
    """Ручная привязка короба: наша номенклатура + штук в коробе."""

    nomenclature_id: int
    units_per_box: int = Field(ge=1)


class FfNomenclatureOption(BaseModel):
    """Кандидат номенклатуры для ручной привязки короба (поиск по артикулу/ШК)."""

    id: int
    barcode: str
    article_seller: str | None = None
    subject: str | None = None


class FfGuidBarcodePayload(BaseModel):
    """Ручной ШК для товара ФФ без штрихкода в карточке (короб ITF14 / россыпь EAN13)."""

    barcode: str = Field(min_length=8, max_length=100)
    note: str | None = Field(default=None, max_length=300)


class FfGuidBarcodeRow(BaseModel):
    """Ручная привязка ШК к product_guid (overlay поверх карточки/кэша)."""

    model_config = ConfigDict(from_attributes=True)

    product_guid: str
    barcode: str
    note: str | None = None


# ─── Requests ────────────────────────────────────────────────────────────────


class FfRequestRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    external_id: str
    number: str | None = None
    kind: str  # assembly | inbound | return | other
    type_name: str | None = None
    status: str | None = None
    stage_code: str | None = None
    stage_title: str | None = None
    is_completed: bool = False
    archived: bool = False
    expired: bool = False
    # Нормализованный статус ФФ для колонки «Статус ФФ» (см. _ff_status_code):
    # assembling | ready | shipped | expected | accepted | archived | expired
    ff_status: str = "assembling"
    local_archived: bool = False  # локальный архив DDS (синк не трогает)
    local_archived_at: datetime | None = None
    total_qty: int | None = None  # заявлено всего, шт (skladbot — из деталки)
    #: Кол-во в штуках россыпи (пересчёт коробов ×units_per_box; migfull
    #: сборка/возврат). None — коробов нет или состав не разрезолвлен.
    total_qty_units: int | None = None
    #: Сколько КОРОБОВ в составе (Σ qty строк с кратностью >1; migfull) — UI
    #: разделяет «что в штуках, что в коробах». None — коробов нет.
    total_boxes: int | None = None
    #: Принято фактически (живые received-строки приёмки migfull, Σ шт) —
    #: прогресс «принято X из Y» у приёмок в обработке. None — нет данных.
    accepted_qty: int | None = None
    dest_warehouse: str | None = None  # склад отгрузки МП («Склад МП» / shipped_target)
    external_created_at: date | None = None
    synced_at: datetime
    assembly_request_id: int | None = None
    inbound_receipt_id: int | None = None
    # Приёмка внутреннего перемещения: товар приехал с нашего же склада, и
    # `inbound_receipts` для такого переезда не существует.
    stock_transfer_id: int | None = None
    # Обогащение по связанному документу (заполняет сервис)
    linked_number: str | None = None
    linked_status: str | None = None
    # Состав нашего документа расходится с привязанной заявкой(ами) ФФ по наполнению
    # (True — расхождение, False — совпадает, None — определить нельзя). См.
    # compute_doc_ff_mismatch: сверка по ШК (wmscelicom/migfull) либо по кол-ву (skladbot).
    linked_mismatch: bool | None = None
    # ВСКРЫТИЕ КОРОБОВ (пара «возврат коробов ↔ поступление россыпью», Натали).
    # У ПОСТУПЛЕНИЯ — id/номер возврата-пары; у ВОЗВРАТА — id/номер поступления
    # (заполняет сервис зеркально). Помеченная пара — внутренняя переупаковка ФФ:
    # сток не двигается, из резерва «в приёмке» поступление исключено.
    repack_return_id: int | None = None
    repack_pair_number: str | None = None
    # kind=return без пары: возможно, РЕАЛЬНЫЙ возврат товара — подсветка в UI.
    repack_unpaired: bool = False


class FfRequestDetailProduct(BaseModel):
    """Позиция заявки ФФ (из недокументированного GET /v1/requests/show/{id})."""

    barcode: str | None = None
    product_guid: str | None = None  # guid товара у ФФ (migfull) — для ручной привязки ШК
    vendor_code: str | None = None
    name: str | None = None
    nomenclature_id: int | None = None
    article_seller: str | None = None  # наш артикул (если товар сматчен)
    qty: int = 0  # заявлено (amount); для короба — уже в штуках россыпи (×units_per_box)
    accepted_qty: int = 0  # принято (acceptedAmount)
    delivery_qty: int = 0  # отгружено (delivery_amount)
    defect_qty: int = 0  # брак (repairAmount)
    units_per_box: int = 1  # штук россыпи в коробе (1 — позиция россыпью)
    box_qty: int = 0  # сколько коробов (если позиция коробом), иначе 0
    our_qty: int | None = None  # кол-во в связанном нашем документе; None — связи нет
    color: str | None = None
    size: str | None = None
    comment: str | None = None
    image: str | None = None


class FfMatchRow(BaseModel):
    """Строка расхождения состава: ФФ-заявка vs наш документ (по barcode)."""

    barcode: str
    article_seller: str | None = None
    name: str | None = None  # название со стороны ФФ (если позиция там есть)
    ff_qty: int = 0
    our_qty: int = 0
    diff: int = 0  # ff_qty - our_qty


class FfRepackCandidate(BaseModel):
    """Кандидат-поступление для РУЧНОЙ связки пары «вскрытие коробов».

    Авто-матчер помечает пару только при ТОЧНОМ равенстве состава; на живых
    вскрытиях ФФ часто пересчитывает фактически (кейс PVB-0000068↔124:
    пересечение 98.4%, 10 позиций из 90 разошлись) — тогда решает человек,
    а цифры ниже дают ему основание.
    """

    id: int
    number: str | None = None
    external_created_at: date | None = None
    status: str | None = None
    #: Σ штук россыпи по составу поступления (для сравнения с возвратом).
    units_sum: int = 0
    #: Пересечение состава с возвратом, % от большей стороны (0–100).
    overlap_pct: float = 0
    #: Состав совпал точно — такой кандидат авто-матчер пометил бы сам.
    exact: bool = False


class FfRepackCandidatesOut(BaseModel):
    """GET /requests/{id}/repack-candidates — кандидаты пары для возврата."""

    return_id: int
    return_number: str | None = None
    #: Σ штук россыпи возврата (короба × кратность + россыпь); None — состав
    #: не разрешился в ШК (нет карты кратности).
    return_units: int | None = None
    candidates: list[FfRepackCandidate] = Field(default_factory=list)


class FfRepackLinkIn(BaseModel):
    """POST /requests/{id}/repack-link — связать возврат с поступлением-парой."""

    submission_id: int


class FfRequestMatch(BaseModel):
    """Итог сверки состава ФФ-заявки со связанным нашим документом."""

    matched: bool
    ff_positions: int = 0
    our_positions: int = 0
    ff_total: int = 0
    our_total: int = 0
    mismatches: list[FfMatchRow] = Field(default_factory=list)


class FfMismatchDetailRow(BaseModel):
    """Расходящаяся позиция: наш qty vs суммарный qty привязанных заявок ФФ."""

    barcode: str
    article_seller: str | None = None
    our_qty: int = 0
    ff_qty: int = 0
    diff: int = 0  # ff_qty - our_qty (>0 — ФФ заявил больше, <0 — у нас больше)


class FfMismatchDetail(BaseModel):
    """Разбивка расхождения наполнения сборки с привязанными заявками ФФ (модалка)."""

    assembly_id: int
    assembly_number: str | None = None
    # barcode — сверка по ШК (rows заполнены); total — состав ФФ по позициям недоступен (только итоги)
    mode: Literal["barcode", "total"]
    our_total: int = 0
    ff_total: int = 0
    ff_request_numbers: list[str] = Field(default_factory=list)
    # rows — расхождение по НАШИМ ШК (наш qty ≠ qty у ФФ, включая «мы отправили, а в
    # заявке нет»). extra_rows — ШК, которые есть только у ФФ (мы их не отправляли):
    # инфо-строки, расхождением не считаются (не зажигают ⚠ и бейдж «расхождение»).
    rows: list[FfMismatchDetailRow] = Field(default_factory=list)
    extra_rows: list[FfMismatchDetailRow] = Field(default_factory=list)


class FfSiblingRequest(BaseModel):
    """«Сестра» заявки ФФ по мульти-связке (N заявок → один наш документ).

    migfull раскладывает одну машину на несколько PVB (штучная + коробовая),
    привязанных к одному InboundReceipt. Деталка показывает группу бейджами и
    строит сверку по СУММЕ составов группы.
    """

    id: int
    number: str | None = None
    kind: str  # assembly | inbound | return | other
    total_qty: int | None = None  # заявлено всего, шт (зеркало БД)


class FfRequestStageLog(BaseModel):
    stage: str | None = None
    executor: str | None = None
    created_at: str | None = None  # формат провайдера «10.06.2026 17:53:35», отдаём как есть
    spent_time: str | None = None


class FfRequestFieldValue(BaseModel):
    """Динамическое поле заявки (Маркетплейс, Склад МП, Дата забора, ...)."""

    name: str | None = None
    field: str | None = None
    value: str | None = None


class FfRequestDetail(FfRequestRow):
    """Деталка заявки ФФ: шапка списочной строки + живой состав от провайдера."""

    comment: str | None = None
    customer_name: str | None = None
    executor: str | None = None
    creator: str | None = None
    stage_description: str | None = None
    total_qty: int = 0
    total_accepted: int = 0
    products: list[FfRequestDetailProduct] = Field(default_factory=list)
    stage_logs: list[FfRequestStageLog] = Field(default_factory=list)
    fields: list[FfRequestFieldValue] = Field(default_factory=list)
    # Сверка состава со связанным нашим документом (None — связи нет)
    match: FfRequestMatch | None = None
    # Мульти-связка: другие активные заявки ФФ, привязанные к тому же документу
    # (без текущей). Пусто — заявка одиночная.
    sibling_requests: list[FfSiblingRequest] = Field(default_factory=list)
    # Сверка `match` построена по СУММЕ составов всей группы: номера всех заявок
    # группы (включая текущую). None — сверка обычная (одна заявка vs документ).
    mismatch_group_numbers: list[str] | None = None


class FfLinkPayload(BaseModel):
    """Ровно один id — какой именно, проверяет сервис (`link_request`)."""

    assembly_request_id: int | None = None
    inbound_receipt_id: int | None = None
    stock_transfer_id: int | None = None


class FfLinkCandidate(BaseModel):
    """Кандидат для связывания ФФ-заявки с нашим документом (модал «Связать»).

    kind=assembly → заявка на сборку; kind=inbound → приёмка ИЛИ входящее
    перемещение (товар мог приехать с нашего же склада).
    score/reason заполнены, когда эвристика считает кандидата похожим
    (дата ± дни, пересечение ШК состава, близость количества).

    🔴 `doc_id` уникален только внутри своего `doc_kind`: приёмка №7 и
    перемещение №7 существуют одновременно.
    """

    doc_id: int
    #: assembly | inbound | transfer — в какой слот связи уедет этот кандидат.
    doc_kind: str = "inbound"
    number: str
    status: str
    created_at: datetime | None = None
    total_qty: int = 0  # суммарное кол-во позиций документа, шт
    fbo_supply_number: str | None = None  # ФБО-поставка WB (assembly)
    dest_warehouse: str | None = None  # склад назначения WB (assembly)
    score: int | None = None  # 0..100, None — эвристика кандидата не выделила
    reason: str | None = None  # «дата совпадает, ШК 80%»
    # Склад сдачи кандидата совпал со складом сдачи ФФ-заявки (нормализованное
    # сравнение имён). True, когда у ФФ-заявки склад неизвестен (фильтровать нечем).
    warehouse_match: bool = True
    # Сколько ДРУГИХ ФФ-заявок уже связано с этим документом. >0 только для
    # migfull/«Натали» (N:1 — одной сборке соответствуют 2+ заявки склада); для
    # остальных провайдеров связанные документы из кандидатов исключаются → всегда 0.
    linked_ff_count: int = 0


class FfLinkCandidatesResponse(BaseModel):
    kind: str  # assembly | inbound
    ff_number: str | None = None
    ff_total_qty: int | None = None
    ff_dest_warehouse: str | None = None  # склад сдачи самой ФФ-заявки (для фильтра по складу)
    # Состав ФФ-заявки удалось получить → скоринг учитывает пересечение ШК
    composition_available: bool = False
    candidates: list[FfLinkCandidate] = Field(default_factory=list)


class FfCreateAssemblyResult(BaseModel):
    """Итог создания нашей заявки на сборку из ФФ-заявки (kind=assembly)."""

    request: FfRequestRow  # обновлённая ФФ-заявка (уже связана)
    assembly_request_id: int
    assembly_number: str
    items_created: int = 0
    skipped_barcodes: list[str] = Field(default_factory=list)  # ШК без номенклатуры


# ─── Push: наша заявка на сборку → заявка ФФ (skladbot тип 851) ──────────────


class FfFormOption(BaseModel):
    """Опция select-поля формы создания заявки ФФ (skladbot form-data → utils)."""

    id: int  # value справочника — отправляется в fields.*.value
    name: str  # text для отображения в выпадающем списке


class FfDeliveryTypeOption(BaseModel):
    """Тип поставки: его value — строковый ключ (straight/cross_dock), не id."""

    value: str
    name: str


class FfCreateFormResponse(BaseModel):
    """Справочники для диалога создания заявки 851 (живой GET /v1/requests/form-data).

    Поля `marketplace` / `marketplace_warehouse` у skladbot — select по integer id,
    а НЕ по имени; поэтому диалог обязан выбирать из этих списков и слать id.
    Даты/тип/склад предзаполняются: `suggested_*` — лучшее совпадение по заявке.
    """

    marketplace_id: int  # выбранный по умолчанию маркетплейс (Wildberries)
    marketplace_name: str
    warehouses: list[FfFormOption]  # склады МП для marketplace_id (активные, видимые клиенту)
    delivery_types: list[FfDeliveryTypeOption]
    suggested_warehouse_id: int | None = None  # совпадение со складом WB заявки, иначе None
    suggested_warehouse_hint: str | None = None  # склад WB заявки (для подсказки в UI)
    collection_date: date
    unloading_date: date
    delivery_type: str = "straight"


class FfCreateRequestPayload(BaseModel):
    """Параметры создания заявки ФФ из нашей сборки (provider-agnostic).

    skladbot («Доставка на склад МП», 851): склад МП, даты забора/выгрузки и тип
    поставки выбираются в диалоге из `GET .../assembly/{id}/create-form` (skladbot
    принимает integer id из form-data, не имя) — поэтому для skladbot поля склада
    и дат ОБЯЗАТЕЛЬНЫ (валидируются в сервисе). wmscelicom («Целиком»): отгрузка
    создаётся самовывозом (delivery=2, fbo=1), склад WB берётся из WB-привязки на
    стороне «Целиком» — этих полей нет, шлём только comment/notify.
    Состав в обоих случаях берётся из позиций сборки автоматически.
    """

    marketplace_warehouse_id: int | None = Field(None, gt=0)  # skladbot: id склада МП (form-data); wms — не нужен
    collection_date: date | None = None  # skladbot: дата забора груза
    unloading_date: date | None = None  # skladbot: дата выгрузки на склад МП
    marketplace_id: int = Field(1, gt=0)  # skladbot: id маркетплейса (Wildberries=1)
    delivery_type: Literal["straight", "cross_dock"] = "straight"  # skladbot: прямая / транзит
    comment: str | None = Field(None, max_length=1000)
    notify: bool = False  # уведомление на стороне ФФ (skladbot)


class FfPushAssemblyResult(BaseModel):
    """Итог отправки нашей заявки на сборку в ФФ (создан реальный заказ у skladbot)."""

    request: FfRequestRow  # зеркало созданной ФФ-заявки (уже связано со сборкой)
    external_id: str  # id заявки у ФФ
    ff_number: str | None = None  # WH-R-...
    items_sent: int = 0  # позиций отправлено
    total_qty: int = 0  # суммарно штук отправлено
    skipped_barcodes: list[str] = Field(default_factory=list)  # ШК без остатка/карточки у ФФ


class FfDeficitItem(BaseModel):
    """ШК, по которому доступного у ФФ остатка меньше, чем нужно по сборке."""

    barcode: str
    needed: int  # нужно по заявке на сборку
    available: int  # доступно у ФФ под этот тип заявки (0 — карточки/остатка нет)


# ─── Массовое создание заявок ФФ из нескольких сборок (bulk push) ────────────


class FfBulkCreateRequestPayload(BaseModel):
    """Параметры массового создания заявок ФФ из выбранных заявок на сборку.

    Склад МП и дата выгрузки подбираются ПО КАЖДОЙ сборке автоматически (склад
    МП — по её складу WB, дата выгрузки = дата сдачи её поставки FBW), поэтому в
    payload их нет. Общие на весь батч: тип поставки, дата забора и комментарий.
    """

    assembly_request_ids: list[int] = Field(min_length=1, max_length=50)
    collection_date: date  # дата забора груза — общая для всех сборок батча
    marketplace_id: int = Field(1, gt=0)  # id маркетплейса (Wildberries=1)
    delivery_type: Literal["straight", "cross_dock"] = "straight"
    comment: str | None = Field(None, max_length=1000)
    notify: bool = False


class FfBulkCreateAssemblyResult(BaseModel):
    """Итог push одной сборки в батче (создано / пропущено с причиной)."""

    assembly_request_id: int
    assembly_number: str
    # created — заявка создана; deficit — нехватка остатков у ФФ; no_warehouse —
    # склад МП не подобран; already_linked — уже связана; empty — нет позиций;
    # error — ошибка провайдера/сети (текст в message)
    status: Literal["created", "deficit", "no_warehouse", "already_linked", "empty", "error"]
    ff_number: str | None = None
    external_id: str | None = None
    items_sent: int = 0
    total_qty: int = 0
    dest_warehouse: str | None = None  # подобранный склад МП (для созданных)
    deficit: list[FfDeficitItem] = Field(default_factory=list)
    message: str | None = None  # человекочитаемая причина для не-created статусов


class FfBulkCreateResult(BaseModel):
    results: list[FfBulkCreateAssemblyResult] = Field(default_factory=list)
    created_count: int = 0
    failed_count: int = 0  # всё, что не created


# ─── Массовый локальный архив заявок ФФ ──────────────────────────────────────


class FfBulkArchivePayload(BaseModel):
    ff_request_ids: list[int] = Field(min_length=1, max_length=500)
    archived: bool = True  # True — в архив, False — вернуть из архива


class FfBulkArchiveResult(BaseModel):
    updated: int = 0  # сколько строк реально сменили признак


# ─── История смены статусов заявок ФФ ──────────────────────────────────────


class FfStatusEvent(BaseModel):
    """Событие истории: синк зафиксировал смену стадии/статуса заявки ФФ."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    fulfillment_request_id: int
    external_id: str
    number: str | None = None
    kind: str  # assembly | inbound | other
    provider: str
    event_type: str  # created | changed
    old_status: str | None = None
    new_status: str | None = None
    old_stage_code: str | None = None
    new_stage_code: str | None = None
    old_stage_title: str | None = None
    new_stage_title: str | None = None
    old_is_completed: bool | None = None
    new_is_completed: bool | None = None
    old_archived: bool | None = None
    new_archived: bool | None = None
    changed_at: datetime
    # Обогащение из текущей заявки ФФ (для колонок истории)
    dest_warehouse: str | None = None  # склад сдачи (для сборки)
    total_qty: int | None = None  # заявленное кол-во
    linked_number: str | None = None  # наша заявка/приёмка, если сматчена


# ─── Сводная страница «Заявки ФФ» (все склады с интеграцией) ────────────────


class FfIntegratedWarehouse(BaseModel):
    warehouse_id: int
    warehouse_name: str
    provider: str  # skladbot | wmscelicom
    provider_label: str  # человекочитаемое имя провайдера
    last_sync_at: datetime | None = None
    requests_total: int = 0
    requests_unlinked: int = 0  # активные несвязанные заявки kind=assembly


class FfMatchSuggestion(BaseModel):
    """Кандидат авто-мэтчинга ФФ-заявки к нашей заявке на сборку."""

    assembly_request_id: int
    number: str
    status: str  # AssemblyStatus
    created_at: datetime
    total_qty: int = 0
    score: int  # 0..100 — уверенность эвристики
    reason: str  # объяснение: «дата ±1 дн», «ШК 80%»


class FfOverviewRequestRow(FfRequestRow):
    warehouse_id: int
    warehouse_name: str
    provider: str
    # топ-кандидаты для несвязанных активных заявок (иначе [])
    suggestions: list[FfMatchSuggestion] = Field(default_factory=list)


class FfOverviewResponse(BaseModel):
    warehouses: list[FfIntegratedWarehouse]
    requests: list[FfOverviewRequestRow]


# ─── Несвязанные наши заявки на сборку (обратный линк ФФ → ASM) ──────────────


class FfUnlinkedAssembly(BaseModel):
    """Наша заявка на сборку без связанной заявки ФФ (для модалки обратного линка).

    Активные сборки склада (IN_PROGRESS/READY/VEHICLE_ASSIGNED), которым ещё не
    сопоставлена ни одна ФФ-заявка зеркала.
    """

    id: int
    number: str
    status: str  # AssemblyStatus
    brands: str | None = None  # бренды позиций через запятую (или None)
    total_qty: int = 0  # суммарное кол-во позиций, шт
    dest_warehouse: str | None = None  # склад сдачи МП (FBO warehouse_name или ручной)
    estimated_ready_date: date | None = None
    created_at: datetime


# ─── Sync ────────────────────────────────────────────────────────────────────


class FfSyncResult(BaseModel):
    stocks_synced: int = 0
    requests_synced: int = 0
    unmatched_barcodes: int = 0
    assemblies_marked_ready: int = 0  # наших заявок переведено в READY по стадии ФФ
    inbound_receipts_accepted: int = 0  # наших приёмок принято (сток запостен) по сигналу ФФ
    assemblies_shipped: int = 0  # наших VEHICLE_ASSIGNED сборок отгружено (сток списан) по сигналу ФФ
    synced_at: datetime


class FfSyncRun(BaseModel):
    """Один прогон синхронизации ФФ-склада (строка журнала sync_log).

    Питает вкладку «ФФ синхронизация» — видно, когда были последние обновления
    зеркала (авто-синк по расписанию + ручной «Синхронизировать сейчас»).
    """

    id: int
    service: str  # skladbot | wmscelicom | migfull
    status: str  # RUNNING | OK | ERROR
    started_at: datetime
    finished_at: datetime | None = None
    stocks_synced: int = 0  # позиций остатков (rows_inserted)
    requests_synced: int = 0  # заявок (rows_fetched − rows_inserted)
    duration_seconds: float | None = None
    error_msg: str | None = None
