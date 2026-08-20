# ruff: noqa: RUF002, RUF003
"""
Design-tasks models — модуль «Дизайн карточек» (канбан задач на инфографику).

Задача = заявка на инфографику: название товара текстом (+опциональная привязка
к артикулу WB через nm_id, БЕЗ FK — товара может ещё не быть в справочнике, Р2),
обязательная ссылка на ТЗ-таблицу (Р13), суть, исходные материалы. Распределяет
задачи только ведущий дизайнер (owner/admin). Сдача — версиями с вердиктом.

Статусная модель (Р1): 8 статусов, хранение String(20), НЕ pg-enum; словарь
DESIGN_TASK_TRANSITIONS — единственный источник правды (зеркало
PAYMENT_REQUEST_TRANSITIONS). ACCEPTED/CANCELLED терминальны; возврат из
ON_HOLD — в статус из held_from_status (заполняет change_status, Ф1).

    NEW ──▶ ASSIGNED ──▶ IN_PROGRESS ──▶ REVIEW ──▶ ACCEPTED (терминал)
     ▲          │                          │  ▲
     └──────────┘ (снять исполнителя)      ▼  │
                                        REVISION
    ON_HOLD (вне доски) ⇄ NEW/ASSIGNED/IN_PROGRESS/REVISION
    CANCELLED (терминал, вне доски) ← из любого нетерминального

Срочность — булев is_urgent (Р9): подсветка-сигнал от менеджера, порядок в
колонке НЕ меняет (порядок — только sort_order от лида, шаг 1000, Р3).
Нумерация DES-N внутри проекта (Р14): partial-unique WHERE is_deleted = false
(индекс uq_design_tasks_project_number — в __table_args__ и в миграции dsn01).
Изоляция детей на уровне БД: составные FK (task_id, project_id) →
design_tasks(id, project_id) — чужой project_id падает IntegrityError.
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin
from backend.utils.time import utcnow

# ─── Enums (stored as String to avoid Alembic enum churn) ────────────────────


class DesignTaskStatus(str, enum.Enum):
    NEW = "NEW"                  # Новые заявки
    ASSIGNED = "ASSIGNED"        # Назначена
    IN_PROGRESS = "IN_PROGRESS"  # В работе
    REVIEW = "REVIEW"            # На проверке
    REVISION = "REVISION"        # Правки
    ON_HOLD = "ON_HOLD"          # Отложена (вне доски)
    ACCEPTED = "ACCEPTED"        # Принято (терминал)
    CANCELLED = "CANCELLED"      # Отменена (терминал, вне доски)


class DesignWorkType(str, enum.Enum):
    MAIN_PHOTO = "MAIN_PHOTO"  # главное фото
    FULL_SET = "FULL_SET"      # полный комплект (дефолт)
    EDIT = "EDIT"              # правка существующего
    RICH = "RICH"              # rich-контент
    VIDEO = "VIDEO"            # видео


class DesignComplexity(str, enum.Enum):
    S = "S"
    M = "M"
    L = "L"


class DesignMaterialKind(str, enum.Enum):
    FILE = "FILE"  # файл в MinIO
    LINK = "LINK"  # внешняя ссылка
    NM = "NM"      # артикул-пример (ref_nm_id)


class DesignVerdict(str, enum.Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"


# Срочность — булев флаг is_urgent на задаче (Р9), отдельного enum нет.


# Declarative state machine — single source of truth, mirrors PAYMENT_REQUEST_TRANSITIONS.
DESIGN_TASK_TRANSITIONS: dict[DesignTaskStatus, set[DesignTaskStatus]] = {
    DesignTaskStatus.NEW: {
        DesignTaskStatus.ASSIGNED,
        DesignTaskStatus.ON_HOLD,
        DesignTaskStatus.CANCELLED,
    },
    DesignTaskStatus.ASSIGNED: {
        DesignTaskStatus.IN_PROGRESS,
        DesignTaskStatus.NEW,  # снять исполнителя
        DesignTaskStatus.ON_HOLD,
        DesignTaskStatus.CANCELLED,
    },
    DesignTaskStatus.IN_PROGRESS: {
        DesignTaskStatus.REVIEW,
        DesignTaskStatus.ON_HOLD,
        DesignTaskStatus.CANCELLED,
    },
    DesignTaskStatus.REVIEW: {
        DesignTaskStatus.ACCEPTED,
        DesignTaskStatus.REVISION,
        DesignTaskStatus.CANCELLED,
    },
    DesignTaskStatus.REVISION: {
        DesignTaskStatus.REVIEW,
        DesignTaskStatus.ON_HOLD,
        DesignTaskStatus.CANCELLED,
    },
    DesignTaskStatus.ON_HOLD: {
        DesignTaskStatus.NEW,
        DesignTaskStatus.ASSIGNED,
        DesignTaskStatus.IN_PROGRESS,
        DesignTaskStatus.REVISION,
        DesignTaskStatus.CANCELLED,
    },
    DesignTaskStatus.ACCEPTED: set(),   # terminal
    DesignTaskStatus.CANCELLED: set(),  # terminal
}

# 6 колонок доски PRD §4 (ON_HOLD и CANCELLED — вне доски, доступны фильтром).
DESIGN_BOARD_STATUSES: list[DesignTaskStatus] = [
    DesignTaskStatus.NEW,
    DesignTaskStatus.ASSIGNED,
    DesignTaskStatus.IN_PROGRESS,
    DesignTaskStatus.REVIEW,
    DesignTaskStatus.REVISION,
    DesignTaskStatus.ACCEPTED,
]

# Рабочие статусы — для workload/просрочки (ON_HOLD и терминалы не считаются).
DESIGN_ACTIVE_STATUSES: set[DesignTaskStatus] = {
    DesignTaskStatus.NEW,
    DesignTaskStatus.ASSIGNED,
    DesignTaskStatus.IN_PROGRESS,
    DesignTaskStatus.REVIEW,
    DesignTaskStatus.REVISION,
}


# ─── DesignTask ──────────────────────────────────────────────────────────────


class DesignTask(Base, TimestampMixin, SoftDeleteMixin):
    """Задача на дизайн (заявка на инфографику)."""

    __tablename__ = "design_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    # DES-N внутри проекта (Р14): max+1 под lock; partial-unique в миграции dsn01.
    # Р18 (волна B v2): ведущий может задать свой номер свободным текстом до 40
    # символов — автогенерация DES-N остаётся дефолтом и живёт своей линейкой.
    number: Mapped[str] = mapped_column(String(40), nullable=False)
    # Название товара/задачи свободным текстом.
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    # 2–3 строки сути, min 10 симв. (Р13, валидация в схеме).
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # Ссылка на ТЗ-гугл-таблицу — обязательна (Р13).
    sheet_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    # = Nomenclature.article_wb при опознании; БЕЗ FK (Р2: товара может ещё не быть).
    nm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    work_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DesignWorkType.FULL_SET.value, server_default="FULL_SET"
    )
    # Сложность S/M/L — меняет только lead.
    complexity: Mapped[str] = mapped_column(
        String(1), nullable=False, default=DesignComplexity.M.value, server_default="M"
    )
    # Р9: сигнал-подсветка «срочно» от менеджера; порядок НЕ меняет.
    is_urgent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DesignTaskStatus.NEW.value, server_default="NEW"
    )
    # Порядок в колонке (Р3): шаг 1000, вставка в середину зазора.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Менеджер-постановщик = приёмщик.
    author_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    # Назначает только lead (owner/admin).
    assignee_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    # Аутсорс — флаг, ставит lead.
    is_outsourced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    # Р5: красная метка «непросмотрено» = status NEW && viewed_by_lead_at IS NULL.
    viewed_by_lead_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Р1: откуда отложили (для возврата из ON_HOLD); пишет только change_status (Ф1).
    held_from_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)   # первый вход в IN_PROGRESS
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # вход в ACCEPTED

    # Relationships
    materials: Mapped[list["DesignMaterial"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    submissions: Mapped[list["DesignSubmission"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    # SoftDelete-модель: без delete-orphan (физический DELETE запрещён, iron rule 3).
    comments: Mapped[list["DesignTaskComment"]] = relationship(back_populates="task")
    events: Mapped[list["DesignTaskEvent"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # DB-гарантия изоляции детей: составные FK детей ссылаются на (id, project_id).
        UniqueConstraint("id", "project_id", name="uq_design_tasks_id_project"),
        # Partial-индексы: доска/список/календарь всегда фильтруют is_deleted = false.
        Index(
            "ix_design_tasks_project_status_sort",
            "project_id",
            "status",
            "sort_order",
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "ix_design_tasks_project_assignee",
            "project_id",
            "assignee_user_id",
            "status",
            postgresql_where=text("is_deleted = false"),
        ),
        Index(
            "ix_design_tasks_project_due",
            "project_id",
            "due_date",
            postgresql_where=text("is_deleted = false"),
        ),
        Index("ix_design_tasks_author", "project_id", "author_user_id"),
        # Partial-unique номера DES-N (Р14); дублирует dsn01 — иначе autogenerate-дрейф.
        Index(
            "uq_design_tasks_project_number",
            "project_id",
            "number",
            unique=True,
            postgresql_where=text("is_deleted = false"),
        ),
    )


# ─── DesignMaterial («Исходные материалы», Р12) ──────────────────────────────


class DesignMaterial(Base):
    """Исходный материал заявки: файл (MinIO) / ссылка / артикул-пример."""

    __tablename__ = "design_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    # Составной FK (task_id, project_id) — в __table_args__ (DB-гарантия изоляции).
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    minio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    ref_nm_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    caption: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    task: Mapped["DesignTask"] = relationship(back_populates="materials")

    __table_args__ = (
        # Ровно один payload, соответствующий kind.
        CheckConstraint(
            "(kind = 'FILE' AND minio_path IS NOT NULL AND url IS NULL AND ref_nm_id IS NULL) OR "
            "(kind = 'LINK' AND url IS NOT NULL AND minio_path IS NULL AND ref_nm_id IS NULL) OR "
            "(kind = 'NM' AND ref_nm_id IS NOT NULL AND minio_path IS NULL AND url IS NULL)",
            name="ck_design_material_payload",
        ),
        ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        Index("ix_design_materials_project_task", "project_id", "task_id"),
        Index("ix_design_materials_task_id", "task_id"),
        # FK ⇒ индекс (миграция dsn03; дублируется здесь — иначе autogenerate-дрейф).
        Index("ix_design_materials_created_by_user_id", "created_by_user_id"),
    )


# ─── DesignSubmission (версии сдач; версии не удаляются — без SoftDelete) ────


class DesignSubmission(Base):
    """Версия сдачи по задаче: файлы + вердикт приёмщика."""

    __tablename__ = "design_submissions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    # Составной FK (task_id, project_id) — в __table_args__ (DB-гарантия изоляции).
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # max+1 под lock (Ф1).
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    submitted_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict: Mapped[str] = mapped_column(
        String(10), nullable=False, default=DesignVerdict.PENDING.value, server_default="PENDING"
    )
    # Обязателен при REJECTED — гвард в сервисе (Ф1).
    verdict_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    verdict_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)
    verdict_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    task: Mapped["DesignTask"] = relationship(back_populates="submissions")
    files: Mapped[list["DesignSubmissionFile"]] = relationship(
        back_populates="submission", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("task_id", "version_no", name="uq_design_submissions_task_version"),
        # DB-гарантия изоляции файлов сдач: составной FK детей на (id, project_id).
        UniqueConstraint("id", "project_id", name="uq_design_submissions_id_project"),
        ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        Index("ix_design_submissions_project_task", "project_id", "task_id"),
        Index("ix_design_submissions_task_id", "task_id"),
        # FK ⇒ индекс (миграция dsn03; дублируется здесь — иначе autogenerate-дрейф).
        Index("ix_design_submissions_submitted_by_user_id", "submitted_by_user_id"),
        Index("ix_design_submissions_verdict_by_user_id", "verdict_by_user_id"),
    )


class DesignSubmissionFile(Base):
    """Файл версии сдачи (MinIO)."""

    __tablename__ = "design_submission_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    # Составной FK (submission_id, project_id) — в __table_args__ (DB-гарантия изоляции).
    submission_id: Mapped[int] = mapped_column(Integer, nullable=False)
    minio_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    submission: Mapped["DesignSubmission"] = relationship(back_populates="files")

    __table_args__ = (
        ForeignKeyConstraint(
            ["submission_id", "project_id"],
            ["design_submissions.id", "design_submissions.project_id"],
            ondelete="CASCADE",
        ),
        Index("ix_design_submission_files_project_id", "project_id"),
        Index("ix_design_submission_files_submission_id", "submission_id"),
    )


# ─── DesignTaskComment ───────────────────────────────────────────────────────


class DesignTaskComment(Base, SoftDeleteMixin):
    """Комментарий к задаче (+опциональный файл-вложение)."""

    __tablename__ = "design_task_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    # Составной FK (task_id, project_id) — в __table_args__ (DB-гарантия изоляции).
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    author_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    minio_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    task: Mapped["DesignTask"] = relationship(back_populates="comments")

    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        Index("ix_design_task_comments_project_task", "project_id", "task_id"),
        Index("ix_design_task_comments_task_id", "task_id"),
        # FK ⇒ индекс (миграция dsn03; дублируется здесь — иначе autogenerate-дрейф).
        Index("ix_design_task_comments_author_user_id", "author_user_id"),
    )


# ─── DesignTaskEvent (журнал переходов; append-only, зеркало PaymentRequestEvent) ─


class DesignTaskEvent(Base):
    """Аудит-лог переходов статуса задачи. Append-only — не подтирается."""

    __tablename__ = "design_task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    # Составной FK (task_id, project_id) — в __table_args__ (DB-гарантия изоляции).
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    old_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_status: Mapped[str] = mapped_column(String(20), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    changed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    task: Mapped["DesignTask"] = relationship(back_populates="events")

    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        Index("ix_design_task_events_project_task", "project_id", "task_id"),
        Index("ix_design_task_events_task_id", "task_id"),
    )


# ─── Справочники волны C v2: метки и реквизиты ───────────────────────────────
#
# Все таблицы project_id-scoped, изоляция детей — составными FK, как в v1.
#
# Про SoftDeleteMixin у справочников: он есть ради инварианта §6.2 и
# единообразия выборок, но soft_delete() по ним НЕ вызывается никогда.
# Пользовательское «Удалить» — это АРХИВИРОВАНИЕ (Р30): запись пропадает из
# выбора для новых задач, но остаётся видимой на старых и в аналитике.
# is_deleted зарезервирован на будущее жёсткое удаление и всегда false.
# Поэтому все partial-unique условны по ОБОИМ флагам: индекс только по
# is_deleted держал бы имя архивной записи занятым навсегда, и попытка завести
# запись с тем же именем падала бы IntegrityError (500) вместо контрактного 400.
# Условие индекса и гвард уникальности в сервисе обязаны совпадать.


class DesignLabelColor(str, enum.Enum):
    """Десять цветов меток (Р26). Произвольный hex запрещён: палитра — часть
    дизайн-системы, токены --color-label-* живут в globals.css."""

    RED = "red"
    ORANGE = "orange"
    AMBER = "amber"
    GREEN = "green"
    TEAL = "teal"
    BLUE = "blue"
    VIOLET = "violet"
    PINK = "pink"
    BROWN = "brown"
    SLATE = "slate"


class DesignLabel(Base, TimestampMixin, SoftDeleteMixin):
    """Цветная метка проекта. Ведёт ведущий дизайнер."""

    __tablename__ = "design_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    # Значение DesignLabelColor; String, не pg-enum — правило модуля (Alembic-churn).
    color: Mapped[str] = mapped_column(String(20), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_design_labels_id_project"),
        Index(
            "uq_design_labels_project_name",
            "project_id",
            "name",
            unique=True,
            postgresql_where=text("is_deleted = false AND is_archived = false"),
        ),
        Index(
            "ix_design_labels_project_sort",
            "project_id",
            "sort_order",
            postgresql_where=text("is_deleted = false"),
        ),
    )


class DesignTaskLabel(Base):
    """Связь «задача ↔ метка» С ИСТОРИЕЙ.

    Снятие метки не удаляет строку, а проставляет removed_at — это и есть
    счётчик Р20 «была с меткой N раз». Считать историю по DesignTaskEvent.comment
    нельзя: там свободный текст, и счёт сломался бы при переименовании метки.
    """

    __tablename__ = "design_task_labels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, nullable=False)
    label_id: Mapped[int] = mapped_column(Integer, nullable=False)
    attached_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    attached_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    removed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["label_id", "project_id"],
            ["design_labels.id", "design_labels.project_id"],
            ondelete="CASCADE",
        ),
        # Метка не задваивается среди ТЕКУЩИХ, но история хранит все прошлые.
        Index(
            "uq_design_task_labels_active",
            "task_id",
            "label_id",
            unique=True,
            postgresql_where=text("removed_at IS NULL"),
        ),
        Index(
            "ix_design_task_labels_project_task",
            "project_id",
            "task_id",
            postgresql_where=text("removed_at IS NULL"),
        ),
        Index("ix_design_task_labels_project_label", "project_id", "label_id"),
    )


class DesignAttribute(Base, TimestampMixin, SoftDeleteMixin):
    """Поле-список («Бренд», «Кабинет ВБ»). Универсальный справочник, не хардкод."""

    __tablename__ = "design_attributes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    # true — на задаче можно выбрать несколько значений этого поля.
    is_multi: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_design_attributes_id_project"),
        Index(
            "uq_design_attributes_project_name",
            "project_id",
            "name",
            unique=True,
            postgresql_where=text("is_deleted = false AND is_archived = false"),
        ),
        Index(
            "ix_design_attributes_project_sort",
            "project_id",
            "sort_order",
            postgresql_where=text("is_deleted = false"),
        ),
    )


class DesignAttributeValue(Base, SoftDeleteMixin):
    """Значение поля-списка («Меллори» у поля «Бренд»)."""

    __tablename__ = "design_attribute_values"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    attribute_id: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[str] = mapped_column(String(120), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    __table_args__ = (
        UniqueConstraint("id", "project_id", name="uq_design_attribute_values_id_project"),
        ForeignKeyConstraint(
            ["attribute_id", "project_id"],
            ["design_attributes.id", "design_attributes.project_id"],
            ondelete="CASCADE",
        ),
        Index(
            "uq_design_attribute_values_unique",
            "project_id",
            "attribute_id",
            "value",
            unique=True,
            postgresql_where=text("is_deleted = false AND is_archived = false"),
        ),
        Index(
            "ix_design_attribute_values_project_attribute",
            "project_id",
            "attribute_id",
            postgresql_where=text("is_deleted = false"),
        ),
    )


class DesignTaskAttributeValue(Base):
    """Связь «задача ↔ значение реквизита». Без истории: Р20 — про метки.

    Снятие значения — жёсткий DELETE строки (в отличие от меток).
    """

    __tablename__ = "design_task_attribute_values"

    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    task_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["value_id", "project_id"],
            ["design_attribute_values.id", "design_attribute_values.project_id"],
            ondelete="CASCADE",
        ),
        Index("ix_design_task_attr_values_project_task", "project_id", "task_id"),
        Index("ix_design_task_attr_values_project_value", "project_id", "value_id"),
    )
