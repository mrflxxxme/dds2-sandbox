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
from backend.rbac import ALL_PAGES, require_page, require_role
from backend.utils.rate_limit import RateLimiter

# Оба гейта — фабрики, возвращающие замыкание `dependency`; узнаём их по qualname.
# `require_role` вешают на ручку, `require_page` — на роутер целиком.
ROLE_GATE_MARKER = require_role().__qualname__
PAGE_GATE_MARKER = require_page("dashboard").__qualname__

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
}

# ─── Класс 2: домены, отложенные осознанно ────────────────────────────────────
# Модуль -> почему отложено. Каждая строка — известная дыра: участник проекта с
# ролью viewer и без ключа раздела может дёрнуть эти ручки напрямую.
DEFERRED_DOMAINS: dict[str, str] = {
    # ── ПРИОРИТЕТ 1 следующей итерации: мутации меняют внешнее состояние ──
    "funnel": (
        "САМОЕ ОПАСНОЕ ИЗ ОТЛОЖЕННОГО. 91 ручка на шесть ключей (funnel/ads-manager/"
        "trends/opiu/plan-fact/geography), поэтому одним ключом роутер не закрыть — "
        "нужна раскладка ручка→ключ. Внутри: POST /funnel/campaigns/{id}/deposit "
        "(пополнение рекламного бюджета WB — прямые деньги), PUT /campaigns/{id}/bid "
        "и /zone-bid (ставки — деньги), POST /campaigns/{id}/state, /stop, "
        "DELETE /campaigns/{id} (старт/пауза/удаление кампаний)."
    ),
    "wb_fbs": (
        "POST /fbs/stock/push и /stock/reconcile транслируют остатки в кабинет WB "
        "(влияет на то, что реально продаётся), POST /fbs/supplies + PATCH "
        "/supplies/{id}/deliver + DELETE создают и отправляют реальные поставки, "
        "PATCH /orders/{id}/cancel отменяет сборочные задания. Частично прикрыт "
        "require_internal на include_router — внешние аккаунты (ФФ/лендер) не ходят."
    ),
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
    "ab_tests": "Чинится параллельно в ветке fix/ab-tests-rbac-gate — не трогаем, чтобы не разъехаться в merge.",
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


def _gates(route) -> list[dict]:
    """Гейты ручки, приведённые к общему виду {page, read_role, write_role}.

    `require_role` задаёт одну роль на любой метод, `require_page` — разные для
    чтения и мутации, поэтому нормализуем оба к одной форме.
    """
    found = []
    for fn in _dep_calls(route):
        qualname = getattr(fn, "__qualname__", "")
        cl = _closure(fn)
        if qualname == ROLE_GATE_MARKER:
            found.append(
                {
                    "page": cl.get("page"),
                    "read_role": cl.get("min_role"),
                    "write_role": cl.get("min_role"),
                }
            )
        elif qualname == PAGE_GATE_MARKER:
            found.append(
                {
                    "page": cl.get("page"),
                    "read_role": cl.get("read_role"),
                    "write_role": cl.get("write_role"),
                    "read_paths": cl.get("read_paths") or frozenset(),
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
        gates = _gates(route)
        label = f"{sorted(route.methods)} {path}"
        if not gates:
            ungated.append(label)
        elif not any(g.get("page") in allowed for g in gates):
            wrong_page.append(f"{label} -> pages={[g.get('page') for g in gates]}")

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
        gates = _gates(route)
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
