# ruff: noqa: RUF002, RUF003
"""
FF-портал: контекст внешнего фулфилмент-оператора (Хамза).

В отличие от `get_current_project` (один проект на запрос), FF-эндпоинты КРОСС-проектные:
оператор видит свои склады сразу в нескольких проектах (default + вяткин). Скоуп задаётся:
  - членство с ролью 'fulfillment' в проекте  → какие проекты;
  - ProjectSetting ключ `ff_warehouses_{user_id}` (JSON list) → какие склады в проекте.

Изоляция данных от внутренней аналитики/себестоимости — на уровне middleware
(внешний юзер допускается только к /api/v1/ff/*), здесь — скоуп по складам.
"""

import json
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models.auth import Project, ProjectMember, User
from backend.models.refs import ProjectSetting
from backend.models.warehouse import Warehouse, WarehouseType

FF_ROLE = "fulfillment"


def ff_warehouses_key(user_id: int) -> str:
    """ProjectSetting key holding the list of warehouse ids an FF user manages in a project."""
    return f"ff_warehouses_{user_id}"


def _parse_id_list(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    out: list[int] = []
    for v in parsed:
        try:
            out.append(int(v))
        except (TypeError, ValueError):
            continue
    return out


@dataclass
class FfContext:
    """Resolved FF scope for the current request."""

    user: User
    projects: dict[int, Project] = field(default_factory=dict)  # project_id -> Project (slug/name)
    warehouses: dict[int, list[int]] = field(default_factory=dict)  # project_id -> [warehouse_id]
    warehouse_by_id: dict[int, Warehouse] = field(default_factory=dict)  # warehouse_id -> Warehouse

    @property
    def project_ids(self) -> list[int]:
        return list(self.projects.keys())

    @property
    def warehouse_ids(self) -> list[int]:
        return [wid for ids in self.warehouses.values() for wid in ids]

    def pairs(self) -> list[tuple[int, int]]:
        return [(pid, wid) for pid, ids in self.warehouses.items() for wid in ids]

    def allows(self, project_id: int, warehouse_id: int) -> bool:
        return warehouse_id in self.warehouses.get(project_id, [])

    def allows_any_project(self, warehouse_id: int) -> bool:
        return warehouse_id in self.warehouse_ids


async def get_ff_context(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FfContext:
    """Resolve the FF operator's cross-project warehouse scope. 403 for non-FF users."""
    if not user.is_external:
        raise HTTPException(403, "Доступ только для аккаунтов фулфилмента")

    proj_rows = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == user.id,
            ProjectMember.role == FF_ROLE,
            ProjectMember.is_deleted == False,  # noqa: E712
            Project.is_deleted == False,  # noqa: E712
        )
        .order_by(Project.id)
    )
    projects = {p.id: p for p in proj_rows.scalars().all()}
    if not projects:
        raise HTTPException(403, "Не найдены проекты фулфилмента для этого аккаунта")

    # Configured warehouse ids per project (from ProjectSetting) — one batched query.
    key = ff_warehouses_key(user.id)
    setting_rows = await db.execute(
        select(ProjectSetting.project_id, ProjectSetting.value).where(
            ProjectSetting.project_id.in_(list(projects)),
            ProjectSetting.key == key,
        )
    )
    raw_by_pid = {pid: val for pid, val in setting_rows.all()}
    configured: dict[int, list[int]] = {}
    all_ids: set[int] = set()
    for pid in projects:
        ids = _parse_id_list(raw_by_pid.get(pid))
        configured[pid] = ids
        all_ids.update(ids)

    # Validate: keep only FULFILLMENT warehouses that actually belong to the project.
    warehouse_by_id: dict[int, Warehouse] = {}
    if all_ids:
        wh_rows = await db.execute(
            select(Warehouse).where(
                Warehouse.id.in_(all_ids),
                Warehouse.is_deleted == False,  # noqa: E712
                Warehouse.warehouse_type == WarehouseType.FULFILLMENT.value,
            )
        )
        for wh in wh_rows.scalars().all():
            warehouse_by_id[wh.id] = wh

    warehouses: dict[int, list[int]] = {}
    for pid, ids in configured.items():
        warehouses[pid] = [wid for wid in ids if wid in warehouse_by_id and warehouse_by_id[wid].project_id == pid]

    return FfContext(user=user, projects=projects, warehouses=warehouses, warehouse_by_id=warehouse_by_id)


async def warehouse_has_ff_operator(db: AsyncSession, project_id: int, warehouse_id: int) -> bool:
    """True if `warehouse_id` is managed by at least one FF portal operator in `project_id`.

    Used by assembly returns: a return to a portal-operated FF warehouse is created
    as EXPECTED (pending the operator's acceptance) instead of auto-ACCEPTED.
    """
    rows = await db.execute(
        select(ProjectSetting.value).where(
            ProjectSetting.project_id == project_id,
            ProjectSetting.key.like("ff_warehouses_%"),
        )
    )
    for (raw,) in rows.all():
        if warehouse_id in _parse_id_list(raw):
            return True
    return False
