# ruff: noqa: RUF002, RUF003
"""
Лендер-портал: контекст внешнего заёмщика (кредитора).

Зеркало `ff_context.py`, но скоуп — по контрагентам (а не складам). Заёмщик видит
ТОЛЬКО займы своих контрагент-сущностей (физ/ИП). Скоуп задаётся:
  - членство с ролью 'lender' в проекте  → какие проекты;
  - ProjectSetting ключ `lender_counterparties_{user_id}` (JSON list) → какие
    контрагенты (cp_id) в проекте принадлежат этому заёмщику.

Изоляция от внутренней аналитики — на уровне middleware (внешний юзер допускается
только к /api/v1/lender/* и /api/v1/ff/*); здесь — скоуп по контрагентам.
"""

import json
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models.auth import Project, ProjectMember, User
from backend.models.counterparty import Counterparty
from backend.models.refs import ProjectSetting

LENDER_ROLE = "lender"
LENDER_CP_KEY_PREFIX = "lender_counterparties_"


def lender_counterparties_key(user_id: int) -> str:
    """ProjectSetting key holding the list of counterparty ids a lender owns in a project."""
    return f"{LENDER_CP_KEY_PREFIX}{user_id}"


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
class LenderContext:
    """Resolved lender scope for the current request."""

    user: User
    projects: dict[int, Project] = field(default_factory=dict)  # project_id -> Project
    counterparties: dict[int, list[int]] = field(default_factory=dict)  # project_id -> [cp_id]
    counterparty_by_id: dict[int, Counterparty] = field(default_factory=dict)  # cp_id -> Counterparty

    @property
    def project_ids(self) -> list[int]:
        return list(self.projects.keys())

    @property
    def counterparty_ids(self) -> list[int]:
        return [cid for ids in self.counterparties.values() for cid in ids]

    def allows(self, project_id: int, counterparty_id: int) -> bool:
        return counterparty_id in self.counterparties.get(project_id, [])


async def get_lender_context(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LenderContext:
    """Resolve the lender's cross-project counterparty scope. 403 for non-lenders."""
    if not user.is_external:
        raise HTTPException(403, "Доступ только для аккаунтов заёмщиков")

    proj_rows = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == user.id,
            ProjectMember.role == LENDER_ROLE,
            ProjectMember.is_deleted == False,  # noqa: E712
            Project.is_deleted == False,  # noqa: E712
        )
        .order_by(Project.id)
    )
    projects = {p.id: p for p in proj_rows.scalars().all()}
    if not projects:
        raise HTTPException(403, "Не найден доступ заёмщика для этого аккаунта")

    key = lender_counterparties_key(user.id)
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

    # Validate: keep only counterparties that actually belong to the project.
    counterparty_by_id: dict[int, Counterparty] = {}
    if all_ids:
        cp_rows = await db.execute(
            select(Counterparty).where(
                Counterparty.id.in_(all_ids),
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
        for cp in cp_rows.scalars().all():
            counterparty_by_id[cp.id] = cp

    counterparties: dict[int, list[int]] = {}
    for pid, ids in configured.items():
        counterparties[pid] = [
            cid for cid in ids if cid in counterparty_by_id and counterparty_by_id[cid].project_id == pid
        ]

    return LenderContext(
        user=user, projects=projects, counterparties=counterparties, counterparty_by_id=counterparty_by_id
    )
