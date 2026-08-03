"""
Payroll models: сотрудники, команды брендов и тарифная лестница зарплаты.

Схема начисления: команда (1-2 сотрудника) владеет набором брендов и/или
категорий (предметов); её начисление за неделю = недельная «Чистая выплата»
БДР по этим брендам × ставка «Команда» из тарифной лестницы (ступень —
ближайший порог снизу, ниже минимального порога — минимальная ставка,
отрицательная неделя — 0). Процент делится между участниками поровну.
Начисление за месяц = сумма недель, привязанных к месяцу (неделя Пн-Вс
принадлежит месяцу своего четверга). Выплата 50/50 десятого и двадцать
пятого числа следующего месяца. Отдельно — фиксированные оклады
(fixed_salary + fixed_pay_days). Официальная часть вычитается по факту
из банковской выписки через counterparty_id.
"""

import enum
from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base
from backend.models.mixins import SoftDeleteMixin, TimestampMixin


class PayrollScopeKind(str, enum.Enum):
    """Чем владеет команда: брендом или категорией (предметом) WB."""

    BRAND = "brand"
    SUBJECT = "subject"


class PayrollClientBillingMode(str, enum.Enum):
    """Формат оплаты клиента консалтингового агентства."""

    FIXED = "fixed"  # фикс-сумма в месяц
    PERCENT = "percent"  # ставка «Команда» лестницы × недельная «Чистая выплата» клиента
    PROFIT_SHARE = "profit_share"  # fee_percent × чистая прибыль месяца (ЧП вводится руками)


class PayrollClientEntryKind(str, enum.Enum):
    """Ручные суммы клиента: недельная база (внешний кабинет) или ЧП месяца."""

    WEEK_BASE = "week_base"  # «Чистая выплата» за неделю Пн-Вс (date_from = Пн)
    MONTH_PROFIT = "month_profit"  # чистая прибыль месяца (date_from = 1-е число)


class PayrollEmployee(Base, TimestampMixin, SoftDeleteMixin):
    """
    Сотрудник. counterparty_id связывает с контрагентом-физлицом из банковской
    выписки — по нему подтягивается факт официальных выплат.
    """

    __tablename__ = "payroll_employee"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # Должность («Бухгалтер», «Логист»…) — группирует фикс-оклады в разбивке
    # ФОТ ОПиУ; процентные начисления команд идут строкой «Менеджеры» независимо
    # от должности.
    position: Mapped[str | None] = mapped_column(String(100), nullable=True)
    counterparty_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("counterparty.id"), nullable=True)
    # График выплат фикса: [{"day": 10, "share": 0.5}, {"day": 25, "share": 0.5}].
    # None — дефолт 50/50 на 10-е и 25-е. Логист: [{"day": 15, "share": 1}].
    fixed_pay_days: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    salary_periods: Mapped[list["PayrollSalaryPeriod"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan", order_by="PayrollSalaryPeriod.valid_from"
    )

    __table_args__ = (Index("ix_payroll_employee_project_id", "project_id"),)


class PayrollSalaryPeriod(Base, TimestampMixin):
    """
    Период фикс-оклада: с месяца valid_from (первое число) действует amount.

    Оклад месяца M = период с максимальным valid_from <= M; до первого периода
    начисления нет (история: «у бух была 50к, с июля стала 80к» — два периода).
    """

    __tablename__ = "payroll_salary_period"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("payroll_employee.id"), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    employee: Mapped["PayrollEmployee"] = relationship(back_populates="salary_periods")

    __table_args__ = (
        UniqueConstraint("employee_id", "valid_from", name="uq_payroll_salary_period"),
        Index("ix_payroll_salary_period_employee_id", "employee_id"),
    )


class PayrollTeam(Base, TimestampMixin, SoftDeleteMixin):
    """Команда — единица начисления процента. Привязана к одному проекту."""

    __tablename__ = "payroll_team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    members: Mapped[list["PayrollTeamMember"]] = relationship(back_populates="team", cascade="all, delete-orphan")
    scopes: Mapped[list["PayrollTeamScope"]] = relationship(back_populates="team", cascade="all, delete-orphan")

    __table_args__ = (Index("ix_payroll_team_project_id", "project_id"),)


class PayrollTeamMember(Base):
    """
    Участник команды. Доли всегда равные, поэтому поля доли нет.

    Членство ограничено месяцами: valid_from/valid_to (первое число, NULL =
    без границы). Начисление месяца M получают только участники с
    valid_from <= M <= valid_to.
    """

    __tablename__ = "payroll_team_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("payroll_team.id"), nullable=False)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("payroll_employee.id"), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    team: Mapped["PayrollTeam"] = relationship(back_populates="members")
    employee: Mapped["PayrollEmployee"] = relationship()

    __table_args__ = (
        UniqueConstraint("team_id", "employee_id", name="uq_payroll_team_member"),
        Index("ix_payroll_team_member_team_id", "team_id"),
        Index("ix_payroll_team_member_employee_id", "employee_id"),
    )


class PayrollTeamScope(Base):
    """
    Скоуп команды: бренд, категория или их пересечение (бренд × категория).

    brand_value / subject_value — значения как в wb_finance_rows; заполнено
    хотя бы одно. Композитный скоуп (оба поля) ВЫТЕСНЯЕТ общий: строки
    (бренд, категория), закреплённые композитом за какой-либо командой,
    вычитаются из базы команд с одноимённым brand-only/subject-only скоупом
    («Мария получает за коврики в ванную ну-ну, остальные — всё кроме них»).
    """

    __tablename__ = "payroll_team_scope"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("payroll_team.id"), nullable=False)
    brand_value: Mapped[str | None] = mapped_column(String(300), nullable=True)
    subject_value: Mapped[str | None] = mapped_column(String(300), nullable=True)

    team: Mapped["PayrollTeam"] = relationship(back_populates="scopes")

    __table_args__ = (
        UniqueConstraint(
            "team_id",
            "brand_value",
            "subject_value",
            name="uq_payroll_team_scope",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_payroll_team_scope_team_id", "team_id"),
    )


class PayrollClientProject(Base, TimestampMixin, SoftDeleteMixin):
    """
    Клиентский проект консалтингового агентства.

    project_id — проект-агентство, в котором ведётся учёт. Сплит выручки:
    manager_share (45%) уходит команде team_id как обычное начисление
    (ведомость + ФОТ «Менеджеры»), остаток — агентству (в ОПиУ пока не
    включается — решение юзера 2026-07-28 «попозже»).
    linked_project_id — кабинет клиента, если он заведён проектом в этой же
    системе (percent-режим считается автоматически от его недельной «Чистой
    выплаты»); NULL — внешний клиент, недельные базы вводятся руками.
    Формат оплаты ведётся ПЕРИОДАМИ (billing_periods): формат месяца M =
    период с max(valid_from) <= M, до первого периода начисления нет —
    «у Брыссина раньше был процент, с июля оклад».
    """

    __tablename__ = "payroll_client_project"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    team_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("payroll_team.id"), nullable=True)
    linked_project_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("projects.id"), nullable=True)
    manager_share: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0.45"), server_default="0.45"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    team: Mapped["PayrollTeam | None"] = relationship()
    entries: Mapped[list["PayrollClientEntry"]] = relationship(
        back_populates="client", cascade="all, delete-orphan"
    )
    billing_periods: Mapped[list["PayrollClientBillingPeriod"]] = relationship(
        back_populates="client",
        cascade="all, delete-orphan",
        order_by="PayrollClientBillingPeriod.valid_from",
    )

    __table_args__ = (
        Index("ix_payroll_client_project_project_id", "project_id"),
        Index("ix_payroll_client_project_team_id", "team_id"),
    )


class PayrollClientBillingPeriod(Base, TimestampMixin):
    """
    Период формата оплаты клиента: с месяца valid_from действует billing_mode
    со своими параметрами (fixed_amount для fixed, fee_percent для profit_share).
    """

    __tablename__ = "payroll_client_billing_period"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payroll_client_project.id"), nullable=False
    )
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    billing_mode: Mapped[str] = mapped_column(String(20), nullable=False)  # PayrollClientBillingMode
    fixed_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    fee_percent: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)

    client: Mapped["PayrollClientProject"] = relationship(back_populates="billing_periods")

    __table_args__ = (
        UniqueConstraint("client_id", "valid_from", name="uq_payroll_client_billing_period"),
        Index("ix_payroll_client_billing_period_client_id", "client_id"),
    )


class PayrollClientEntry(Base, TimestampMixin):
    """Ручная сумма клиента: недельная база (внешние) либо ЧП месяца."""

    __tablename__ = "payroll_client_entry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("payroll_client_project.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # PayrollClientEntryKind
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)

    client: Mapped["PayrollClientProject"] = relationship(back_populates="entries")

    __table_args__ = (
        UniqueConstraint("client_id", "kind", "date_from", name="uq_payroll_client_entry"),
        Index("ix_payroll_client_entry_client_id", "client_id"),
    )


class PayrollTariffStep(Base, TimestampMixin):
    """
    Ступень тарифной лестницы: порог недельной выплаты → ставки.

    Глобальный справочник уровня инсталляции (без project_id, как category_ref):
    лестница одна для всех команд всех проектов (решение юзера 2026-07-28).
    Версионность через valid_from: действующий набор — строки максимального
    valid_from <= даты расчёта.
    """

    __tablename__ = "payroll_tariff_step"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    threshold: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    company_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    team_rate: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)

    __table_args__ = (UniqueConstraint("valid_from", "threshold", name="uq_payroll_tariff_step"),)


class PayrollPayoutMark(Base, TimestampMixin):
    """
    Отметка «выплачено» по строке плана выплат (сотрудник × месяц × день выплаты).
    month — первое число расчётного месяца (за который начислено, не месяца выплаты).
    amount — фактически выплаченная сумма, если отличается от плановой.
    """

    __tablename__ = "payroll_payout_mark"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[int] = mapped_column(Integer, ForeignKey("projects.id"), nullable=False)
    employee_id: Mapped[int] = mapped_column(Integer, ForeignKey("payroll_employee.id"), nullable=False)
    month: Mapped[date] = mapped_column(Date, nullable=False)
    pay_day: Mapped[int] = mapped_column(Integer, nullable=False)
    paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("employee_id", "month", "pay_day", name="uq_payroll_payout_mark"),
        Index("ix_payroll_payout_mark_project_id", "project_id"),
        Index("ix_payroll_payout_mark_employee_id", "employee_id"),
    )
