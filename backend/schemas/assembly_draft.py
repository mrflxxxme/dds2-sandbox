# ruff: noqa: RUF001, RUF002, RUF003
"""
Pydantic schemas for AssemblyDraft.

Used by the distribution UI to plan an NxM split (RF source warehouses x
WB target warehouses) before committing it as N AssemblyRequests.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PackageTypeStr = Literal["BOX", "MONOPALLET", "SUPERSAFE"]


class AssemblyDraftRow(BaseModel):
    """One article in the distribution matrix."""

    nm_id: int
    barcode: str = ""
    vendor_code: str = ""
    src: dict[str, int] = Field(default_factory=dict)  # warehouse_id (str) -> qty
    tgt: dict[str, int] = Field(default_factory=dict)  # wb_warehouse_name -> qty
    # Per-SKU acceptance package type (set by /warehouse/acceptance-check).
    # Drives grouping into AssemblyRequests on commit_draft —
    # one request per (source_ff, target_wb, package_type).
    package_type: PackageTypeStr = "BOX"
    # Сознательно отгруженная ЧАСТИЧНАЯ паллета («Оставить так» в предброни).
    # Такие строки нормализатор фронта НЕ трогает (не срезает до целых паллет);
    # всё непомеченное приводится к инварианту «целые коробы + целые паллеты»
    # при загрузке страницы (self-heal легаси-недобора).
    as_is: bool = False


class HandedUnitItem(BaseModel):
    """Позиция замороженной заявки-юнита (передан на ФФ)."""

    nm_id: int
    barcode: str
    vendor_code: str = ""
    qty: int


class HandedUnit(BaseModel):
    """Заявка-юнит (source_ff × target_wb × упаковка), вырезанная из черновика и
    переданная на ФФ. Снимок заморожен: правки распределения его не трогают (его
    уже нет в rows). `в сборке` создаёт из него AssemblyRequest. Новинки и обычные
    товары на один склад живут в ОДНОМ юните — признак новинки derived из
    nomenclature на уровне товара, а не оси группировки."""

    source_ff_id: int
    target_wb_name: str
    package_type: PackageTypeStr = "BOX"
    status: str = "handed"  # "handed" (передан на ФФ) | "draft" (ручной черновик)
    items: list[HandedUnitItem] = Field(default_factory=list)


class AssemblyDraftDistribution(BaseModel):
    """Full distribution payload stored in AssemblyDraft.distribution JSONB."""

    source_warehouse_ids: list[int] = Field(default_factory=list)
    target_warehouse_names: list[str] = Field(default_factory=list)
    rows: list[AssemblyDraftRow] = Field(default_factory=list)
    pallets_count: int = 1
    pallet_weight_kg: float = 0.0
    estimated_ready_date: str | None = None  # YYYY-MM-DD
    # Cold-start доли по WB-складам (warehouse_name → доля 0..1, не проценты).
    # Если задано — Авто-баланс на странице distribute распределяет qty
    # пропорционально этим долям (вместо wbNeed). None для обычных сборок.
    cold_start_shares: dict[str, float] | None = None
    # Замороженные заявки-юниты, переданные на ФФ (вырезаны из rows).
    handed_units: list[HandedUnit] = Field(default_factory=list)
    # Предбронь: целые коробы, НЕ собравшиеся в целую паллету при «Заполнить черновик»
    # (под-паллетный хвост). Не теряются на ФФ — показываются отдельным списком с
    # действиями (удалить / дозабить паллету из свободного ФФ / оставить). Пересчитывается
    # при каждом заполнении из потребности. Та же строчная структура, что и rows.
    prebook: list[AssemblyDraftRow] = Field(default_factory=list)
    # Провенанс «из предброни»: ключи `${nm_id}::${wb_warehouse_name}`, чей контент попал
    # в rows ИЗ предброни (через «Оставить так»/«Дозабить»/авто-консолидацию). Чисто для
    # показа — бейдж «из предброни» на паллете раскладки. Нормализатор его не трогает
    # (живёт отдельно от rows). Сбрасывается при полном «Заполнить из потребности».
    prebook_origin: list[str] = Field(default_factory=list)
    # РУЧНЫЕ SKU (nm_id): план правлен юзером в матрице-редакторе (степпер/✕) —
    # авто-синк расчёта от потребности такие SKU НЕ трогает (ручное решение
    # священно), пока юзер не вернёт SKU «в авто». JSONB — миграция не нужна.
    manual_nms: list[int] = Field(default_factory=list)
    # КАТЕГОРИЙНЫЙ СКОУП черновика: список «эффективных категорий»
    # (CategoryOverride ?? Nomenclature.subject). None/[] = обычный черновик без
    # ограничений. Скоупленный черновик заполняется/распределяется только по этим
    # категориям; его содержимое резервирует сток от других черновиков
    # (GET /assembly/drafts/reserved). JSONB — миграция не нужна.
    category_scope: list[str] | None = None
    # Ограничение ФФ-источника скоупленного черновика (id склада-фулфилмента):
    # раскладка берёт товар только с этого ФФ. None = все ФФ.
    scope_ff_id: int | None = None

    @field_validator(
        "rows", "source_warehouse_ids", "target_warehouse_names", "handed_units", "prebook", "prebook_origin", "manual_nms", mode="before"
    )
    @classmethod
    def coerce_null_to_empty_list(cls, v: object) -> object:
        """Guard against explicit null in stored JSONB (null → [])."""
        return v if v is not None else []


class AssemblyDraftCreate(BaseModel):
    name: str = "Черновик сборки"
    distribution: AssemblyDraftDistribution
    comment: str | None = None


class DraftEventLog(BaseModel):
    """Маркер события истории для логирования при PUT черновика.

    Клиент помечает «значимый» PUT (дозабор хвоста предброни / запись раскладки из
    матрицы / правка степпером в редакторе), сервер снапшотит СТАРЫЙ distribution в
    `AssemblyDraftEvent` для отката. PUT без события не логируется. COMMIT логируется
    отдельно в `commit_draft` (не через этот путь)."""

    event_type: Literal["PREBOOK_TOPUP", "MATRIX_WRITE", "MATRIX_EDIT", "AUTO_SYNC", "ACCEPTANCE_SYNC"]
    summary: str | None = None


class AssemblyDraftUpdate(BaseModel):
    name: str | None = None
    distribution: AssemblyDraftDistribution | None = None
    comment: str | None = None
    # Опц. маркер: если задан — этот PUT логируется в историю с before-снапшотом.
    event: DraftEventLog | None = None
    # Оптимистическая версия (CAS): updated_at черновика, от которого клиент строил
    # distribution. Не совпала с БД → 409 DRAFT_VERSION_CONFLICT вместо молчаливого
    # full-replace (прод-кейс: фоновый синк stale-вкладки воскресил очищенный
    # черновик через 9с). Шлют ФОНОВЫЕ писатели (автосейв/консолидация/self-heal);
    # явные действия юзера идут без версии — последняя воля побеждает.
    base_updated_at: datetime | None = None


class AssemblyDraftAddRows(BaseModel):
    """Request body for POST /assembly/drafts/{id}/rows.

    Merge a batch of rows INTO an existing draft, preserving handed_units and
    manual edits. Rows with matching (nm_id, package_type) are summed
    element-wise (src per ff-id-string, tgt per wb-name); new keys appended.
    """

    rows: list[AssemblyDraftRow] = Field(default_factory=list)


class AssemblyDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    distribution: AssemblyDraftDistribution
    comment: str | None
    created_at: datetime
    updated_at: datetime
    # SKU-новинки в этом draft (Nomenclature.first_sale_date IS NULL OR ≥ today-14d).
    # Заполняется роутером при возврате; в БД не хранится — derived из nomenclature.
    # UI использует чтобы (a) показать бейдж 🆕 в матрице, (b) посчитать сколько
    # отдельных заявок будет создано (новинки идут отдельно от обычных).
    newcomer_nm_ids: list[int] = Field(default_factory=list)


class DraftsReservedResponse(BaseModel):
    """Резерв стока черновиками: barcode → {ff_warehouse_id → qty}.

    Σ rows.src + prebook.src + handed_units.items всех не-удалённых черновиков
    проекта (кроме exclude_draft_id). Фронт вычитает из доступного ФФ, чтобы
    параллельные черновики (в т.ч. категорийные) не планировали один товар дважды."""

    reserved: dict[str, dict[str, int]] = Field(default_factory=dict)


class CommitSupply(BaseModel):
    """Явная отгрузка ФФ→склад: набор баркодов с количествами на ОДНУ заявку.

    Когда фронт передаёт `supplies`, commit строит заявки ИЗ НИХ напрямую, минуя
    pro-rata `_allocate_pairs`. Нужно для режима «только целые паллеты»: целые паллеты
    на каждую отгрузку ФФ→склад нельзя выразить суммами строк (строка хранит Σ по ФФ и
    Σ по складам раздельно, связку решает аллокатор), поэтому фронт считает конкретную
    раскладку и присылает её. Бэкенд валидирует, что отгрузка не превышает черновик."""

    source_ff_id: int
    target_wb_name: str
    package_type: str = "BOX"
    items: dict[str, int]  # barcode -> qty


class CommitDraftOptions(BaseModel):
    """Опции коммита черновика. `pallet_counts` — map `"{ff_id}::{wb_name}::{pkg}" →
    паллет`: число паллет, авто-проставляемое в каждую создаваемую заявку (считается
    на фронте из габаритов коробки). Ключ отсутствует → берётся плоский
    `distribution.pallets_count` (старое поведение).

    `supplies` (опц.) — явные отгрузки ФФ→склад (режим «только целые паллеты»). Если
    переданы — заявки создаются ровно из них (минуя pro-rata); неполные отгрузки фронт
    уже убрал. Σ по баркоду не может превышать черновик (валидация на бэке)."""

    pallet_counts: dict[str, int] | None = None
    supplies: list[CommitSupply] | None = None


class AssemblyDraftCommitResponse(BaseModel):
    """Returned after a draft is committed into N AssemblyRequests."""

    created_request_ids: list[int]
    draft_id: int


class DraftEventRead(BaseModel):
    """Одно событие истории черновика (вкладка «🕘 История»).

    `can_revert` / `revert_blocked_reason` вычисляет сервис (гварды ФФ/WB/версия),
    поэтому НЕ from_attributes — строится явно."""

    id: int
    event_type: str
    summary: str | None = None
    created_at: datetime
    created_by: str | None = None
    reverted_at: datetime | None = None
    reverted_by: str | None = None
    # Для COMMIT: сколько заявок создано (показ в UI).
    created_request_ids: list[int] | None = None
    # Можно ли откатить прямо сейчас + причина запрета (если нельзя).
    can_revert: bool = False
    revert_blocked_reason: str | None = None


class DraftHistoryResponse(BaseModel):
    """Ответ GET /assembly/drafts/{id}/history — события новейшие первыми."""

    events: list[DraftEventRead] = Field(default_factory=list)


class DraftEventRevertResponse(BaseModel):
    """Ответ POST /assembly/drafts/{id}/history/{event_id}/revert."""

    reverted_event_id: int
    event_type: str
    # Что произошло:
    restored_draft: bool = False  # черновик был восстановлен/обновлён снапшотом
    deleted_request_ids: list[int] = Field(default_factory=list)  # отменённые+удалённые заявки
    draft: AssemblyDraftRead | None = None  # обновлённый черновик после отката


class AssemblyDraftUnitRef(BaseModel):
    """Ссылка на заявку-юнит черновика (для hand-off / revert / commit). Ключ —
    (source_ff × target_wb × package_type); новинки и обычные не разделяются."""

    source_ff_id: int
    target_wb_name: str
    package_type: PackageTypeStr = "BOX"


class AssemblyDraftUnitEdit(AssemblyDraftUnitRef):
    """Замена наполнения заявки-юнита (ручная правка черновика)."""

    items: list[HandedUnitItem] = Field(default_factory=list)


class AssemblyDraftUnitMove(AssemblyDraftUnitRef):
    """Перенос заявки-юнита на другой WB-склад (только для этого ФФ)."""

    new_target_wb_name: str


class AssemblyDraftMergeRequest(BaseModel):
    """Request body for POST /assembly/drafts/merge.

    Merges N drafts into one: rows with matching (nm_id, package_type) are
    summed element-wise; source_warehouse_ids and target_warehouse_names are
    unioned. cold_start_shares is dropped (user re-runs auto-balance).
    """

    draft_ids: list[int] = Field(
        ...,
        min_length=2,
        description="IDs of drafts to merge (≥2 distinct values required)",
    )

    @field_validator("draft_ids")
    @classmethod
    def dedup_and_validate(cls, v: list[int]) -> list[int]:
        """Dedup while preserving order; ensure ≥2 distinct ids remain."""
        seen: set[int] = set()
        result: list[int] = []
        for x in v:
            if x not in seen:
                seen.add(x)
                result.append(x)
        if len(result) < 2:
            raise ValueError("draft_ids must contain at least 2 distinct IDs")
        return result


# ─── Прогноз загрузки складов / локализации (вкладка «Прогноз») ──────────────


class ForecastLeadTime(BaseModel):
    """Среднее время до появления поставки на WB-складе (дни)."""

    assembly_ship_days: float  # создание заявки → отгрузка (сборка + отправка)
    delivery_days: float  # отгрузка → приёмка WB (SHIPPED → DELIVERED)
    total_days: float  # сумма = lead-time
    has_history: bool  # были ли данные истории (иначе использованы дефолты)


class ForecastItem(BaseModel):
    """Прогноз по одному (WB-склад × SKU)."""

    warehouse_name: str
    district: str
    district_label: str
    nm_id: int
    vendor_code: str = ""
    current_stock: int  # текущий остаток на WB-складе
    incoming: int  # входящая поставка из черновика
    avg_daily: float  # скорость продаж на этом складе (шт/день, по доле спроса)
    projected_on_arrival: int  # остаток на момент прихода поставки (может быть < 0)
    days_cover: int  # дней покрытия после поставки ((остаток+входящая)/скорость)
    traffic_light: str  # red / orange / yellow / green


class ForecastWarehouse(BaseModel):
    """Сводка по одному WB-складу."""

    warehouse_name: str
    district: str
    district_label: str
    sku_count: int
    current_stock: int
    incoming: int
    traffic_light_counts: dict[str, int]  # {red,orange,yellow,green}


class ForecastLocalizationDistrict(BaseModel):
    """Разбивка локализации по федеральному округу (примерная оценка)."""

    district: str
    label: str
    demand: int  # спрос за горизонт (шт)
    avail_current: int  # покрытие текущим остатком (шт)
    avail_after: int  # покрытие после поставки (шт)
    local_pct_current: float  # % спроса, покрытого локально сейчас
    local_pct_after: float  # % спроса, покрытого локально после поставки


class ForecastLocalization(BaseModel):
    """Примерный индекс локализации до/после поставки черновика."""

    index_current: float  # ИЛ (КТР-взвеш.) сейчас — НИЖЕ = лучше
    index_after: float  # ИЛ после поставки
    avg_loc_pct_current: float  # средняя доля локализации сейчас (выше = лучше)
    avg_loc_pct_after: float  # средняя доля локализации после поставки
    status_current: str  # excellent / neutral / weak / critical
    status_after: str
    horizon_days: int  # горизонт спроса, на котором оценивали
    by_district: list[ForecastLocalizationDistrict]


class ForecastSkuLocalization(BaseModel):
    """Примерная доля локализации одного SKU до/после поставки (%)."""

    nm_id: int
    loc_pct_current: float
    loc_pct_after: float


class ForecastSummary(BaseModel):
    """Итоги по всему черновику."""

    sku_count: int
    warehouse_count: int
    total_incoming: int
    traffic_light_counts: dict[str, int]


class ForecastResponse(BaseModel):
    """Ответ /assembly/drafts/{id}/forecast — предпросмотр загрузки складов."""

    draft_id: int
    lead_time: ForecastLeadTime
    summary: ForecastSummary
    localization: ForecastLocalization
    sku_localization: list[ForecastSkuLocalization]  # per-SKU доля локализации до/после
    newcomer_nm_ids: list[int]  # SKU-новинки (для бейджа 🆕 в матрице)
    warehouses: list[ForecastWarehouse]
    items: list[ForecastItem]
    generated_at: datetime
