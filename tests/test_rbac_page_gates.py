"""Гард page-гейтов RBAC: ни одна ручка «закрытого» домена не должна остаться без `require_role`.

Обобщение `test_every_ab_tests_route_is_gated` (tests/test_ab_tests_rbac.py) на все
домены каталога `backend.rbac.ALL_PAGES`.

Зачем. До этого гарда page-гейт на бэке стоял ровно у четырёх роутеров (payroll,
raw_data, reports/dashboard, payment_requests), а остальные ~860 ручек защищал
только `Depends(get_current_user)` + `get_current_project`. То есть боевой рубеж
был «участник проекта», а не «участник с ключом раздела и ролью editor»:
`PageGuard` на фронте прятал пункт меню, но прямой вызов API проходил. Ровно этот
класс дефекта чинил `fix/ab-tests-rbac-gate` на домене АБ-тестов фото.

Как устроено. Домен = модуль роутера (`backend.routers.<модуль>`), потому что по
пути домены не разделяются: под `/api/v1/warehouse` живут семь разных роутеров.

Три словаря ниже разбивают ВСЕ модули с ручками `/api/v1` на три класса, и
`test_every_router_module_is_classified` не даёт новому роутеру проскользнуть мимо
классификации. Домен, который сознательно не чинится в этой итерации, лежит в
`DEFERRED_DOMAINS` с причиной — дыра зафиксирована, но тест не вечно красный.
"""

import pytest

from backend.main import app
from backend.rbac import ALL_PAGES, require_page, require_page_map, require_role
from backend.routers.funnel import PAGES_BY_ROUTE as FUNNEL_PAGES_BY_ROUTE
from backend.routers.funnel import ROUTES_BY_PAGES as FUNNEL_ROUTES_BY_PAGES
from backend.utils.rate_limit import RateLimiter

# Все три гейта — фабрики, возвращающие замыкание `dependency`; узнаём их по
# qualname. `require_role` вешают на ручку, `require_page` — на роутер целиком,
# `require_page_map` — на роутер, ручки которого сидят на разных ключах.
ROLE_GATE_MARKER = require_role().__qualname__
PAGE_GATE_MARKER = require_page("dashboard").__qualname__
PAGE_MAP_GATE_MARKER = require_page_map({}, "dashboard").__qualname__

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Роли, которых достаточно для мутации. viewer сюда не входит намеренно: смысл
# фикса в том, что «смотрящий» участник не меняет состояние.
WRITE_ROLES = frozenset({"editor", "admin", "owner"})


# ─── Класс 1: домены с обязательным page-гейтом ───────────────────────────────
# модуль роутера -> ключи страниц, которыми домен закрывается (`pageKey` из
# frontend-react/src/app/(main)/p/[slug]/layout.tsx).
GATED_DOMAINS: dict[str, set[str]] = {
    # Меняет цены в кабинете WB и гоняет SPP-пробы живыми ценами — прямые деньги.
    # Страница /pricing в меню сидит под ключом funnel.
    "pricing": {"funnel"},
    # Публикует ПУБЛИЧНЫЕ ответы на отзывы в карточке WB — внешнее состояние,
    # которое видят покупатели.
    "reviews": {"reviews"},
    # Биржа карточек: обмен витринами между проектами.
    "card_exchange": {"card-exchange"},
    # Замеры: синк тянет данные из WB.
    "measurements": {"measurements"},
    # Поставки FBO — создание/изменение поставок в кабинете WB.
    "fbo_supplies": {"fbo"},
    # Порталы перевозчиков: создают РЕАЛЬНЫЕ заказы на перевозку (деньги наружу).
    # Оба пункта меню («Оплаты», «Счета ФФ», «Слоты сдачи») — ключ logistics.
    "gazelka": {"logistics"},
    "migfull_portal": {"logistics"},
    # Реклама WB: /campaigns/{id}/deposit тратит РЕАЛЬНЫЕ деньги, ставки и
    # старт/стоп кампаний — тоже деньги. 91 ручка на семь ключей каталога, так
    # что домен закрыт раскладкой `require_page_map` (backend/routers/funnel.py):
    # ключ у каждой ручки свой, роль выбирается по методу. Здесь перечислены ВСЕ
    # ключи, которые раскладка вправе использовать; что раскладка полная и что
    # денежные ручки сидят именно на ads-manager, проверяют отдельные тесты ниже.
    "funnel": {
        "funnel",
        "ads-manager",
        "trends",
        "geography",
        "opiu",
        "ai-chat",
        "cost",
        "project-settings",
        "ab-tests",
        "dashboard",
    },
    # FBS: /stock/push транслирует остатки в кабинет WB, /supplies + /deliver
    # создают и отправляют реальные поставки. Один ключ на весь домен —
    # страница «FBS Wildberries» единственный потребитель.
    "wb_fbs": {"fbs"},
    # АБ-тесты фото: домен-донор этого гарда, закрыт в fix/ab-tests-rbac-gate —
    # каждая ручка на require_role(..., page="ab-tests"), мутации от editor.
    "ab_tests": {"ab-tests"},
    # Дизайн карточек: модуль изначально написан с гейтом на каждой ручке
    # (require_role(..., page="design-tasks")) — держим, чтобы не отъехал назад.
    "design_tasks": {"design-tasks"},
    # Уже были закрыты до этой итерации — держим, чтобы не отъехали назад.
    "payroll": {"salary"},
    "raw_data": {"raw-data"},
}

# Точечные исключения ВНУТРИ закрытого домена: путь -> почему гейта нет.
# Не «забыли», а осознанное решение с ценой в комментарии у самой ручки.
EXEMPT_ROUTES: dict[str, str] = {
    "/api/v1/raw-data/refresh-progress": (
        "Фронт поллит каждые ~3с; page-проверка = 2 лишних SELECT на тик. Отдаёт "
        "только статусы дозагрузок (низкая чувствительность), членство в проекте "
        "проверяет get_current_project. Решение зафиксировано в backend/routers/raw_data.py."
    ),
    "/api/v1/design-tasks/all-projects": (
        "Сквозной список по брендам — единственная ручка домена БЕЗ "
        "get_current_project. `require_role(page=...)` неприменим по построению: "
        "он резолвит ключ относительно ТЕКУЩЕГО проекта, а тут проектов много. "
        "Гейт не снят, а перенесён в сервис: queries.list_tasks_all_projects "
        "оставляет только проекты, где у участника есть ключ design-tasks "
        "(через get_effective_pages, с наследованием по pages_updated_at)."
    ),
}

# ─── Класс 2: домены, отложенные осознанно ────────────────────────────────────
# Модуль -> почему отложено. Каждая строка — известная дыра: участник проекта с
# ролью viewer и без ключа раздела может дёрнуть эти ручки напрямую.
DEFERRED_DOMAINS: dict[str, str] = {
    # ── ПРИОРИТЕТ 1 следующей итерации: мутации меняют внешнее состояние ──
    "assembly_wb": (
        "POST /wb/bulk-preorder и /{id}/wb/{goods,boxes,pass}/push заводят и проводят "
        "реальный преордер поставки в кабинете WB через WbPortalClient. Ключ assembly."
    ),
    "fulfillment": (
        "POST /assembly/{id}/create-request и /bulk-create-requests создают реальный "
        "заказ у ФФ skladbot. Ключи logistics/assembly вперемешку — нужен разбор."
    ),
    "supply_chain": (
        "PUT /vehicles/{order_no}/status при переходе в DISPATCHED дёргает "
        "push_ff_inbound — реально создаёт приёмку у ФФ skladbot. Остальные 44 "
        "мутации — наша БД. 64 ручки, ключ supply-chain."
    ),
    # ── ПРИОРИТЕТ 2: мутации меняют только наши данные ──
    "warehouse": "77 ручек под общим префиксом /warehouse, ключи stocks/warehouse/logistics вперемешку. Наружу только читает.",
    "assembly": "54 ручки; ключи assembly и assembly-analytics вперемешку.",
    "assembly_drafts": "17 мутаций черновиков сборки; ключ assembly, но живёт под /assembly/drafts.",
    "ff_billing": "Счета и тарифы ФФ; ключ logistics. Внутренние деньги.",
    "cost": "32 ручки себестоимости; ключ cost. Внутренние деньги.",
    "loans": "Займы; ключ refs. Прикрыт require_internal на уровне include_router.",
    "counterparty": "Контрагенты; ключ refs.",
    "refs": "49 ручек справочников; ключ refs.",
    "planning": "Планирование; ключ planning.",
    "planning_customs": "Таможня внутри планирования; ключ planning.",
    "planning_wb_payouts": "Выплаты WB внутри планирования; ключ planning. Три мутации БЕЗ rate_limit_write.",
    "import_txn": "Импорт документов и операции; ключи import/txn.",
    "reports": "Частично закрыт: 4 dashboard-ручки с гейтом, остальные 14 без. Ключи dashboard/reports.",
    "reports_stock": "Отчёты по остаткам; ключ stocks.",
    "reports_wb": "Отчёты WB; ключ reports.",
    "ai_chat": "AI-ассистент; ключ ai-chat.",
    "wb_returns": "Возвраты на ПВЗ; ключ warehouse.",
    "warehouse_speed": "Приоритет складов, только чтение; ключ stock-analytics.",
    "localization": "Индекс локализации; ключ geography.",
    "monitoring": "Мониторинг, только чтение; ключ monitoring.",
    "payment_requests": "Роль admin уже требуется на согласовании/оплате; page-ключа нет. Ключ logistics.",
    "integrations": "Ключи интеграций; ключ project-settings. Требует отдельного разбора — там секреты.",
    "integrations_faktura": "Фактура; ключ project-settings.",
    "telegram": "Привязка чатов и тумблеры уведомлений; ключ project-settings. Семь мутаций БЕЗ rate_limit_write.",
    "vibe": "Вайбкодинг; закрыт отдельным признаком is_vibecoder, не ключом каталога.",
}

# ─── Класс 3: вне доменной модели каталога ────────────────────────────────────
# Модуль -> почему page-ключ к нему неприменим.
OUT_OF_SCOPE: dict[str, str] = {
    "auth": "Публичный вход/регистрация — до всякого проекта.",
    "media": "Публичный кэш фото товаров: <img> не шлёт JWT.",
    "ws": "WebSocket, авторизация внутри по query-параметру.",
    "telegram_webhook": "Вызывает Telegram напрямую, без пользователя.",
    "telegram_miniapp": "TMA: /tma/auth публичный, остальное проверяет JWT внутри.",
    "projects": "CRUD самих проектов и участников — рубеж «членство», page-ключа тут нет по смыслу.",
    "ff_portal": "Портал внешнего оператора ФФ: кросс-проектный, скоуп внутри роутера + middleware.",
    "lender_portal": "Портал внешнего заёмщика: кросс-проектный, скоуп по контрагентам внутри роутера.",
    "backend.main": "Служебные /ping и /seed.",
}


def _iter_api_routes():
    """Все ручки /api/v1 с их модулем-роутером."""
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/v1"):
            continue
        endpoint = getattr(route, "endpoint", None)
        module = getattr(endpoint, "__module__", "?").replace("backend.routers.", "")
        yield route, path, module


def _dep_calls(route) -> list:
    """Все зависимости ручки, развёрнутые в плоский список вызываемых объектов.

    Смотрим и `route.dependencies` (декоратор + `dependencies=` у APIRouter и
    include_router), и параметры сигнатуры — через дерево `dependant`. Иначе
    гейт, объявленный как аргумент функции, посчитался бы отсутствующим.
    """
    calls = [d.dependency for d in getattr(route, "dependencies", []) if d.dependency is not None]
    dep = getattr(route, "dependant", None)
    stack = list(getattr(dep, "dependencies", [])) if dep else []
    while stack:
        sub = stack.pop()
        if sub.call is not None:
            calls.append(sub.call)
        stack.extend(sub.dependencies)
    return calls


def _closure(fn) -> dict:
    out = {}
    code, cells = getattr(fn, "__code__", None), getattr(fn, "__closure__", None)
    if code and cells:
        for name, cell in zip(code.co_freevars, cells):
            try:
                out[name] = cell.cell_contents
            except ValueError:
                pass
    return out


def _gates(route, path: str) -> list[dict]:
    """Гейты ручки, приведённые к общему виду {pages, read_role, write_role}.

    `require_role` задаёт одну роль на любой метод, `require_page` — разные для
    чтения и мутации, `require_page_map` — ещё и разные ключи по путям, поэтому
    нормализуем все три к одной форме. `pages` всегда множество: ключей у ручки
    может быть несколько («достаточно любого»), см. `_check_access`.
    """

    def _pages(page) -> set[str]:
        if not page:
            return set()
        return {page} if isinstance(page, str) else set(page)

    found = []
    for fn in _dep_calls(route):
        qualname = getattr(fn, "__qualname__", "")
        cl = _closure(fn)
        if qualname == ROLE_GATE_MARKER:
            found.append(
                {
                    "pages": _pages(cl.get("page")),
                    "read_role": cl.get("min_role"),
                    "write_role": cl.get("min_role"),
                }
            )
        elif qualname == PAGE_GATE_MARKER:
            found.append(
                {
                    "pages": _pages(cl.get("page")),
                    "read_role": cl.get("read_role"),
                    "write_role": cl.get("write_role"),
                    "read_paths": cl.get("read_paths") or frozenset(),
                }
            )
        elif qualname == PAGE_MAP_GATE_MARKER:
            by_route = cl.get("pages_by_route") or {}
            found.append(
                {
                    "pages": _pages(by_route.get(path, cl.get("default"))),
                    "read_role": cl.get("read_role"),
                    "write_role": cl.get("write_role"),
                    "read_paths": cl.get("read_paths") or frozenset(),
                    # False = ключ достался ручке из `default`, а не из раскладки.
                    "explicit": path in by_route,
                }
            )
    return found


def _has_rate_limit(route) -> bool:
    return any(isinstance(c, RateLimiter) for c in _dep_calls(route))


@pytest.mark.parametrize("module", sorted(GATED_DOMAINS))
def test_every_route_of_domain_is_gated(module: str):
    """Ни одна ручка закрытого домена не должна остаться без `require_role(page=...)`."""
    allowed = GATED_DOMAINS[module]
    ungated = []
    wrong_page = []
    for route, path, mod in _iter_api_routes():
        if mod != module or path in EXEMPT_ROUTES:
            continue
        gates = _gates(route, path)
        label = f"{sorted(route.methods)} {path}"
        if not gates:
            ungated.append(label)
        elif not any(g["pages"] & allowed for g in gates):
            wrong_page.append(f"{label} -> pages={[sorted(g['pages']) for g in gates]}")

    assert ungated == [], f"ручки {module} без RBAC page-гейта: {ungated}"
    assert wrong_page == [], f"ручки {module} с чужим ключом (ожидался один из {sorted(allowed)}): {wrong_page}"


@pytest.mark.parametrize("module", sorted(GATED_DOMAINS))
def test_domain_mutations_require_editor(module: str):
    """Мутации закрытого домена требуют роль не ниже editor — viewer только читает."""
    weak = []
    for route, path, mod in _iter_api_routes():
        if mod != module or path in EXEMPT_ROUTES:
            continue
        if not (set(route.methods or ()) & WRITE_METHODS):
            continue
        gates = _gates(route, path)
        # POST-ради-тела (поиск/витрина) объявлен чтением через read_paths —
        # для него editor не требуется по построению.
        if any(
            any(path.endswith(s) for s in g.get("read_paths", ()))
            for g in gates
        ):
            continue
        roles = {g.get("write_role") for g in gates}
        if not (roles & WRITE_ROLES):
            weak.append(f"{sorted(route.methods)} {path} -> write_role={sorted(r for r in roles if r)}")
    assert weak == [], f"мутации {module} доступны роли viewer: {weak}"


@pytest.mark.parametrize("module", sorted(GATED_DOMAINS))
def test_domain_mutations_are_rate_limited(module: str):
    """Правило 9 корневого CLAUDE.md: write-эндпоинт идёт через rate limiter."""
    unlimited = []
    for route, path, mod in _iter_api_routes():
        if mod != module:
            continue
        if not (set(route.methods or ()) & WRITE_METHODS):
            continue
        if not _has_rate_limit(route):
            unlimited.append(f"{sorted(route.methods)} {path}")
    assert unlimited == [], f"мутации {module} без rate limiter: {unlimited}"


# ─── Раскладка ключей внутри /funnel ──────────────────────────────────────────
# Домен закрыт не одним ключом, а таблицей «путь → ключ(и)», поэтому у него свои
# гарды: таблица обязана покрывать ВСЕ ручки роутера, а денежные ручки — сидеть
# именно на ключе «Управление рекламой».


def test_funnel_page_map_covers_every_route():
    """Каждая ручка /funnel обязана быть в раскладке явно, а не падать в `default`.

    `default` — страховка от незакрытой ручки (fail-closed), но ключ там почти
    наверняка не тот, что нужен странице. Красный тест = автор новой ручки
    должен дописать её в `ROUTES_BY_PAGES` и выбрать раздел осознанно.
    """
    implicit = [
        f"{sorted(route.methods)} {path}"
        for route, path, mod in _iter_api_routes()
        if mod == "funnel" and not all(g.get("explicit") for g in _gates(route, path))
    ]
    assert implicit == [], (
        "ручки /funnel вне раскладки ключей (закрыты дефолтным ключом): "
        f"{implicit}. Добавь путь в ROUTES_BY_PAGES (backend/routers/funnel.py)."
    )


def test_funnel_page_map_has_no_duplicate_paths():
    """Путь в двух группах раскладки — тихая потеря ключей при инверсии словаря.

    `PAGES_BY_ROUTE` собирается из `ROUTES_BY_PAGES` по принципу «последний
    выигрывает», так что дубль не упал бы, а молча отдал ручку чужому разделу.
    """
    declared = sum(len(paths) for paths in FUNNEL_ROUTES_BY_PAGES.values())
    assert declared == len(FUNNEL_PAGES_BY_ROUTE), (
        "в ROUTES_BY_PAGES один путь объявлен дважды: "
        f"{declared} записей свернулись в {len(FUNNEL_PAGES_BY_ROUTE)}"
    )


def test_funnel_page_map_uses_known_page_keys():
    """Ключ вне ALL_PAGES = вечный 403: `get_effective_pages` его не вернёт."""
    unknown = {p for pages in FUNNEL_PAGES_BY_ROUTE.values() for p in pages if p not in ALL_PAGES}
    assert unknown == set(), f"ключи вне каталога ALL_PAGES: {sorted(unknown)}"


# Ручки, которые тратят деньги или меняют состояние рекламы в кабинете WB.
# Им мало «какого-нибудь ключа из домена» — нужен именно ads-manager и editor.
ADS_MONEY_ROUTES: dict[str, str] = {
    "/api/v1/funnel/campaigns/{campaign_id}/deposit": "пополнение бюджета кампании — прямое списание",
    "/api/v1/funnel/campaigns/{campaign_id}/bid": "ставка кампании — цена клика",
    "/api/v1/funnel/campaigns/{campaign_id}/zone-bid": "ставка по зоне — цена клика",
    "/api/v1/funnel/campaigns/{campaign_id}/card-bids": "ставки по карточкам",
    "/api/v1/funnel/campaigns/{campaign_id}/clusters/bid": "ставка по кластеру",
    "/api/v1/funnel/campaigns/{campaign_id}/clusters/bid-bulk": "ставки по кластерам пачкой",
    "/api/v1/funnel/products/{nm_id}/clusters/bid": "ставка по кластеру товара",
    "/api/v1/funnel/campaigns/{campaign_id}/state": "старт/пауза кампании — открывает трату",
    "/api/v1/funnel/campaigns/{campaign_id}/stop": "остановка кампании",
    "/api/v1/funnel/campaigns/{campaign_id}": "удаление кампании",
    "/api/v1/funnel/campaigns/create": "создание кампании в кабинете WB",
    "/api/v1/funnel/campaigns/{campaign_id}/autorefill": "автопополнение — регулярное списание",
    "/api/v1/funnel/campaigns/schedule": "расписание старт/стоп — управляет тратой",
}

# То же для FBS: наружу уходят остатки и реальные поставки.
FBS_DANGEROUS_ROUTES: dict[str, str] = {
    "/api/v1/fbs/stock/push": "трансляция остатков в кабинет WB",
    "/api/v1/fbs/stock/reconcile": "выравнивание остатков в WB",
    "/api/v1/fbs/stock/override": "ручная правка остатка к трансляции",
    "/api/v1/fbs/supplies": "создание поставки в WB",
    "/api/v1/fbs/supplies/bulk": "создание поставок пачкой",
    "/api/v1/fbs/supplies/{wb_supply_id}": "удаление поставки",
    "/api/v1/fbs/supplies/{wb_supply_id}/deliver": "отправка поставки в доставку — необратимо",
    "/api/v1/fbs/orders/{wb_order_id}/cancel": "отмена сборочного задания",
}


@pytest.mark.parametrize(
    ("path", "expected_page", "why"),
    [(p, "ads-manager", why) for p, why in ADS_MONEY_ROUTES.items()]
    + [(p, "fbs", why) for p, why in FBS_DANGEROUS_ROUTES.items()],
)
def test_dangerous_route_requires_its_page_and_editor(path: str, expected_page: str, why: str):
    """Денежная ручка: ключ ровно свой, роль на мутации — не ниже editor."""
    routes = [r for r, p, _ in _iter_api_routes() if p == path]
    assert routes, f"ручка пропала из приложения: {path} ({why})"

    for route in routes:
        if not (set(route.methods or ()) & WRITE_METHODS):
            continue  # у пути бывает и GET-сосед (напр. /autorefill, /schedule)
        gates = _gates(route, path)
        pages = {p for g in gates for p in g["pages"]}
        assert pages == {expected_page}, f"{path} ({why}) закрыт ключами {sorted(pages)}"
        roles = {g.get("write_role") for g in gates}
        assert roles & WRITE_ROLES, f"{path} ({why}) доступен роли viewer: {sorted(r for r in roles if r)}"


# ─── Поведенческая проверка гейта ─────────────────────────────────────────────
# Структурные тесты выше доказывают, что зависимость навешена. Здесь доказываем,
# что она РАБОТАЕТ: `require_page` — новый код, и его 403 надо увидеть живьём.
# Помощники — донор tests/test_ab_tests_rbac.py.


async def _register_user(client):
    import uuid

    username = f"pgrbac_{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "pgrbacpass123", "email": f"{username}@test.com"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": "pgrbacpass123"},
    )
    data = resp.json()
    assert "access_token" in data, f"Login failed ({resp.status_code}): {data}"
    return {"Authorization": f"Bearer {data['access_token']}"}


async def _owner_with_project(client, name):
    owner_headers = await _register_user(client)
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=owner_headers)
    assert resp.status_code == 200, f"Create project failed: {resp.text}"
    return owner_headers, resp.json()


async def _member(client, owner_headers, project, role, pages):
    import json as _json

    member_headers = await _register_user(client)
    resp = await client.get(
        f"/api/v1/projects/{project['slug']}/invite-link",
        params={"role": role, "pages": _json.dumps(pages)},
        headers=owner_headers,
    )
    assert resp.status_code == 200, f"invite-link failed: {resp.text}"
    token = resp.json()["invite_token"]
    resp = await client.post(f"/api/v1/projects/invite/accept/{token}", headers=member_headers)
    assert resp.status_code == 200, f"accept invite failed: {resp.text}"
    return {**member_headers, "X-Project-Id": str(project["id"])}


@pytest.mark.asyncio
async def test_viewer_with_page_reads_pricing_but_cannot_mutate(client):
    """viewer с ключом funnel: чтение цен 200, изменение цен — 403.

    `POST /pricing/sync` и `/spp-probe` реально ходят в кабинет WB и меняют цену,
    поэтому «смотрящему» участнику они закрыты.
    """
    owner_headers, project = await _owner_with_project(client, "RBAC pricing viewer")
    headers = await _member(client, owner_headers, project, "viewer", ["funnel"])

    resp = await client.get("/api/v1/pricing/markup", headers=headers)
    assert resp.status_code == 200, resp.text

    resp = await client.post("/api/v1/pricing/sync", headers=headers)
    assert resp.status_code == 403, f"viewer не должен менять цены: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_editor_without_page_blocked_on_pricing(client):
    """editor БЕЗ ключа funnel: 403 и на чтении, и на мутации.

    Ключевой сценарий: раньше такой участник ходил в API мимо PageGuard.
    """
    owner_headers, project = await _owner_with_project(client, "RBAC pricing no page")
    headers = await _member(client, owner_headers, project, "editor", ["reports"])

    resp = await client.get("/api/v1/pricing/markup", headers=headers)
    assert resp.status_code == 403, f"чтение без ключа funnel: {resp.status_code} {resp.text}"

    resp = await client.post("/api/v1/pricing/sync", headers=headers)
    assert resp.status_code == 403, f"мутация без ключа funnel: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_editor_with_page_passes_pricing_gate(client):
    """editor С ключом funnel проходит гейт: не 403 (дальше отвечает сервис)."""
    owner_headers, project = await _owner_with_project(client, "RBAC pricing editor")
    headers = await _member(client, owner_headers, project, "editor", ["funnel"])

    resp = await client.get("/api/v1/pricing/markup", headers=headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_read_paths_lets_viewer_post_showcase(client):
    """`read_paths`: POST-ради-тела остаётся чтением — viewer его не теряет.

    Без этого послабления основное чтение «Биржи карточек» (POST /showcase)
    вернуло бы viewer'у 403, то есть гейт сломал бы страницу вместо её защиты.
    """
    owner_headers, project = await _owner_with_project(client, "RBAC showcase viewer")
    headers = await _member(client, owner_headers, project, "viewer", ["card-exchange"])

    resp = await client.post(
        "/api/v1/card-exchange/showcase",
        json={"root_id": 1, "page": 1},
        headers=headers,
    )
    # Сервис без настроенной сессии биржи ответит своей ошибкой — важно, что не 403.
    assert resp.status_code != 403, f"viewer потерял чтение витрины: {resp.text}"

    # А вот корзина — настоящая мутация в портале WB, её viewer'у нельзя.
    resp = await client.post(
        "/api/v1/card-exchange/cart/add",
        json={"nm_ids": [1]},
        headers=headers,
    )
    assert resp.status_code == 403, f"viewer не должен править корзину биржи: {resp.status_code}"


@pytest.mark.asyncio
async def test_viewer_cannot_deposit_ad_budget(client):
    """САМОЕ ДОРОГОЕ: viewer с ключом ads-manager не пополняет рекламный бюджет WB.

    `POST /funnel/campaigns/{id}/deposit` списывает РЕАЛЬНЫЕ деньги со счёта
    кабинета Продвижения. До гейта единственным, что отделяло «смотрящего»
    участника от списания, было отсутствие WB-ключа у тестового проекта:
    ручка отвечала 400 «Нет WB-ключа», то есть доходила до сервиса.
    """
    owner_headers, project = await _owner_with_project(client, "RBAC ads viewer")
    headers = await _member(client, owner_headers, project, "viewer", ["ads-manager"])

    resp = await client.post(
        "/api/v1/funnel/campaigns/1/deposit",
        json={"amount": 1000, "source": 0},
        headers=headers,
    )
    assert resp.status_code == 403, f"viewer дошёл до пополнения бюджета: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_editor_without_ads_page_blocked_on_campaigns(client):
    """editor БЕЗ ключа ads-manager: 403 и на чтении списка кампаний, и на ставке."""
    owner_headers, project = await _owner_with_project(client, "RBAC ads no page")
    headers = await _member(client, owner_headers, project, "editor", ["reports"])

    resp = await client.get("/api/v1/funnel/campaigns", headers=headers)
    assert resp.status_code == 403, f"чтение кампаний без ключа: {resp.status_code} {resp.text}"

    resp = await client.put("/api/v1/funnel/campaigns/1/bid", json={"bid": 500}, headers=headers)
    assert resp.status_code == 403, f"ставка без ключа: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_viewer_with_ads_page_reads_campaigns(client):
    """viewer С ключом ads-manager сохраняет чтение — гейт защищает, а не ломает страницу."""
    owner_headers, project = await _owner_with_project(client, "RBAC ads read")
    headers = await _member(client, owner_headers, project, "viewer", ["ads-manager"])

    resp = await client.get("/api/v1/funnel/campaigns", headers=headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_geography_viewer_keeps_funnel_data(client):
    """Несколько ключей на ручку: «Индекс локализации» читает `GET /funnel/data`.

    Ключ у страницы — geography, а ручка живёт в роутере воронки. Требуй гейт
    ровно ключ funnel — и страница локализации перестала бы грузиться.
    """
    owner_headers, project = await _owner_with_project(client, "RBAC geo data")
    headers = await _member(client, owner_headers, project, "viewer", ["geography"])

    resp = await client.get("/api/v1/funnel/data?group_by=day", headers=headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_viewer_cannot_push_fbs_stocks(client):
    """viewer с ключом fbs не транслирует остатки в кабинет WB.

    `POST /fbs/stock/push` пишет остатки на склад продавца — это то, что
    реально продаётся. До гейта viewer доходил до сервиса (409 «нет ключа
    Маркетплейс»).
    """
    owner_headers, project = await _owner_with_project(client, "RBAC fbs viewer")
    headers = await _member(client, owner_headers, project, "viewer", ["fbs"])

    resp = await client.post("/api/v1/fbs/stock/push", json={}, headers=headers)
    assert resp.status_code == 403, f"viewer дошёл до трансляции остатков: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_editor_without_fbs_page_blocked(client):
    """editor БЕЗ ключа fbs: 403 и на чтении заданий, и на создании поставки."""
    owner_headers, project = await _owner_with_project(client, "RBAC fbs no page")
    headers = await _member(client, owner_headers, project, "editor", ["reports"])

    resp = await client.get("/api/v1/fbs/orders", headers=headers)
    assert resp.status_code == 403, f"чтение заданий без ключа fbs: {resp.status_code} {resp.text}"

    resp = await client.post("/api/v1/fbs/supplies", json={"name": "test"}, headers=headers)
    assert resp.status_code == 403, f"создание поставки без ключа fbs: {resp.status_code} {resp.text}"


@pytest.mark.asyncio
async def test_fbs_supply_plan_stays_read_for_viewer(client):
    """`POST /fbs/supplies/plan` — предпросмотр разбиения, а не запись: viewer его не теряет.

    POST выбран из-за размера тела (тысячи заданий в выделении), в WB ручка не
    ходит. Без `read_paths` гейт отобрал бы у «смотрящего» основной сценарий
    страницы FBS.
    """
    owner_headers, project = await _owner_with_project(client, "RBAC fbs plan")
    headers = await _member(client, owner_headers, project, "viewer", ["fbs"])

    resp = await client.post("/api/v1/fbs/supplies/plan", json={"order_ids": []}, headers=headers)
    assert resp.status_code != 403, f"viewer потерял предпросмотр поставок: {resp.text}"


def test_exempt_routes_still_exist():
    """Исключение, потерявшее свою ручку, — мусор: оно должно уйти из словаря."""
    paths = {path for _, path, _ in _iter_api_routes()}
    stale = sorted(set(EXEMPT_ROUTES) - paths)
    assert stale == [], f"EXEMPT_ROUTES ссылается на несуществующие ручки: {stale}"


def test_gated_domains_use_known_page_keys():
    """Ключи в GATED_DOMAINS обязаны существовать в каталоге ALL_PAGES.

    Гейт по ключу вне каталога = вечный 403 для editor/viewer: `get_effective_pages`
    не может вернуть ключ, которого в каталоге нет. Ровно этой ошибкой раздел
    «Биржа карточек» был скрыт от участников (см. PAGE_ADDED_AT в backend/rbac.py).
    """
    unknown = {
        page
        for pages in GATED_DOMAINS.values()
        for page in pages
        if page not in ALL_PAGES
    }
    assert unknown == set(), f"ключи вне каталога ALL_PAGES: {sorted(unknown)}"


def test_every_router_module_is_classified():
    """Новый роутер обязан попасть в один из трёх классов выше.

    Это и есть механизм «дыра не забудется»: пока модуль не классифицирован,
    тест красный, и автор нового домена должен явно решить — закрываем сейчас
    (GATED_DOMAINS) или откладываем с причиной (DEFERRED_DOMAINS).
    """
    known = set(GATED_DOMAINS) | set(DEFERRED_DOMAINS) | set(OUT_OF_SCOPE)
    seen = {mod for _, _, mod in _iter_api_routes()}
    unclassified = sorted(seen - known)
    assert unclassified == [], (
        "роутеры без классификации в tests/test_rbac_page_gates.py: "
        f"{unclassified}. Добавь модуль в GATED_DOMAINS (и закрой гейтом) "
        "или в DEFERRED_DOMAINS с причиной."
    )


def test_classification_has_no_stale_entries():
    """Обратная сторона: модуль, которого больше нет, не должен висеть в словарях."""
    seen = {mod for _, _, mod in _iter_api_routes()}
    stale = sorted((set(GATED_DOMAINS) | set(DEFERRED_DOMAINS) | set(OUT_OF_SCOPE)) - seen)
    assert stale == [], f"классифицированы несуществующие роутеры: {stale}"
