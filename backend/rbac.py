"""
RBAC (Role-Based Access Control) — dependency module for DDS2.

Roles: owner > admin > editor > viewer
Pages: granular page-level access for editor/viewer roles.
"""

import json
import logging
from collections.abc import Mapping
from datetime import date, datetime

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models.auth import Project, ProjectMember, User
from backend.project_context import get_current_project

logger = logging.getLogger(__name__)

# Ключ каталога или несколько ключей («достаточно любого»), см. `_check_access`.
PageKey = str | tuple[str, ...]

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


def _as_pages(page: PageKey | None) -> tuple[str, ...]:
    """Ключ страницы или набор ключей → кортеж. Пустой кортеж = гейта по ключу нет."""
    if not page:
        return ()
    return (page,) if isinstance(page, str) else tuple(page)


async def _check_access(
    db: AsyncSession,
    project: Project,
    user: User,
    min_role: str,
    page: PageKey | None,
) -> User:
    """Общее ядро проверки: членство в проекте, уровень роли, ключ страницы.

    `page` — ключ каталога или НЕСКОЛЬКО ключей. Несколько означает «любого
    достаточно»: одну и ту же ручку читают разные разделы (напр. `GET
    /funnel/data` открыт с «Воронки», «Управления рекламой» и «Индекса
    локализации»), и требовать пересечения значило бы сломать две страницы из трёх.
    """
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

    wanted = _as_pages(page)
    if wanted and member.role in ("editor", "viewer"):
        member_pages = set(get_effective_pages(member.role, member.pages, member.pages_updated_at))
        if not member_pages.intersection(wanted):
            raise HTTPException(403, f"Нет доступа к странице: {' / '.join(wanted)}")

    return user


def require_role(min_role: str = "viewer", page: PageKey | None = None):
    """FastAPI dependency factory — checks role hierarchy + page access."""

    async def dependency(
        project: Project = Depends(get_current_project),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        return await _check_access(db, project, user, min_role, page)

    return dependency


# HTTP-методы, которые считаем чтением. Всё остальное — мутация.
READ_METHODS: frozenset[str] = frozenset({"GET", "HEAD", "OPTIONS"})


def _route_path_of(request: Request) -> str:
    """Шаблон пути ручки (`/api/v1/funnel/campaigns/{campaign_id}/bid`).

    Именно шаблон, а не `request.url.path`: по нему ищут раскладку ключей и
    исключения `read_paths`, а конкретный id в пути для этого не нужен.
    """
    route = request.scope.get("route")
    return getattr(route, "path", "") or request.url.path


def _is_read(method: str, route_path: str, read_paths: frozenset[str]) -> bool:
    """Чтение — по методу или по явному исключению для POST-ради-тела."""
    return method.upper() in READ_METHODS or any(route_path.endswith(s) for s in read_paths)


def require_page(
    page: PageKey,
    read_role: str = "viewer",
    write_role: str = "editor",
    read_paths: frozenset[str] = frozenset(),
):
    """Гейт уровня РОУТЕРА: ключ страницы + роль, выбранная по методу запроса.

    Чем отличается от `require_role`, который вешают на отдельную ручку:

    * вешается один раз в `APIRouter(dependencies=[...])` и закрывает ВСЕ ручки
      роутера, включая те, которые допишут завтра. Это fail-closed: новая ручка
      защищена по умолчанию, а не «пока кто-нибудь не вспомнит про гейт» — ровно
      тот класс дефекта, из-за которого домен АБ-тестов уехал в прод открытым;
    * роль зависит от метода (GET/HEAD — `read_role`, мутации — `write_role`),
      поэтому одного гейта хватает и на чтение, и на запись, и в БД по-прежнему
      уходит ОДИН запрос за участником, а не два (как было бы при паре
      «router-level viewer + route-level editor»).

    `read_paths` — суффиксы путей, которые считаются чтением вопреки методу. Это
    для POST-ради-тела: поиск/витрина, где POST выбран из-за размера фильтра, а
    не потому, что что-то меняется (`/card-exchange/showcase`). Без такого списка
    viewer с ключом раздела получил бы 403 на основном чтении страницы.

    `require_role` остаётся для точечных случаев: ручка со своей ролью
    (`payment-requests/approve` — только admin) или роутер, где ручки живут на
    разных ключах каталога.
    """

    async def dependency(
        request: Request,
        project: Project = Depends(get_current_project),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        is_read = _is_read(request.method, _route_path_of(request), read_paths)
        return await _check_access(db, project, user, read_role if is_read else write_role, page)

    return dependency


def require_page_map(
    pages_by_route: Mapping[str, PageKey],
    default: PageKey,
    read_role: str = "viewer",
    write_role: str = "editor",
    read_paths: frozenset[str] = frozenset(),
):
    """То же, что `require_page`, но для роутера, ручки которого сидят на РАЗНЫХ ключах.

    Зачем отдельная фабрика. `/funnel` — один роутер на 91 ручку и СЕМЬ разделов
    каталога («Воронка», «Управление рекламой», «Метрики и тренды», «Индекс
    локализации», «Себестоимость», «Настройка проекта», «АБ-тесты»): страницы
    исторически ходят в один и тот же аналитический бэкенд. Одним ключом такой
    роутер не закрыть, а вешать `require_role` на каждую ручку — не fail-closed:
    ручка, дописанная завтра, снова окажется открытой (ровно так домен АБ-тестов
    уехал в прод без гейта).

    Здесь гейт по-прежнему ОДИН на `APIRouter`, а ключ выбирается по шаблону
    пути. В БД уходит один запрос за участником — как и у `require_page`.

    `pages_by_route` — ПОЛНЫЙ шаблон пути (`/api/v1/funnel/campaigns/{campaign_id}/bid`)
    → ключ или кортеж ключей. `default` — ключ для пути, которого в раскладке
    нет: новая ручка остаётся закрытой (fail-closed), пусть и не тем ключом,
    какой ей нужен. Что раскладка полная, проверяет гард-тест
    `test_funnel_page_map_covers_every_route`, так что `default` — страховка, а
    не рабочий режим.
    """

    async def dependency(
        request: Request,
        project: Project = Depends(get_current_project),
        user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> User:
        route_path = _route_path_of(request)
        page = pages_by_route.get(route_path, default)
        is_read = _is_read(request.method, route_path, read_paths)
        return await _check_access(db, project, user, read_role if is_read else write_role, page)

    return dependency
