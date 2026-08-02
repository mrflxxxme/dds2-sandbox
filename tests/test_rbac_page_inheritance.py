"""
RBAC: наследование новых разделов для editor/viewer.

Список страниц у editor/viewer — снимок, сделанный в момент настройки доступа.
Раздел, добавленный в каталог ПОЗЖЕ этого момента, участнику доставаться должен
(иначе каждый новый раздел приходится вручную раздавать всей команде), а раздел,
который существовал на момент настройки и остался неотмеченным, — нет (это
осознанное решение владельца).
"""

from datetime import date, datetime

import pytest
from sqlalchemy import text

from backend.rbac import (
    ALL_PAGES,
    CATALOG_BIRTH,
    PAGE_ADDED_AT,
    PAGES_INHERITABLE,
    PAGES_NEVER_INHERITED,
    SECTION_PAGES,
    get_effective_pages,
    inherited_pages,
    require_role,
)

# Момент «до» добавления свежих разделов склада/продаж.
BEFORE_NEW_PAGES = datetime(2026, 6, 1, 12, 0, 0)
# Момент «после» появления тех разделов, которыми проверяем наследование.
AFTER_NEW_PAGES = datetime(2026, 7, 29, 12, 0, 0)

WAREHOUSE_LEGACY = ["assembly", "logistics", "fbo", "stocks", "stock-analytics"]
FINANCE_LEGACY = ["import", "txn", "reports", "cost"]


class TestPageCatalogMetadata:
    def test_every_page_has_an_added_at_date(self):
        """PAGE_ADDED_AT обязан покрывать каталог целиком.

        Страница без даты добавления не наследуется никогда — новый раздел молча
        не доедет до команды, то есть вернётся ровно тот баг, который лечим.
        """
        assert set(PAGE_ADDED_AT) == set(ALL_PAGES), (
            f"нет даты появления для {set(ALL_PAGES) - set(PAGE_ADDED_AT)}; "
            f"лишние ключи: {set(PAGE_ADDED_AT) - set(ALL_PAGES)}"
        )

    def test_never_inherited_pages_are_real_page_keys(self):
        assert PAGES_NEVER_INHERITED <= set(ALL_PAGES)

    def test_catalog_is_partitioned_by_inheritance_policy(self):
        """Каждый раздел обязан быть ЛИБО наследуемым, ЛИБО явно запрещённым.

        Гард полярности: без него забытый ключ молча уезжает по секции всем, у
        кого она открыта. С ним «забыл классифицировать» = красный CI.
        """
        overlap = PAGES_INHERITABLE & PAGES_NEVER_INHERITED
        assert not overlap, f"раздел и наследуемый, и запрещённый одновременно: {overlap}"

        unclassified = set(ALL_PAGES) - PAGES_INHERITABLE - PAGES_NEVER_INHERITED
        assert not unclassified, (
            f"разделы без решения о наследовании: {unclassified}. "
            f"Добавь в PAGES_INHERITABLE (разъезжается по секции) либо в "
            f"PAGES_NEVER_INHERITED (только явной галочкой владельца)."
        )

        stale = (PAGES_INHERITABLE | PAGES_NEVER_INHERITED) - set(ALL_PAGES)
        assert not stale, f"классификация ссылается на разделы вне каталога: {stale}"

    def test_every_page_belongs_to_exactly_one_section(self):
        """Ключ, забытый в SECTION_PAGES, наследуется только «полным доступом».

        Это тот же класс молчаливого промаха: раздел вроде бы в каталоге, а по
        секции не разъезжается. `dashboard` — легитимное исключение (он вне
        секций и выдаётся только явной галочкой).
        """
        sectioned: dict[str, list[str]] = {}
        for section, pages in SECTION_PAGES.items():
            for page in pages:
                sectioned.setdefault(page, []).append(section)

        duplicates = {p: s for p, s in sectioned.items() if len(s) > 1}
        assert not duplicates, f"раздел в нескольких секциях: {duplicates}"

        missing = set(ALL_PAGES) - set(sectioned) - {"dashboard"}
        assert not missing, f"разделы вне SECTION_PAGES: {missing}"

        stale = set(sectioned) - set(ALL_PAGES)
        assert not stale, f"SECTION_PAGES ссылается на разделы вне каталога: {stale}"

    def test_added_at_dates_are_sane(self):
        """Дата вне [рождение каталога, сегодня] ломает наследование молча.

        Скопированная старая дата = раздел не достаётся никому (исходный баг);
        опечатка в будущее = достаётся всем и навсегда.
        """
        today = date.today()
        for page, added_at in PAGE_ADDED_AT.items():
            assert CATALOG_BIRTH <= added_at <= today, f"{page}: {added_at}"

    def test_sensitive_pages_are_not_inheritable(self):
        """Зарплата, сырые данные и админка проекта — вне наследуемого множества."""
        for page in ("salary", "raw-data", "project-settings", "team"):
            assert page not in PAGES_INHERITABLE


class TestInheritance:
    def test_new_page_of_held_section_is_inherited(self):
        """Кладовщик, настроенный в июне, получает FBS и Генератор ШК."""
        pages = get_effective_pages("editor", _json(WAREHOUSE_LEGACY), BEFORE_NEW_PAGES)

        assert "fbs" in pages
        assert "barcode-labels" in pages
        assert "assembly-analytics" in pages
        # Старые выданные разделы никуда не делись.
        assert set(WAREHOUSE_LEGACY) <= set(pages)

    def test_inheritance_does_not_cross_sections(self):
        """Финансист не получает складские новинки — секция не его."""
        pages = get_effective_pages("editor", _json(FINANCE_LEGACY), BEFORE_NEW_PAGES)

        assert "fbs" not in pages
        assert "barcode-labels" not in pages
        assert "ads-manager" not in pages

    def test_sales_section_inherits_ads_and_ab_tests(self):
        pages = get_effective_pages("viewer", _json(["funnel", "trends", "opiu"]), BEFORE_NEW_PAGES)

        assert "ads-manager" in pages
        assert "ab-tests" in pages

    def test_sensitive_pages_are_never_inherited(self):
        """Зарплата и сырые данные — только явной галочкой, даже своей секцией."""
        finance = get_effective_pages("editor", _json(FINANCE_LEGACY), BEFORE_NEW_PAGES)
        settings = get_effective_pages("editor", _json(["monitoring"]), BEFORE_NEW_PAGES)

        assert "salary" not in finance
        assert "raw-data" not in settings

    def test_page_present_at_configuration_time_stays_revoked(self):
        """Снятая владельцем галочка не «возвращается» наследованием."""
        # Каталог на 29.07 актуален, fbo существовал с самого начала и не выдан.
        held = [p for p in WAREHOUSE_LEGACY if p != "fbo"]
        pages = get_effective_pages("editor", _json(held), AFTER_NEW_PAGES)

        assert "fbo" not in pages

    def test_nothing_inherited_when_config_is_current(self):
        """Настройка против актуального каталога не наследует ничего.

        Момент берём из самой свежей записи каталога, а не константой: иначе тест
        краснеет от каждого нового раздела, хотя проверяет другое.
        """
        newest = max(PAGE_ADDED_AT.values())
        configured_at = datetime(newest.year, newest.month, newest.day, 12, 0, 0)
        held = SECTION_PAGES["warehouse"]

        assert inherited_pages(list(held), configured_at) == []

    def test_sectionless_page_requires_explicit_grant(self):
        """У «Дашборда» нет секции — наследовать его не от чего."""
        assert "dashboard" not in inherited_pages(WAREHOUSE_LEGACY, BEFORE_NEW_PAGES)

    def test_member_without_any_grant_gets_nothing(self):
        assert get_effective_pages("viewer", None, BEFORE_NEW_PAGES) == []
        assert get_effective_pages("viewer", "[]", BEFORE_NEW_PAGES) == []

    def test_missing_watermark_disables_inheritance(self):
        """Без момента настройки наследовать нечего — fail-closed."""
        assert inherited_pages(WAREHOUSE_LEGACY, None) == []

    def test_owner_and_admin_still_get_everything(self):
        for role in ("owner", "admin"):
            assert get_effective_pages(role, None, BEFORE_NEW_PAGES) == list(ALL_PAGES)

    def test_blanket_grant_inherits_every_new_page(self):
        """Кому выдали ВСЁ, что было, тому новые разделы достаются и вне его секций.

        Иначе секция из одной страницы (supply-chain, ai-chat) не достаётся
        никому и никогда: наследовать её не от чего.
        """
        initial = [p for p, added_at in PAGE_ADDED_AT.items() if added_at <= CATALOG_BIRTH]
        pages = get_effective_pages("editor", _json(initial), datetime(2026, 4, 1, 9, 0, 0))

        assert "ai-chat" in pages
        assert "supply-chain" in pages
        assert "fbs" in pages
        # Запрет сильнее полного доступа.
        assert "salary" not in pages
        assert "raw-data" not in pages

    def test_pre_catalog_watermark_does_not_imply_blanket(self):
        """Настройка «до появления каталога» не превращает 5 галочек в полный доступ."""
        extra = inherited_pages(WAREHOUSE_LEGACY, datetime(2026, 1, 1, 0, 0, 0))

        assert "ai-chat" not in extra
        assert "supply-chain" not in extra
        assert set(extra) <= set(SECTION_PAGES["warehouse"])

    def test_catalog_birth_matches_initial_rbac_release(self):
        assert CATALOG_BIRTH == date(2026, 3, 26)

    def test_inherited_pages_are_subset_of_catalog_sections(self):
        extra = inherited_pages(WAREHOUSE_LEGACY, BEFORE_NEW_PAGES)
        warehouse = set(SECTION_PAGES["warehouse"])

        assert set(extra) <= warehouse


class TestRequireRoleGate:
    """API-гейт обязан видеть наследованные страницы, иначе сайдбар пускает, а бэк 403-ит."""

    @pytest.mark.asyncio
    async def test_gate_allows_inherited_page(self, db_session, project):
        from types import SimpleNamespace

        user_id = await _add_member(db_session, project.id, WAREHOUSE_LEGACY, BEFORE_NEW_PAGES)
        dependency = require_role("viewer", page="fbs")

        result = await dependency(
            project=SimpleNamespace(id=project.id),
            user=SimpleNamespace(id=user_id),
            db=db_session,
        )

        assert result.id == user_id

    @pytest.mark.asyncio
    async def test_gate_blocks_salary_and_raw_data_for_blanket_member(self, db_session, project):
        """«Полный доступ» не открывает зарплату и сырые данные — их гейтит API.

        Регрессия, которую этот тест ловит: убрать запрет из emit-цикла
        `inherited_pages`, оставив его в `grantable_then` — чисто функциональные
        тесты останутся зелёными, а ведомость откроется всей секции «Финансы».
        """
        from types import SimpleNamespace

        from fastapi import HTTPException

        blanket = [p for p, added_at in PAGE_ADDED_AT.items() if added_at <= CATALOG_BIRTH]
        configured_at = datetime(2026, 4, 1, 9, 0, 0)
        user_id = await _add_member(db_session, project.id, blanket, configured_at)
        # Убеждаемся, что участник реально blanket, иначе тест проверял бы пустоту.
        assert "ai-chat" in get_effective_pages("editor", _json(blanket), configured_at)

        for page in ("salary", "raw-data"):
            dependency = require_role("viewer", page=page)
            with pytest.raises(HTTPException) as exc:
                await dependency(
                    project=SimpleNamespace(id=project.id),
                    user=SimpleNamespace(id=user_id),
                    db=db_session,
                )
            assert exc.value.status_code == 403, page

    @pytest.mark.asyncio
    async def test_gate_blocks_page_of_other_section(self, db_session, project):
        from types import SimpleNamespace

        from fastapi import HTTPException

        user_id = await _add_member(db_session, project.id, FINANCE_LEGACY, BEFORE_NEW_PAGES)
        dependency = require_role("viewer", page="fbs")

        with pytest.raises(HTTPException) as exc:
            await dependency(
                project=SimpleNamespace(id=project.id),
                user=SimpleNamespace(id=user_id),
                db=db_session,
            )

        assert exc.value.status_code == 403


class TestRevocationThroughApi:
    """Снятие галочки владельцем должно отзывать доступ НАСОВСЕМ.

    Держится это на одной строке — бампе `pages_updated_at` в
    `update_member_role`. Без неё водяной знак остаётся старым, и снятая
    страница возвращается наследованием на следующем же чтении: фича молча
    превращается в «отозвать доступ нельзя». Функциональные тесты такую
    регрессию не видят, поэтому проверяем через сам эндпоинт.
    """

    @pytest.mark.asyncio
    async def test_unchecking_inherited_page_revokes_it_for_good(self, db_session, project):
        from types import SimpleNamespace

        from backend.routers.projects import UpdateRoleRequest, update_member_role

        owner = SimpleNamespace(id=project.owner_id)
        await _add_member(db_session, project.id, [], BEFORE_NEW_PAGES, role="owner", user_id=project.owner_id)
        member_id = await _add_member(db_session, project.id, WAREHOUSE_LEGACY, BEFORE_NEW_PAGES)

        # Стартовое состояние: fbs доехал наследованием, явной галочки нет.
        assert "fbs" in get_effective_pages("editor", _json(WAREHOUSE_LEGACY), BEFORE_NEW_PAGES)

        # Владелец сохраняет доступы без fbs — ровно то, что делает модалка.
        response = await update_member_role(
            slug=project.slug,
            user_id=member_id,
            body=UpdateRoleRequest(role="editor", pages=list(WAREHOUSE_LEGACY)),
            user=owner,
            db=db_session,
        )

        assert "fbs" not in response["pages"]

        row = (
            await db_session.execute(
                text("SELECT pages, pages_updated_at FROM project_members WHERE project_id = :p AND user_id = :u"),
                {"p": project.id, "u": member_id},
            )
        ).first()
        pages_json, watermark = row
        assert "fbs" not in get_effective_pages("editor", pages_json, watermark)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _json(pages: list[str]) -> str:
    import json

    return json.dumps(pages)


async def _add_member(
    db_session,
    project_id: int,
    pages: list[str],
    configured_at: datetime,
    role: str = "editor",
    user_id: int | None = None,
) -> int:
    """Insert a member with an explicit page grant and watermark."""
    import uuid

    if user_id is None:
        suffix = uuid.uuid4().hex[:8]
        user_id = (
            await db_session.execute(
                text(
                    "INSERT INTO users (username, email, password_hash, is_active, created_at) "
                    "VALUES (:u, :e, 'nohash', true, NOW()) RETURNING id"
                ),
                {"u": f"rbac_inh_{suffix}", "e": f"rbac_inh_{suffix}@test.com"},
            )
        ).scalar()
    await db_session.execute(
        text(
            "INSERT INTO project_members (project_id, user_id, role, pages, pages_updated_at, joined_at, is_deleted) "
            "VALUES (:p, :u, :role, :pg, :cfg, NOW(), false)"
        ),
        {
            "p": project_id,
            "u": user_id,
            "role": role,
            "pg": _json(pages) if pages else None,
            "cfg": configured_at,
        },
    )
    await db_session.commit()
    return user_id
