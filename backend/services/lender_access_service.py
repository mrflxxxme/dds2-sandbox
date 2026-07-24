# ruff: noqa: RUF002, RUF003
"""
Выдача доступа заёмщику в лендер-портал (admin-операция).

Создаёт внешнего пользователя (is_external), членство role='lender' и
ProjectSetting `lender_counterparties_{user_id}` со списком его контрагентов.
Зеркало схемы ФФ-оператора, но скоуп — по контрагентам.
"""

from __future__ import annotations

import json
import secrets

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import hash_password
from backend.cache import invalidate_project_reports
from backend.lender_context import LENDER_ROLE, lender_counterparties_key
from backend.models.auth import ProjectMember, User
from backend.models.counterparty import Counterparty
from backend.models.refs import ProjectSetting
from backend.schemas.loan import LenderAccessCreate, LenderAccessCreated, LenderAccessInfo

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def _translit(name: str) -> str:
    out = []
    for ch in name.lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalnum() and ch.isascii():
            out.append(ch)
        else:
            out.append("")
    slug = "".join(out)
    return slug or "lender"


async def _unique_username(db: AsyncSession, base: str) -> str:
    candidate = base
    n = 1
    while True:
        exists = (await db.execute(select(User.id).where(User.username == candidate))).scalar_one_or_none()
        if exists is None:
            return candidate
        n += 1
        candidate = f"{base}{n}"


async def _cp_to_user_map(db: AsyncSession, project_id: int) -> dict[int, int]:
    """Map counterparty_id -> lender user_id from ProjectSetting scope lists."""
    rows = await db.execute(
        select(ProjectSetting.key, ProjectSetting.value).where(
            ProjectSetting.project_id == project_id,
            ProjectSetting.key.like("lender_counterparties_%"),
        )
    )
    out: dict[int, int] = {}
    for key, raw in rows.all():
        try:
            user_id = int(key.rsplit("_", 1)[-1])
            parsed = json.loads(raw) if raw else []
        except (ValueError, TypeError):
            continue
        for cid in parsed if isinstance(parsed, list) else []:
            try:
                out[int(cid)] = user_id
            except (ValueError, TypeError):
                continue
    return out


async def list_access(db: AsyncSession, project_id: int) -> list[LenderAccessInfo]:
    """Access info for every counterparty that has a lender login in this project."""
    cp_to_user = await _cp_to_user_map(db, project_id)
    if not cp_to_user:
        return []
    user_rows = await db.execute(
        select(User.id, User.username, User.is_active, User.created_at).where(User.id.in_(list(cp_to_user.values())))
    )
    user_map = {u.id: u for u in user_rows.all()}
    cp_rows = await db.execute(
        select(Counterparty.id, Counterparty.name).where(Counterparty.id.in_(list(cp_to_user)))
    )
    cp_names = {c.id: c.name for c in cp_rows.all()}

    out: list[LenderAccessInfo] = []
    for cp_id, user_id in cp_to_user.items():
        u = user_map.get(user_id)
        out.append(
            LenderAccessInfo(
                counterparty_id=cp_id,
                counterparty_name=cp_names.get(cp_id, f"#{cp_id}"),
                user_id=user_id,
                username=u.username if u else None,
                is_active=bool(u.is_active) if u else False,
                created_at=u.created_at if u else None,
            )
        )
    out.sort(key=lambda x: x.counterparty_name.lower())
    return out


async def create_access(db: AsyncSession, project_id: int, data: LenderAccessCreate) -> LenderAccessCreated:
    cp = (
        await db.execute(
            select(Counterparty).where(
                Counterparty.id == data.counterparty_id,
                Counterparty.project_id == project_id,
                Counterparty.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    if cp is None:
        raise HTTPException(404, "Контрагент не найден в проекте")

    existing = await _cp_to_user_map(db, project_id)
    if data.counterparty_id in existing:
        raise HTTPException(409, "У заёмщика уже есть доступ — используйте сброс пароля")

    username = (data.username or "").strip() or await _unique_username(db, _translit(cp.name))
    # explicit username must still be globally unique
    if data.username:
        clash = (await db.execute(select(User.id).where(User.username == username))).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(409, f"Логин «{username}» уже занят")

    password = data.password or secrets.token_urlsafe(9)
    user = User(
        username=username,
        password_hash=hash_password(password),
        is_active=True,
        is_external=True,
    )
    db.add(user)
    await db.flush()

    db.add(ProjectMember(project_id=project_id, user_id=user.id, role=LENDER_ROLE))
    db.add(
        ProjectSetting(
            project_id=project_id,
            key=lender_counterparties_key(user.id),
            value=json.dumps([data.counterparty_id]),
        )
    )
    await db.commit()
    await db.refresh(user)
    await invalidate_project_reports(project_id)
    return LenderAccessCreated(
        counterparty_id=data.counterparty_id,
        counterparty_name=cp.name,
        user_id=user.id,
        username=user.username,
        is_active=user.is_active,
        created_at=user.created_at,
        password=password,
    )


async def reset_password(db: AsyncSession, project_id: int, counterparty_id: int) -> LenderAccessCreated:
    cp_to_user = await _cp_to_user_map(db, project_id)
    user_id = cp_to_user.get(counterparty_id)
    if user_id is None:
        raise HTTPException(404, "Доступ для заёмщика не найден")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(404, "Пользователь не найден")
    cp = (await db.execute(select(Counterparty).where(Counterparty.id == counterparty_id))).scalar_one_or_none()
    password = secrets.token_urlsafe(9)
    user.password_hash = hash_password(password)
    user.is_active = True
    await db.commit()
    return LenderAccessCreated(
        counterparty_id=counterparty_id,
        counterparty_name=cp.name if cp else f"#{counterparty_id}",
        user_id=user.id,
        username=user.username,
        is_active=user.is_active,
        created_at=user.created_at,
        password=password,
    )


async def revoke_access(db: AsyncSession, project_id: int, counterparty_id: int) -> LenderAccessInfo:
    cp_to_user = await _cp_to_user_map(db, project_id)
    user_id = cp_to_user.get(counterparty_id)
    if user_id is None:
        raise HTTPException(404, "Доступ для заёмщика не найден")
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    cp = (await db.execute(select(Counterparty).where(Counterparty.id == counterparty_id))).scalar_one_or_none()
    if user is not None:
        user.is_active = False
        await db.commit()
        await invalidate_project_reports(project_id)
    return LenderAccessInfo(
        counterparty_id=counterparty_id,
        counterparty_name=cp.name if cp else f"#{counterparty_id}",
        user_id=user_id,
        username=user.username if user else None,
        is_active=False,
    )
