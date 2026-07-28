"""
Router: /payroll — зарплата: сотрудники, команды, тарифная лестница, ведомость.

Только HTTP и маппинг ошибок — бизнес-логика в services/payroll_service.py.
Доступ: page-гейт «salary» (viewer на чтение, editor на мутации; тариф —
только admin/owner: лестница глобальная, одна на все проекты).

Инвалидация (iron rule 7): мутации, влияющие на начисления (сотрудники,
команды, скоупы, участники), гасят payroll:sheet + payroll:accruals +
reports:opiu проекта; отметка выплаты — только payroll:sheet (на начисления
не влияет); замена тарифа — все три префикса ГЛОБАЛЬНО.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.rbac import require_role
from backend.schemas.payroll import (
    EmployeeIn,
    EmployeeListResponse,
    EmployeeResponse,
    EmployeeUpdate,
    OkResponse,
    PayoutMarkIn,
    PayrollSheetResponse,
    ScopeOptionsResponse,
    TariffReplace,
    TariffResponse,
    TeamIn,
    TeamListResponse,
    TeamMembersReplace,
    TeamResponse,
    TeamScopesReplace,
    TeamUpdate,
)
from backend.services import payroll_service
from backend.services.payroll_service import PayrollNotFoundError
from backend.utils.rate_limit import rate_limit_write
from backend.utils.time import utcnow

router = APIRouter(prefix="/payroll", tags=["Payroll"])

_read = Depends(require_role("viewer", page="salary"))
_write = [Depends(require_role("editor", page="salary")), Depends(rate_limit_write)]
# Тариф глобальный (одна лестница на ВСЕ проекты) — менять может только admin/owner.
_write_admin = [Depends(require_role("admin", page="salary")), Depends(rate_limit_write)]

# Месяц 'YYYY-MM' с валидным номером месяца — мусор режется 422 ещё на Query.
_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"

_ACCRUAL_PREFIXES = ("payroll:sheet", "payroll:accruals", "reports:opiu")


async def _invalidate_marks(project_id: int) -> None:
    """Отметка выплаты меняет только ведомость (начисления не трогает)."""
    await invalidate_cache(f"payroll:sheet:project_id={project_id}")


async def _invalidate_accruals(project_id: int) -> None:
    """Мутация, влияющая на начисления: ведомость + помесячный ФОТ + ОПиУ."""
    for prefix in _ACCRUAL_PREFIXES:
        await invalidate_cache(f"{prefix}:project_id={project_id}")


async def _invalidate_accruals_global() -> None:
    """Тариф глобальный — гасим ведомости/начисления/ОПиУ всех проектов."""
    for prefix in _ACCRUAL_PREFIXES:
        await invalidate_cache(prefix)


# ─── Сотрудники ──────────────────────────────────────────────────────────────


@router.get(
    "/employees",
    response_model=EmployeeListResponse,
    summary="Список сотрудников",
    dependencies=[_read],
)
async def list_employees(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сотрудники проекта с именем контрагента и командами."""
    return await payroll_service.list_employees(db, project.id)


@router.post(
    "/employees",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать сотрудника",
    dependencies=_write,
)
async def create_employee(
    body: EmployeeIn,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать сотрудника."""
    try:
        result = await payroll_service.create_employee(db, project.id, body)
    except PayrollNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    await _invalidate_accruals(project.id)
    return result


@router.patch(
    "/employees/{employee_id}",
    response_model=EmployeeResponse,
    summary="Изменить сотрудника",
    dependencies=_write,
)
async def update_employee(
    employee_id: int,
    body: EmployeeUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Частичное обновление; clear_* флаги — явное обнуление полей."""
    try:
        result = await payroll_service.update_employee(db, project.id, employee_id, body)
    except PayrollNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    await _invalidate_accruals(project.id)
    return result


@router.delete(
    "/employees/{employee_id}",
    response_model=OkResponse,
    summary="Удалить сотрудника",
    dependencies=_write,
)
async def delete_employee(
    employee_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete сотрудника (членства в командах удаляются)."""
    try:
        result = await payroll_service.delete_employee(db, project.id, employee_id)
    except PayrollNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    await _invalidate_accruals(project.id)
    return result


# ─── Команды ─────────────────────────────────────────────────────────────────


@router.get(
    "/teams",
    response_model=TeamListResponse,
    summary="Список команд",
    dependencies=[_read],
)
async def list_teams(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Команды проекта со скоупами и участниками."""
    return await payroll_service.list_teams(db, project.id)


@router.post(
    "/teams",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Создать команду",
    dependencies=_write,
)
async def create_team(
    body: TeamIn,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать команду."""
    result = await payroll_service.create_team(db, project.id, body)
    await _invalidate_accruals(project.id)
    return result


@router.patch(
    "/teams/{team_id}",
    response_model=TeamResponse,
    summary="Изменить команду",
    dependencies=_write,
)
async def update_team(
    team_id: int,
    body: TeamUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Переименовать команду / включить-выключить."""
    try:
        result = await payroll_service.update_team(db, project.id, team_id, body)
    except PayrollNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    await _invalidate_accruals(project.id)
    return result


@router.delete(
    "/teams/{team_id}",
    response_model=OkResponse,
    summary="Удалить команду",
    dependencies=_write,
)
async def delete_team(
    team_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Soft-delete команды."""
    try:
        result = await payroll_service.delete_team(db, project.id, team_id)
    except PayrollNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    await _invalidate_accruals(project.id)
    return result


@router.put(
    "/teams/{team_id}/scopes",
    response_model=TeamResponse,
    summary="Заменить скоупы команды",
    dependencies=_write,
)
async def replace_team_scopes(
    team_id: int,
    body: TeamScopesReplace,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Полная замена скоупов (брендов/категорий) команды."""
    try:
        result = await payroll_service.replace_team_scopes(db, project.id, team_id, body)
    except PayrollNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    await _invalidate_accruals(project.id)
    return result


@router.put(
    "/teams/{team_id}/members",
    response_model=TeamResponse,
    summary="Заменить участников команды",
    dependencies=_write,
)
async def replace_team_members(
    team_id: int,
    body: TeamMembersReplace,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Полная замена участников команды (все id — сотрудники этого проекта)."""
    try:
        result = await payroll_service.replace_team_members(db, project.id, team_id, body)
    except PayrollNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    await _invalidate_accruals(project.id)
    return result


# ─── Справочники ─────────────────────────────────────────────────────────────


@router.get(
    "/scope-options",
    response_model=ScopeOptionsResponse,
    summary="Доступные бренды и категории",
    dependencies=[_read],
)
async def scope_options(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Доступные бренды/категории из wb_finance_rows проекта (кэш 1 час)."""
    return await payroll_service.get_scope_options(db, project.id)


@router.get(
    "/tariff",
    response_model=TariffResponse,
    summary="Действующая тарифная лестница",
    dependencies=[_read],
)
async def get_tariff(
    on_date: date | None = Query(None, description="Дата, на которую нужен действующий набор"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Действующий набор ступеней лестницы (лестница глобальная)."""
    return await payroll_service.get_tariff(db, on_date or utcnow().date())


@router.put(
    "/tariff",
    response_model=TariffResponse,
    summary="Заменить набор ступеней (admin)",
    dependencies=_write_admin,
)
async def replace_tariff(
    body: TariffReplace,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """
    Полная замена набора ступеней для valid_from.

    В ответе — ЗАПИСАННЫЙ набор (для будущего valid_from он ещё не действующий);
    актуальную лестницу фронт перечитывает через GET /payroll/tariff.
    Лестница глобальная — доступ только admin/owner, кэши гасятся у всех проектов.
    """
    result = await payroll_service.replace_tariff(db, body)
    await _invalidate_accruals_global()
    return result


# ─── Ведомость и отметки выплат ──────────────────────────────────────────────


@router.get(
    "/sheet",
    response_model=PayrollSheetResponse,
    summary="Месячная ведомость",
    dependencies=[_read],
)
async def get_sheet(
    month: str = Query(..., pattern=_MONTH_PATTERN, description="Расчётный месяц YYYY-MM"),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Месячная ведомость: начисления команд по неделям, сотрудники, выплаты."""
    try:
        return await payroll_service.get_sheet(db, project.id, month)
    except ValueError as e:
        # Дефенс: сюда не дойти (месяц отвалидирован Query-паттерном выше)
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.put(
    "/payout-mark",
    response_model=OkResponse,
    summary="Отметка «выплачено»",
    dependencies=_write,
)
async def upsert_payout_mark(
    body: PayoutMarkIn,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upsert отметки «выплачено» по (сотрудник, месяц, день выплаты)."""
    try:
        result = await payroll_service.upsert_payout_mark(db, project.id, body)
    except PayrollNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    await _invalidate_marks(project.id)
    return result
