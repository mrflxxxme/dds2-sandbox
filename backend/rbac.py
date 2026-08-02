"""
RBAC (Role-Based Access Control) — dependency module for DDS2.

Roles: owner > admin > editor > viewer
Pages: granular page-level access for editor/viewer roles.
"""

import json
import logging
from datetime import date, datetime

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models.auth import Project, ProjectMember, User
from backend.project_context import get_current_project

logger = logging.getLogger(__name__)

ROLE_HIERARCHY: dict[str, int] = {
    "owner": 4,
    "admin": 3,
    "editor": 2,
    "viewer": 1,
    # External fulfillment operator (Хамза). Below viewer on purpose: even if a
    # `require_role` guard is ever added to a main endpoint, an FF user fails it.
    # Real isolation is enforced by the external-user middleware (only /ff/* allowed).
    "fulfillment": 0,
}

ALL_PAGES: list[str] = [
    "dashboard",
    "import",
    "txn",
    "inbox",
    "reports",
    "cost",
    "refs",
    "salary",
    "warehouse",
    "assembly",
    "assembly-analytics",
    "logistics",
    "fbo",
    "stocks",
    "stock-analytics",
    "measurements",
    "barcode-labels",
    "fbs",
    "planning",
    "container",
    "funnel",
    "reviews",
    "card-exchange",
    "ads-manager",
    "ab-tests",
    "trends",
    "opiu",
    "plan-fact",
    "geography",
    "monitoring",
    "raw-data",
    "project-settings",
    "team",
    "ai-chat",
    "supply-chain",
]

# Дата появления ключа в ALL_PAGES (по истории этого файла). Отличает «раздела не
# существовало, когда владелец настраивал доступ» от «владелец снял галочку» —
# наследуется только первое, см. inherited_pages().
# Новый раздел → добавить дату здесь, иначе он не доедет до editor/viewer
# (гард — tests/test_rbac_page_inheritance.py).
PAGE_ADDED_AT: dict[str, date] = {
    "dashboard": date(2026, 3, 26),
    "import": date(2026, 3, 26),
    "txn": date(2026, 3, 26),
    "inbox": date(2026, 3, 26),
    "reports": date(2026, 3, 26),
    "cost": date(2026, 3, 26),
    "refs": date(2026, 3, 26),
    "assembly": date(2026, 3, 26),
    "logistics": date(2026, 3, 26),
    "fbo": date(2026, 3, 26),
    "stocks": date(2026, 3, 26),
    "stock-analytics": date(2026, 3, 26),
    "planning": date(2026, 3, 26),
    "container": date(2026, 3, 26),
    "funnel": date(2026, 3, 26),
    "trends": date(2026, 3, 26),
    "opiu": date(2026, 3, 26),
    "plan-fact": date(2026, 3, 26),
    "geography": date(2026, 3, 26),
    "monitoring": date(2026, 3, 26),
    "project-settings": date(2026, 3, 26),
    "team": date(2026, 3, 26),
    "ai-chat": date(2026, 4, 7),
    "supply-chain": date(2026, 4, 9),
    "assembly-analytics": date(2026, 6, 14),
    "raw-data": date(2026, 7, 12),
    "barcode-labels": date(2026, 7, 23),
    "ads-manager": date(2026, 7, 23),
    "ab-tests": date(2026, 7, 23),
    "fbs": date(2026, 7, 26),
    "salary": date(2026, 7, 28),
    # Были в меню, но не в каталоге: `canAccess` по ключу вне ALL_PAGES всегда
    # ложь, то есть разделы были скрыты от editor/viewer всегда и выдать их было
    # нечем. Дата — день попадания в каталог, чтобы они разъехались по командам.
    "warehouse": date(2026, 7, 30),
    "measurements": date(2026, 7, 30),
    "reviews": date(2026, 7, 30),
    # Раздел приехал из dev вместе с биржей карточек, но в каталог его не
    # внесли — `canAccess` по ключу вне ALL_PAGES всегда ложь, то есть для
    # editor/viewer «Биржа карточек» была скрыта навсегда (тот же класс дефекта,
    # что у warehouse/measurements/reviews выше).
    "card-exchange": date(2026, 8, 2),
}

# День, когда каталог страниц вообще появился (вместе с RBAC). Гранты старше этой
# даты выдавались против начального набора — см. inherited_pages().
CATALOG_BIRTH: date = min(PAGE_ADDED_AT.values())

# Разделы, которые не наследуются никогда: зарплата, сырые строки БД и
# администрирование проекта выдаются только явной галочкой владельца.
PAGES_NEVER_INHERITED: frozenset[str] = frozenset(
    {
        "salary",
        "raw-data",
        "project-settings",
        "team",
    }
)

# Наследование — ОПТ-ИН: раздел разъезжается по командам только если он здесь.
# Полярность важна: забыть ключ здесь = раздел не распространяется сам (мелкое
# неудобство, ровно то, что эта фича лечит), а забыть его в чёрном списке =
# тихо раздать финансовый раздел всем, у кого открыта секция. Партицию
# ALL_PAGES на эти два множества проверяет тест, поэтому «забыть» — это красный
# CI, а не молчаливое раскрытие данных.
PAGES_INHERITABLE: frozenset[str] = frozenset(
    {
        "dashboard",
        "import",
        "txn",
        "inbox",
        "reports",
        "cost",
        "refs",
        "warehouse",
        "assembly",
        "assembly-analytics",
        "logistics",
        "fbo",
        "stocks",
        "stock-analytics",
        "measurements",
        "barcode-labels",
        "fbs",
        "planning",
        "container",
        "funnel",
        "reviews",
        "card-exchange",
        "ads-manager",
        "ab-tests",
        "trends",
        "opiu",
        "plan-fact",
        "geography",
        "monitoring",
        "ai-chat",
        "supply-chain",
    }
)

SECTION_PAGES: dict[str, list[str]] = {
    "finance": ["import", "txn", "inbox", "reports", "cost", "refs", "salary"],
    "warehouse": [
        "warehouse",
        "assembly",
        "assembly-analytics",
        "logistics",
        "fbo",
        "fbs",
        "stocks",
        "stock-analytics",
        "measurements",
        "barcode-labels",
    ],
    "orders": ["planning", "container"],
    "supply": ["supply-chain"],
    "sales": ["funnel", "reviews", "card-exchange", "ads-manager", "ab-tests", "trends", "opiu", "plan-fact", "geography"],
    "ai": ["ai-chat"],
    "settings": ["monitoring", "raw-data", "project-settings", "team"],
}


def parse_pages(pages_json: str | None) -> list[str]:
    """Parse pages JSON string to list, returning empty list on None/invalid."""
    if not pages_json:
        return []
    try:
        result = json.loads(pages_json)
        if isinstance(result, list):
            return result
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def _page_section(page: str) -> str | None:
    """Return the section a page belongs to, or None for section-less pages."""
    for section, pages in SECTION_PAGES.items():
        if page in pages:
            return section
    return None


def inherited_pages(explicit: list[str], configured_at: datetime | None) -> list[str]:
    """Разделы, появившиеся в каталоге ПОСЛЕ последней настройки доступов участника.

    Выдаются двумя путями и только для PAGES_INHERITABLE:
    * внутри секции, к которой доступ уже есть («кладовщик получает новый склад»);
    * целиком, если на момент настройки участнику выдали ВСЁ, что тогда было
      («полный доступ» не должен ветшать; иначе секции из одной страницы —
      supply-chain, ai-chat — не достаются никому никогда).

    Разделы, существовавшие на момент настройки, не трогаем: их отсутствие —
    решение владельца, а не дырка в снимке.
    """
    if not explicit or configured_at is None:
        return []

    held = set(explicit)
    held_sections = {section for section, pages in SECTION_PAGES.items() if held & set(pages)}
    # Грант, сделанный до появления каталога, всё равно выдавался против
    # начального набора страниц — иначе «до RBAC» значило бы «каталог пуст, что
    # ни выдай — полный доступ».
    cutoff = max(configured_at.date(), CATALOG_BIRTH)
    grantable_then = {p for p, added_at in PAGE_ADDED_AT.items() if added_at <= cutoff} & PAGES_INHERITABLE
    # Известное ограничение: вывод раздела ИЗ каталога сужает grantable_then, то
    # есть участник с «всё кроме выведенного» становится blanket. Страницы
    # выводят крайне редко и только правкой этого файла — принято осознанно.
    blanket = bool(grantable_then) and grantable_then <= held

    result: list[str] = []
    for page in ALL_PAGES:
        if page in held or page not in PAGES_INHERITABLE:
            continue
        added_at = PAGE_ADDED_AT.get(page)
        if added_at is None or added_at <= cutoff:
            continue
        if blanket or _page_section(page) in held_sections:
            result.append(page)
    return result


def get_effective_pages(role: str, pages_json: str | None, configured_at: datetime | None = None) -> list[str]:
    """Return effective page list based on role. Owner/admin get all pages.

    `configured_at` — `ProjectMember.pages_updated_at`, водяной знак каталога на
    момент последней настройки доступов. Без него наследование выключено.
    """
    if role in ("owner", "admin"):
        return list(ALL_PAGES)
    explicit = parse_pages(pages_json)
    return explicit + inherited_pages(explicit, configured_at)


def require_role(min_role: str = "viewer", page: str | None = None):
    """FastAPI dependency factory — checks role hierarchy + page access."""

    async def dependency(
        project: Project = Depends(get_current_project),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        member = (
            await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project.id,
                    ProjectMember.user_id == user.id,
                    ProjectMember.is_deleted == False,  # noqa: E712
                )
            )
        ).scalar_one_or_none()

        if not member:
            raise HTTPException(403, "Нет доступа к проекту")

        role_level = ROLE_HIERARCHY.get(member.role, 0)
        required_level = ROLE_HIERARCHY.get(min_role, 0)
        if role_level < required_level:
            raise HTTPException(403, f"Требуется роль {min_role} или выше")

        if page and member.role in ("editor", "viewer"):
            member_pages = get_effective_pages(member.role, member.pages, member.pages_updated_at)
            if page not in member_pages:
                raise HTTPException(403, f"Нет доступа к странице: {page}")

        return user

    return dependency
