"""
Service: payroll_agency — консалтинговое «Агентство»: клиентские проекты.

Fee клиента за месяц (канон юзера 2026-07-28):
- fixed        → fixed_amount;
- percent      → Σ по WB-неделям месяца (правило четверга): недельная база ×
                 team_rate тарифной лестницы (ставка «Команда», ступень как в
                 ведомости; база <= 0 → 0). База недели: внутренний кабинет
                 (linked_project_id) — «Чистая выплата» ВСЕГО кабинета из БДР
                 (summary.net_payout, period_mode='report'); внешний — ручная
                 запись PayrollClientEntry kind=week_base (нет записи → 0 + warning);
- profit_share → fee_percent × ЧП месяца (entry kind=month_profit; нет — 0 + warning).

Сплит: manager_amount = fee_total × manager_share → команде team_id (интеграция
в ведомость — get_team_agency_amounts, зовётся из payroll_service.get_sheet);
agency_amount = fee_total − manager_amount — только показываем, в ОПиУ НЕ
включаем (решение юзера «попозже»).

⚠️ Кросс-проектное чтение БДР клиента (linked_project_id) — ОСОЗНАННОЕ решение:
кабинет клиента заведён проектом в этой же инсталляции, доступ к агентской
вкладке гейтится page-гейтом `salary` агентского проекта. Но ПРИВЯЗАТЬ кабинет
может только владелец/участник этого кабинета (_validate_linked_project — тот же
предикат, что в project-options): иначе editor любого проекта перебором id
читал бы чужую выручку через ведомость агентства.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.cache import cached
from backend.models import Project, ProjectMember
from backend.models.payroll import (
    PayrollClientEntry,
    PayrollClientProject,
    PayrollTeam,
)
from backend.schemas.payroll import (
    AgencyClientWeek,
    AgencySheetClient,
    AgencySheetResponse,
    ClientEntryOut,
    ClientEntryUpsert,
    ClientProjectIn,
    ClientProjectListResponse,
    ClientProjectResponse,
    ClientProjectUpdate,
    OkResponse,
    ProjectOption,
    ProjectOptionsResponse,
)
from backend.services import wb_bdr_service
from backend.services.payroll_service import (
    ZERO,
    PayrollNotFoundError,
    _load_tariff_steps,
    _money,
    _next_month,
    _parse_month,
    resolve_step,
    weeks_for_month,
)
from backend.utils.time import utcnow

_CLIENTS_LIMIT = 500
_PROJECT_OPTIONS_LIMIT = 100


# ─── Загрузка / ответы ───────────────────────────────────────────────────────


async def _get_client(
    db: AsyncSession, project_id: int, client_id: int
) -> PayrollClientProject:
    client = (
        await db.execute(
            select(PayrollClientProject)
            .where(
                PayrollClientProject.id == client_id,
                PayrollClientProject.project_id == project_id,
                PayrollClientProject.is_deleted == False,  # noqa: E712
            )
            .options(
                selectinload(PayrollClientProject.entries),
                selectinload(PayrollClientProject.team),
            )
            # entry-upsert пишет мимо relationship — коллекции перечитываем
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if client is None:
        raise PayrollNotFoundError(f"Клиент {client_id} не найден")
    return client


def _client_response(
    client: PayrollClientProject, linked_project_name: str | None = None
) -> ClientProjectResponse:
    team = client.team
    team_name = team.name if (team is not None and not team.is_deleted) else None
    entries = sorted(client.entries, key=lambda e: (e.kind, e.date_from))
    return ClientProjectResponse(
        id=client.id,
        name=client.name,
        billing_mode=client.billing_mode,
        team_id=client.team_id,
        team_name=team_name,
        linked_project_id=client.linked_project_id,
        linked_project_name=linked_project_name,
        fixed_amount=client.fixed_amount,
        fee_percent=client.fee_percent,
        manager_share=client.manager_share,
        is_active=client.is_active,
        notes=client.notes,
        entries=[
            ClientEntryOut(kind=e.kind, date_from=e.date_from, amount=e.amount)
            for e in entries
        ],
    )


async def _project_names(db: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await db.execute(
        select(Project.id, Project.name).where(
            Project.id.in_(ids),
            Project.is_deleted == False,  # noqa: E712
        )
    )
    return {int(r[0]): r[1] for r in rows}


async def _validate_team(db: AsyncSession, project_id: int, team_id: int) -> None:
    team = (
        await db.execute(
            select(PayrollTeam.id).where(
                PayrollTeam.id == team_id,
                PayrollTeam.project_id == project_id,
                PayrollTeam.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if team is None:
        raise PayrollNotFoundError(f"Команда {team_id} не найдена")


async def _validate_linked_project(
    db: AsyncSession, user_id: int, linked_project_id: int
) -> None:
    """Кабинет клиента: только проект, где ТЕКУЩИЙ юзер владелец или участник
    (тот же предикат, что get_project_options). Чужой/несуществующий → 404 —
    без этого editor перебором id биндил бы чужие кабинеты и читал их выручку."""
    proj = (
        await db.execute(
            select(Project.id)
            .outerjoin(
                ProjectMember,
                (ProjectMember.project_id == Project.id)
                & (ProjectMember.user_id == user_id)
                & (ProjectMember.is_deleted == False),  # noqa: E712
            )
            .where(
                Project.id == linked_project_id,
                Project.is_deleted == False,  # noqa: E712
                or_(Project.owner_id == user_id, ProjectMember.id.isnot(None)),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if proj is None:
        raise PayrollNotFoundError(f"Проект {linked_project_id} не найден")


# ─── CRUD клиентов ───────────────────────────────────────────────────────────


async def list_clients(db: AsyncSession, project_id: int) -> ClientProjectListResponse:
    clients = list(
        (
            await db.execute(
                select(PayrollClientProject)
                .where(
                    PayrollClientProject.project_id == project_id,
                    PayrollClientProject.is_deleted == False,  # noqa: E712
                )
                .options(
                    selectinload(PayrollClientProject.entries),
                    selectinload(PayrollClientProject.team),
                )
                .execution_options(populate_existing=True)
                .order_by(PayrollClientProject.id)
                .limit(_CLIENTS_LIMIT)
            )
        ).scalars()
    )
    names = await _project_names(
        db, {c.linked_project_id for c in clients if c.linked_project_id is not None}
    )
    return ClientProjectListResponse(
        items=[
            _client_response(c, linked_project_name=names.get(c.linked_project_id or 0))
            for c in clients
        ]
    )


async def create_client(
    db: AsyncSession, project_id: int, data: ClientProjectIn, user_id: int
) -> ClientProjectResponse:
    if data.team_id is not None:
        await _validate_team(db, project_id, data.team_id)
    if data.linked_project_id is not None:
        await _validate_linked_project(db, user_id, data.linked_project_id)
    client = PayrollClientProject(
        project_id=project_id,
        name=data.name,
        billing_mode=data.billing_mode,
        team_id=data.team_id,
        linked_project_id=data.linked_project_id,
        fixed_amount=data.fixed_amount,
        fee_percent=data.fee_percent,
        manager_share=data.manager_share,
        is_active=data.is_active,
        notes=data.notes,
    )
    db.add(client)
    await db.flush()
    client_id = client.id
    await db.commit()
    client = await _get_client(db, project_id, client_id)
    names = await _project_names(
        db, {client.linked_project_id} if client.linked_project_id else set()
    )
    return _client_response(
        client, linked_project_name=names.get(client.linked_project_id or 0)
    )


async def update_client(
    db: AsyncSession,
    project_id: int,
    client_id: int,
    data: ClientProjectUpdate,
    user_id: int,
) -> ClientProjectResponse:
    client = await _get_client(db, project_id, client_id)
    provided = data.model_fields_set

    if data.name is not None:
        client.name = data.name
    if data.billing_mode is not None:
        client.billing_mode = data.billing_mode
    if data.clear_team:
        client.team_id = None
    elif data.team_id is not None:
        await _validate_team(db, project_id, data.team_id)
        client.team_id = data.team_id
    if data.clear_linked_project:
        client.linked_project_id = None
    elif data.linked_project_id is not None:
        await _validate_linked_project(db, user_id, data.linked_project_id)
        client.linked_project_id = data.linked_project_id
    if data.fixed_amount is not None:
        client.fixed_amount = data.fixed_amount
    if data.fee_percent is not None:
        client.fee_percent = data.fee_percent
    if data.manager_share is not None:
        client.manager_share = data.manager_share
    if data.is_active is not None:
        client.is_active = data.is_active
    if "notes" in provided:
        client.notes = data.notes

    await db.commit()
    client = await _get_client(db, project_id, client_id)
    names = await _project_names(
        db, {client.linked_project_id} if client.linked_project_id else set()
    )
    return _client_response(
        client, linked_project_name=names.get(client.linked_project_id or 0)
    )


async def delete_client(
    db: AsyncSession, project_id: int, client_id: int
) -> OkResponse:
    client = await _get_client(db, project_id, client_id)
    client.soft_delete()
    await db.commit()
    return OkResponse()


# ─── Ручные суммы (entries) ──────────────────────────────────────────────────


def _validate_entry_date(kind: str, date_from: date) -> None:
    if kind == "week_base" and date_from.weekday() != 0:
        raise ValueError("week_base: date_from должен быть понедельником недели")
    if kind == "month_profit" and date_from.day != 1:
        raise ValueError("month_profit: date_from должен быть первым числом месяца")


async def upsert_entry(
    db: AsyncSession, project_id: int, client_id: int, data: ClientEntryUpsert
) -> OkResponse:
    """Атомарный upsert по (client, kind, date_from) — как payout-mark."""
    await _get_client(db, project_id, client_id)  # 404 на чужой id
    _validate_entry_date(data.kind, data.date_from)
    now = utcnow()
    stmt = (
        pg_insert(PayrollClientEntry)
        .values(
            client_id=client_id,
            kind=data.kind,
            date_from=data.date_from,
            amount=data.amount,
            created_at=now,
            updated_at=now,
        )
        .on_conflict_do_update(
            constraint="uq_payroll_client_entry",
            set_={"amount": data.amount, "updated_at": now},
        )
    )
    await db.execute(stmt)
    await db.commit()
    return OkResponse()


async def delete_entry(
    db: AsyncSession, project_id: int, client_id: int, kind: str, date_from: date
) -> OkResponse:
    await _get_client(db, project_id, client_id)  # 404 на чужой id
    await db.execute(
        delete(PayrollClientEntry).where(
            PayrollClientEntry.client_id == client_id,
            PayrollClientEntry.kind == kind,
            PayrollClientEntry.date_from == date_from,
        )
    )
    await db.commit()
    return OkResponse()


# ─── Ведомость агентства ─────────────────────────────────────────────────────


async def _percent_weeks(
    db: AsyncSession,
    client: PayrollClientProject,
    weeks: list[tuple[date, date]],
    steps: list,
    entries_by_week: dict[date, Decimal],
    warnings: list[str],
) -> list[AgencyClientWeek]:
    """Недели percent-режима: база × ставка «Команда» лестницы."""
    rows: list[AgencyClientWeek] = []
    missing: list[date] = []
    for week_from, week_to in weeks:
        manual = client.linked_project_id is None
        # Фронту важно отличать «введён явный 0» от «не введено» — иначе
        # инпут префилится нулём и явный 0 не создаёт запись.
        has_entry = manual and week_from in entries_by_week
        if client.linked_project_id is not None:
            # Кросс-проектное чтение БДР кабинета клиента — осознанно (см. модуль).
            # net_payout summary = фактическая выплата ВБ кабинета за неделю
            # (см. finalize_net_payout: summary.net_payout == to_pay).
            bdr = await wb_bdr_service.get_wb_bdr(
                db,
                client.linked_project_id,
                week_from,
                week_to,
                period_mode="report",
                include_cost_tax=False,
            )
            base = Decimal(str((bdr.get("summary") or {}).get("net_payout", 0) or 0))
        else:
            found = entries_by_week.get(week_from)
            if found is None:
                missing.append(week_from)
                base = ZERO
            else:
                base = found
        base = _money(base)
        step = resolve_step(base, steps)
        if step is None:
            threshold: Decimal | None = None
            rate = ZERO
            fee = _money(ZERO)
        else:
            threshold = step.threshold
            rate = step.team_rate  # ставка «Команда», НЕ «Компания»
            fee = _money(base * rate)
        rows.append(
            AgencyClientWeek(
                date_from=week_from,
                date_to=week_to,
                base_amount=base,
                threshold=threshold,
                team_rate=rate,
                fee=fee,
                manual=manual,
                has_entry=has_entry,
            )
        )
    if missing:
        warnings.append(
            "Не введена недельная база: " + ", ".join(d.isoformat() for d in missing)
        )
    return rows


@cached(prefix="payroll:agency_sheet", ttl=600)
async def get_agency_sheet(db: AsyncSession, project_id: int, month: str) -> dict:
    """
    Месячная ведомость агентства: fee по клиентам, сплит менеджеры/агентство.

    Возвращает JSON-safe dict (контракт AgencySheetResponse) — кэш-хит и мисс
    отдают одинаковые типы. TTL 600: ведомость производна от БДР ЧУЖОГО проекта
    (кабинета клиента), а его ETL гасит только свои ключи — reverse-lookup не
    делаем осознанно, свежесть добирается коротким TTL.
    """
    month_start = _parse_month(month)
    weeks = weeks_for_month(month_start)
    # Лестница — на ПОСЛЕДНИЙ день расчётного месяца (как payroll_service.
    # get_sheet): ревизия с valid_from=1-е следующего месяца не должна
    # переписывать текущий месяц и расходиться с ведомостью ЗП.
    month_end = _next_month(month_start) - timedelta(days=1)
    steps = await _load_tariff_steps(db, month_end)

    clients = list(
        (
            await db.execute(
                select(PayrollClientProject)
                .where(
                    PayrollClientProject.project_id == project_id,
                    PayrollClientProject.is_deleted == False,  # noqa: E712
                )
                .options(
                    selectinload(PayrollClientProject.entries),
                    selectinload(PayrollClientProject.team),
                )
                .execution_options(populate_existing=True)
                .order_by(PayrollClientProject.id)
                .limit(_CLIENTS_LIMIT)
            )
        ).scalars()
    )

    sheet_clients: list[AgencySheetClient] = []
    totals_fee = ZERO
    totals_manager = ZERO
    totals_agency = ZERO

    for client in clients:
        if not client.is_active:
            continue
        warnings: list[str] = []
        team = client.team
        team_alive = team is not None and not team.is_deleted
        team_name = team.name if (team_alive and team is not None) else None
        if client.team_id is None or not team_alive:
            warnings.append(
                "Не назначена команда — доля менеджеров не попадёт в ведомость"
            )

        week_rows: list[AgencyClientWeek] = []
        profit_amount: Decimal | None = None
        if client.billing_mode == "fixed":
            if client.fixed_amount is None:
                warnings.append("Не задана фикс-сумма")
            fee_total = _money(client.fixed_amount or ZERO)
        elif client.billing_mode == "percent":
            entries_by_week = {
                e.date_from: e.amount
                for e in client.entries
                if e.kind == "week_base"
            }
            week_rows = await _percent_weeks(
                db, client, weeks, steps, entries_by_week, warnings
            )
            fee_total = _money(sum((w.fee for w in week_rows), ZERO))
        else:  # profit_share
            entry = next(
                (
                    e
                    for e in client.entries
                    if e.kind == "month_profit" and e.date_from == month_start
                ),
                None,
            )
            if entry is None:
                warnings.append("Не введена чистая прибыль месяца")
                fee_total = _money(ZERO)
            else:
                profit_amount = entry.amount
                if client.fee_percent is None:
                    warnings.append("Не задан процент от прибыли")
                    fee_total = _money(ZERO)
                else:
                    fee_total = _money(entry.amount * client.fee_percent)

        manager_amount = _money(fee_total * client.manager_share)
        agency_amount = _money(fee_total - manager_amount)
        totals_fee += fee_total
        totals_manager += manager_amount
        totals_agency += agency_amount
        sheet_clients.append(
            AgencySheetClient(
                client_id=client.id,
                name=client.name,
                billing_mode=client.billing_mode,
                team_id=client.team_id if team_alive else None,
                team_name=team_name,
                manager_share=client.manager_share,
                weeks=week_rows,
                profit_amount=profit_amount,
                fee_percent=client.fee_percent,
                fee_total=fee_total,
                manager_amount=manager_amount,
                agency_amount=agency_amount,
                warnings=warnings,
            )
        )

    response = AgencySheetResponse(
        month=month,
        clients=sheet_clients,
        totals_fee=_money(totals_fee),
        totals_manager=_money(totals_manager),
        totals_agency=_money(totals_agency),
    )
    return response.model_dump(mode="json")


async def get_team_agency_amounts(
    db: AsyncSession, project_id: int, month: str
) -> dict[int, list[tuple[str, Decimal]]]:
    """
    team_id → [(имя клиента, manager_amount месяца)] — для интеграции в ведомость
    ЗП (payroll_service.get_sheet добавляет строку «{team} · {client} (агентство)»).
    """
    # Short-circuit: у не-агентских проектов нет клиентов — не строим ведомость
    has_clients = (
        await db.execute(
            select(PayrollClientProject.id)
            .where(
                PayrollClientProject.project_id == project_id,
                PayrollClientProject.is_deleted == False,  # noqa: E712
                PayrollClientProject.is_active == True,  # noqa: E712
                PayrollClientProject.team_id.isnot(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if has_clients is None:
        return {}
    sheet = await get_agency_sheet(db, project_id, month)
    result: dict[int, list[tuple[str, Decimal]]] = {}
    for client in sheet["clients"]:
        team_id = client.get("team_id")
        amount = Decimal(str(client.get("manager_amount", "0")))
        if team_id and amount:
            result.setdefault(int(team_id), []).append((client["name"], amount))
    return result


# ─── Проекты-опции для привязки кабинета ─────────────────────────────────────


async def get_project_options(db: AsyncSession, user_id: int) -> ProjectOptionsResponse:
    """Проекты, где юзер участник или владелец, — кандидаты в linked_project_id."""
    rows = (
        await db.execute(
            select(Project)
            .outerjoin(
                ProjectMember,
                (ProjectMember.project_id == Project.id)
                & (ProjectMember.user_id == user_id)
                & (ProjectMember.is_deleted == False),  # noqa: E712
            )
            .where(
                Project.is_deleted == False,  # noqa: E712
                or_(Project.owner_id == user_id, ProjectMember.id.isnot(None)),
            )
            .distinct()
            .order_by(Project.id)
            .limit(_PROJECT_OPTIONS_LIMIT)
        )
    ).scalars()
    return ProjectOptionsResponse(
        items=[ProjectOption(id=p.id, name=p.name, slug=p.slug) for p in rows]
    )


__all__ = [
    "create_client",
    "delete_client",
    "delete_entry",
    "get_agency_sheet",
    "get_project_options",
    "get_team_agency_amounts",
    "list_clients",
    "update_client",
    "upsert_entry",
]
