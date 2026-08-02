# ruff: noqa: RUF002, RUF003
"""
Design-tasks schemas — модуль «Дизайн карточек».

DesignTaskCreate — БЕЗ assignee (назначает только lead, дельта PRD v4) и БЕЗ
complexity (ставит lead). Срочность — булев is_urgent (Р9), enum приоритета нет.
sheet_url обязателен (Р13), description — min 10 символов (Р13).
"""

from datetime import date, datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    UrlConstraints,
    field_validator,
    model_validator,
)

from backend.models.design import (
    DESIGN_BOARD_STATUSES,
    DesignComplexity,
    DesignMaterialKind,
    DesignTaskStatus,
    DesignVerdict,
    DesignWorkType,
)

# HttpUrl с капом длины — колонки sheet_url/url в БД String(1000).
LimitedHttpUrl = Annotated[HttpUrl, UrlConstraints(max_length=1000)]

# nm_id/ref_nm_id — BigInteger в БД: положительный, в пределах int64.
NmId = Annotated[int, Field(ge=1, lt=2**63)]

ALLOWED_STATUSES = [s.value for s in DesignTaskStatus]
ALLOWED_BOARD_STATUSES = [s.value for s in DESIGN_BOARD_STATUSES]
ALLOWED_WORK_TYPES = [w.value for w in DesignWorkType]
ALLOWED_COMPLEXITIES = [c.value for c in DesignComplexity]
ALLOWED_MATERIAL_KINDS = [k.value for k in DesignMaterialKind]
ALLOWED_VERDICTS = [v.value for v in DesignVerdict]


# ─── Write models ─────────────────────────────────────────────────────────────


class DesignTaskCreate(BaseModel):
    """Создание заявки менеджером. Без assignee и complexity — их ставит lead."""

    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=10, max_length=5000)
    sheet_url: LimitedHttpUrl
    nm_id: NmId | None = None
    work_type: str = Field(default=DesignWorkType.FULL_SET.value)
    is_urgent: bool = False
    due_date: date | None = None

    @field_validator("work_type")
    @classmethod
    def _validate_work_type(cls, v: str) -> str:
        if v not in ALLOWED_WORK_TYPES:
            raise ValueError(f"work_type must be one of: {ALLOWED_WORK_TYPES}")
        return v


class DesignTaskUpdate(BaseModel):
    """PATCH задачи — всё опционально.

    complexity / is_outsourced применяются только для lead (проверка в сервисе, Ф1).
    """

    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, min_length=10, max_length=5000)
    sheet_url: LimitedHttpUrl | None = None
    nm_id: NmId | None = None
    work_type: str | None = None
    complexity: str | None = None
    is_urgent: bool | None = None
    due_date: date | None = None
    is_outsourced: bool | None = None

    @field_validator("work_type")
    @classmethod
    def _validate_work_type(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_WORK_TYPES:
            raise ValueError(f"work_type must be one of: {ALLOWED_WORK_TYPES}")
        return v

    @field_validator("complexity")
    @classmethod
    def _validate_complexity(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_COMPLEXITIES:
            raise ValueError(f"complexity must be one of: {ALLOWED_COMPLEXITIES}")
        return v


class DesignStatusChange(BaseModel):
    """Смена статуса из деталки (валидность перехода проверяет сервис по словарю)."""

    to_status: str
    comment: str | None = Field(None, max_length=2000)

    @field_validator("to_status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        if v not in ALLOWED_STATUSES:
            raise ValueError(f"to_status must be one of: {ALLOWED_STATUSES}")
        return v


class DesignMoveIn(BaseModel):
    """Перетаскивание на доске (Р4): целевая колонка + после какой карточки.

    Цель — только 6 колонок доски (ON_HOLD/CANCELLED — не через drag&drop).
    comment — опционален; dnd в «Правки» требует причину (гвард в сервисе, Ф1).
    """

    to_status: str
    after_task_id: int | None = None
    comment: str | None = Field(None, max_length=2000)

    @field_validator("to_status")
    @classmethod
    def _validate_status(cls, v: str) -> str:
        if v not in ALLOWED_BOARD_STATUSES:
            raise ValueError(f"to_status must be one of: {ALLOWED_BOARD_STATUSES}")
        return v


class DesignAssign(BaseModel):
    """Назначение исполнителя (только lead). null = снять исполнителя."""

    assignee_user_id: int | None = None


class DesignMaterialIn(BaseModel):
    """Добавление материала: LINK (url) или NM (ref_nm_id). FILE — отдельным upload-эндпоинтом."""

    kind: str
    url: LimitedHttpUrl | None = None
    ref_nm_id: NmId | None = None
    caption: str | None = Field(None, max_length=300)

    @field_validator("kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        if v not in ALLOWED_MATERIAL_KINDS:
            raise ValueError(f"kind must be one of: {ALLOWED_MATERIAL_KINDS}")
        return v

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> "DesignMaterialIn":
        if self.kind == DesignMaterialKind.LINK.value and not self.url:
            raise ValueError("kind=LINK требует url")
        if self.kind == DesignMaterialKind.NM.value and self.ref_nm_id is None:
            raise ValueError("kind=NM требует ref_nm_id")
        if self.kind == DesignMaterialKind.FILE.value:
            raise ValueError("kind=FILE добавляется загрузкой файла, не этим payload'ом")
        return self


class DesignCommentIn(BaseModel):
    """Комментарий к задаче (только текст; вложения — вне контракта Ф2)."""

    body: str = Field(min_length=1, max_length=2000)


class DesignVerdictIn(BaseModel):
    """Вердикт по версии сдачи. verdict_comment обязателен при REJECTED (гвард в сервисе)."""

    verdict: str
    verdict_comment: str | None = Field(None, max_length=2000)

    @field_validator("verdict")
    @classmethod
    def _validate_verdict(cls, v: str) -> str:
        allowed = [DesignVerdict.ACCEPTED.value, DesignVerdict.REJECTED.value]
        if v not in allowed:
            raise ValueError(f"verdict must be one of: {allowed}")
        return v


# ─── Read models ──────────────────────────────────────────────────────────────


class DesignTaskListItem(BaseModel):
    id: int
    number: str
    title: str
    status: str
    nm_id: int | None = None
    work_type: str
    complexity: str
    is_urgent: bool
    due_date: date | None = None
    is_overdue: bool = False
    is_outsourced: bool = False
    unviewed: bool = False  # Р5: NEW && viewed_by_lead_at IS NULL
    assignee_name: str | None = None
    author_name: str | None = None
    versions_count: int = 0
    sort_order: int = 0
    # Заполняется только в сквозном режиме GET /all-projects.
    project_name: str | None = None

    model_config = ConfigDict(from_attributes=True)


class DesignBoardResponse(BaseModel):
    """Доска одним ответом (Р4): 6 колонок + счётчики (вкл. ON_HOLD/CANCELLED для фильтра)."""

    columns: dict[str, list[DesignTaskListItem]]
    counts: dict[str, int]


class DesignTaskPermissions(BaseModel):
    """Булевы флаги действий — считает бэк (Ф1); фронт логику прав не дублирует.

    ПОЛНЫЙ набор флагов compute_permissions (инвариант §6.9): состав полей
    зеркалит ключи backend/services/design/permissions.py — паритет закреплён
    тестом test_permissions_schema_matches_service (Ф2).
    """

    can_edit: bool = False
    can_assign: bool = False
    can_take: bool = False
    can_change_status: bool = False
    can_move: bool = False
    can_hold: bool = False
    can_reorder: bool = False
    can_submit: bool = False
    can_verdict: bool = False
    can_comment: bool = False
    can_cancel: bool = False
    can_delete: bool = False
    can_set_complexity: bool = False
    can_set_outsource: bool = False
    can_create_ab_test: bool = False


class DesignMaterialOut(BaseModel):
    """Без minio_path (канон counterparty): скачивание — только через GET-ручки."""

    id: int
    kind: str
    original_filename: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    url: str | None = None
    ref_nm_id: int | None = None
    caption: str | None = None
    created_by_user_id: int | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DesignSubmissionFileOut(BaseModel):
    """Без minio_path: скачивание — GET /{task_id}/submissions/{sub_id}/files/{file_id}."""

    id: int
    original_filename: str | None = None
    mime_type: str | None = None
    file_size: int | None = None

    model_config = ConfigDict(from_attributes=True)


class DesignSubmissionOut(BaseModel):
    id: int
    version_no: int
    submitted_by_user_id: int
    submitted_at: datetime
    comment: str | None = None
    verdict: str
    verdict_comment: str | None = None
    verdict_by_user_id: int | None = None
    verdict_at: datetime | None = None
    files: list[DesignSubmissionFileOut] = []

    model_config = ConfigDict(from_attributes=True)


class DesignCommentOut(BaseModel):
    """Без minio_path (внутренний путь стора не выходит наружу, канон counterparty)."""

    id: int
    author_user_id: int
    author_name: str | None = None
    body: str
    original_filename: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DesignEventOut(BaseModel):
    id: int
    old_status: str | None = None
    new_status: str
    changed_at: datetime
    changed_by: str | None = None
    comment: str | None = None

    model_config = ConfigDict(from_attributes=True)


class DesignTaskDetail(BaseModel):
    """Деталка: шапка + материалы, сдачи (+файлы), комментарии, журнал, права."""

    id: int
    number: str
    title: str
    description: str
    sheet_url: str
    nm_id: int | None = None
    work_type: str
    complexity: str
    is_urgent: bool
    status: str
    sort_order: int
    due_date: date | None = None
    author_user_id: int
    author_name: str | None = None
    assignee_user_id: int | None = None
    assignee_name: str | None = None
    is_outsourced: bool
    viewed_by_lead_at: datetime | None = None
    held_from_status: str | None = None
    started_at: datetime | None = None
    accepted_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None

    materials: list[DesignMaterialOut] = []
    submissions: list[DesignSubmissionOut] = []
    comments: list[DesignCommentOut] = []
    events: list[DesignEventOut] = []
    permissions: DesignTaskPermissions = DesignTaskPermissions()

    model_config = ConfigDict(from_attributes=True)


class DesignProductSuggestion(BaseModel):
    """Автоподсказка товара (nm_id = Nomenclature.article_wb, НЕ vendor_code)."""

    nm_id: int
    article_seller: str | None = None
    brand: str | None = None
    subject: str | None = None


class DesignCalendarOut(BaseModel):
    """Календарь месяца (Р7): задачи по due_date в границах видимой сетки ±6 дней."""

    month: str  # YYYY-MM
    date_from: date
    date_to: date
    tasks: list[DesignTaskListItem] = []


class DesignWorkloadRow(BaseModel):
    """Строка экрана «Загрузка команды»."""

    user_id: int
    user_name: str
    active_tasks: int = 0
    overdue: int = 0
    in_revision: int = 0
    nearest_due: date | None = None


class DesignStatsOut(BaseModel):
    """Метрики PRD §10 (панель метрик, Ф6)."""

    on_time_share: float | None = None
    avg_versions_to_accept: float | None = None
    median_cycle_days: float | None = None
    unassigned_over_2d: int = 0
    outsourced_share: float | None = None
    tracked_share: float | None = None
