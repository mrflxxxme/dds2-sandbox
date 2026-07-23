"""
AssemblyWbSupply: 1:1 связь заявки-сборки (AssemblyRequest) с её FBW-поставкой
в кабинете Wildberries, заводимой через реплей внутреннего API портала
seller-supply.wildberries.ru.

Зеркалит жизненный цикл WB-поставки: черновик → преордер → (ручная бронь даты)
→ короба → пропуск. Финальная бронь даты (plan/add) закрыта антибот-челленджем
и остаётся ручным кликом в портале; всё остальное — автоматический реплей.

TZ: см. backend/DOMAIN_ASSEMBLY.md.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from backend.models.assembly import AssemblyRequest


class WbSupplySyncStatus(enum.StrEnum):
    """Стадия заноса поставки в WB через реплей портала."""

    NONE = "NONE"  # ничего не заведено
    DRAFT = "DRAFT"  # черновик создан, товары набиты
    PREORDER = "PREORDER"  # преордер создан (склад+тип), ждёт ручной брони даты
    BOOKED = "BOOKED"  # дата забронирована человеком (supply_id получен)
    BOXED = "BOXED"  # короба заведены и наполнены
    PASSED = "PASSED"  # пропуск (водитель/авто/паллеты) занесён
    ERROR = "ERROR"  # последняя операция реплея упала (см. last_error)


class AssemblyWbSupply(Base, TimestampMixin):
    """
    1:1 связь AssemblyRequest ↔ WB FBW-поставка (реплей портала).

    Хранит идентификаторы WB через все стадии, локально-редактируемую раскладку
    коробов и данные пропуска. Живёт/умирает вместе с заявкой (CASCADE); 1:1
    гарантируется unique-индексом по assembly_request_id.
    """

    __tablename__ = "assembly_wb_supply"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    assembly_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assembly_requests.id", ondelete="CASCADE"), nullable=False
    )

    # WB-склад назначения (числовой id портала, резолвится из имени направления).
    warehouse_id_wb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Тип упаковки WB: 2 = Короб, 5 = Монопаллета (boxTypeID из supply/create).
    box_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_box_on_pallet: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Идентификаторы WB по стадиям.
    draft_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # uuid черновика
    preorder_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    supply_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    barcode_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # WB-GI для пропуска

    sync_status: Mapped[str] = mapped_column(
        String(20),
        default=WbSupplySyncStatus.NONE.value,
        server_default=WbSupplySyncStatus.NONE.value,
        nullable=False,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Живое состояние поставки в кабинете WB (bulk-синк через supply/listSupplies):
    # человекочитаемое имя статуса («В сборке» / «Готово к отгрузке» / «В пути» …)
    # + числовой statusID портала. Отдельная метка времени, чтобы не путать с
    # операционным last_synced_at (тот двигают ручные шаги реплея).
    wb_supply_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    wb_supply_state_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wb_state_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Дата забронированного слота сдачи в WB (supplyDetails.supplyDate) и текст
    # кабинетных ошибок поставки (supplyDetails.rejectReason — «Не заполнены ШК
    # коробов…», «Не заполнен пропуск…»). Обновляются при чтении карточки поставки.
    supply_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Раскладка коробов: [{"boxcode": str|null, "items": [{"barcode": str, "quantity": int}]}]
    # boxcode = null, пока короб не создан в WB (createBoxBarcodes), затем заполняется.
    boxes: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]", nullable=False
    )

    # Пропуск (данные для setTRNDetails).
    pass_driver_first: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pass_driver_last: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pass_driver_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    pass_car_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pass_car_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    pass_pallets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Способ отгрузки пропуска (setTRNDetails.boxTypeName): False = паллеты («pallets»),
    # True = отдельные короба («box»). pass_pallets в обоих случаях = число единиц.
    # По умолчанию берётся из AssemblyRequest.shipped_as_boxes (sync_pass_from_vehicle).
    pass_as_boxes: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    # Снимок ФАКТИЧЕСКОГО пропуска из кабинета WB (trn_details), синкается
    # sync_all_states. Нужен для сверки нашего пропуска (pass_*) с кабинетным без
    # построчного HTTP. None у wb_pass_present = ещё не синкали.
    wb_pass_present: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    wb_pass_car_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    wb_pass_pallets: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wb_pass_driver: Mapped[str | None] = mapped_column(String(200), nullable=True)
    wb_pass_synced_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    assembly_request: Mapped["AssemblyRequest"] = relationship()

    __table_args__ = (
        Index("ix_assembly_wb_supply_project_id", "project_id"),
        Index(
            "ix_assembly_wb_supply_assembly_request_id",
            "assembly_request_id",
            unique=True,
        ),
        Index("ix_assembly_wb_supply_supply_id", "supply_id"),
    )
