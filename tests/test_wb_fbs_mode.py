# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты режима контура WB FBS (`WB_FBS_MODE`: safe | sandbox | prod).

Зачем режим: владелец продукта запретил трогать боевой WB записью, пока не
разрешит. Буквальное «чтение с прода / запись в песочницу» физически
недостижимо — песочница WB отдельный контур со своими данными и токеном
scope Test, прод-идентификаторов там нет. Поэтому режим один на весь контур:

  • safe (ДЕФОЛТ) — читаем боевой WB, любой write падает `WbFbsWriteBlocked`
    ДО HTTP (4XX у WB стоит 10 запросов бакета — «запрос ради отказа» вреден);
  • sandbox — весь трафик на sandbox-хост, ключ ТОЛЬКО `wb_marketplace_sandbox`
    (тихого фолбэка на боевой ключ нет — это ровно тот риск, от которого режим
    и защищает);
  • prod — боевой кабинет, запись разрешена.

Сети здесь нет: транспорт замокан `httpx.MockTransport`, и главная проверка —
что в safe-режиме до транспорта дело вообще НЕ доходит.
"""

import httpx
import pytest

from backend.config import Settings
from backend.integrations import wb_fbs_api
from backend.integrations.wb_fbs_api import (
    WB_FBS_PROD_BASE,
    WB_FBS_SANDBOX_BASE,
    WbFbsClient,
    WbFbsClientError,
    WbFbsWriteBlocked,
    current_mode,
    is_write_enabled,
    resolve_base_url,
)
from backend.scheduler.jobs import wb_fbs as fbs_jobs
from backend.services.wb_fbs.client_factory import (
    FBS_SANDBOX_SERVICE,
    FBS_SERVICE,
    FbsNotConfigured,
    get_fbs_client,
    service_for_mode,
)

TOKEN = "eyJ0-marketplace-token"
PROJECT_ID = 515151

#: Все WRITE-методы клиента и аргументы для вызова. Список держим полным
#: намеренно: забытый `write=True` на новом методе — это тихая запись в WB.
WRITE_CALLS = [
    ("create_warehouse", ("Склад", 507)),
    ("update_warehouse", (12, "Новое имя")),
    ("delete_warehouse", (12,)),
    ("put_stocks", (77, [(1001, 5)])),
    ("delete_stocks", (77, [1001])),
    ("cancel_order", (999,)),
    ("create_supply", ("Поставка",)),
    ("add_orders_to_supply", ("WB-GI-1", [1, 2])),
    ("deliver_supply", ("WB-GI-1",)),
    ("delete_supply", ("WB-GI-1",)),
]

#: READ-методы. get_stickers (POST) и get_supply_barcode (GET) состояния WB
#: не меняют — они обязаны работать и в safe.
READ_CALLS = [
    ("ping", ()),
    ("list_offices", ()),
    ("list_warehouses", ()),
    ("get_stocks", (77, [1001])),
    ("get_new_orders", ()),
    ("get_orders", ()),
    ("get_orders_status", ([1, 2],)),
    ("get_stickers", ([1, 2],)),
    ("list_supplies", ()),
    ("get_supply", ("WB-GI-1",)),
    ("get_supply_order_ids", ("WB-GI-1",)),
    ("get_supply_barcode", ("WB-GI-1",)),
]


@pytest.fixture(autouse=True)
def _clean_breaker():
    wb_fbs_api._fbs_circuits.reset(PROJECT_ID)
    yield
    wb_fbs_api._fbs_circuits.reset(PROJECT_ID)


class _Counter:
    """Транспорт-счётчик: показывает, дошло ли дело до сети."""

    def __init__(self, payload=None):
        self.calls: list[httpx.Request] = []
        self._payload = payload if payload is not None else []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return httpx.Response(200, json=self._payload)


def _client(handler, project_id: int = PROJECT_ID) -> WbFbsClient:
    return WbFbsClient(api_key=TOKEN, project_id=project_id, transport=httpx.MockTransport(handler))


# ═════════════════════════════════════════════════════════════════════════════
# Конфиг: значение режима и legacy-алиас
# ═════════════════════════════════════════════════════════════════════════════


class TestModeSetting:
    def test_default_is_safe(self):
        """Дефолт обязан быть безопасным: без явной настройки в WB не пишем."""
        assert Settings().WB_FBS_MODE == "safe"
        assert is_write_enabled("safe") is False

    def test_legacy_sandbox_flag_maps_to_sandbox(self):
        """WB_FBS_SANDBOX=true без явного WB_FBS_MODE = режим sandbox (совместимость)."""
        assert Settings(WB_FBS_SANDBOX=True).WB_FBS_MODE == "sandbox"

    def test_explicit_mode_beats_legacy_flag(self):
        """Явный режим сильнее legacy-тумблера — иначе старый .env молча угонял бы контур."""
        assert Settings(WB_FBS_MODE="prod", WB_FBS_SANDBOX=True).WB_FBS_MODE == "prod"

    def test_case_and_spaces_are_normalized(self):
        assert Settings(WB_FBS_MODE=" PROD ").WB_FBS_MODE == "prod"

    def test_unknown_mode_falls_back_to_safe_with_warning(self):
        """Опечатка в .env не роняет сервис, но и не пускает запись в WB."""
        with pytest.warns(UserWarning, match="WB_FBS_MODE"):
            settings = Settings(WB_FBS_MODE="продакшн")
        assert settings.WB_FBS_MODE == "safe"

    def test_runtime_garbage_resolves_to_safe(self, monkeypatch):
        """Мусор, подсунутый в рантайме, тоже читается как safe, а не как «пиши»."""
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "whatever")
        assert current_mode() == "safe"
        assert is_write_enabled() is False


class TestBaseUrlByMode:
    def test_sandbox_uses_sandbox_host(self, monkeypatch):
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
        assert resolve_base_url() == WB_FBS_SANDBOX_BASE

    @pytest.mark.parametrize("mode", ["safe", "prod"])
    def test_non_sandbox_uses_prod_host(self, monkeypatch, mode):
        """В safe читаем БОЕВОЙ кабинет — это и есть смысл режима."""
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", mode)
        assert resolve_base_url() == WB_FBS_PROD_BASE

    def test_explicit_base_url_still_wins(self, monkeypatch):
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
        assert WbFbsClient(TOKEN, PROJECT_ID, base_url="https://stub.local").base_url == "https://stub.local"


# ═════════════════════════════════════════════════════════════════════════════
# Гейт записи в клиенте
# ═════════════════════════════════════════════════════════════════════════════


class TestWriteGate:
    @pytest.mark.parametrize("method,args", WRITE_CALLS)
    @pytest.mark.asyncio
    async def test_write_blocked_in_safe_mode(self, monkeypatch, method, args):
        """Каждый write-метод отваливается ДО сети: транспорт не дёрнут ни разу."""
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "safe")
        counter = _Counter()
        client = _client(counter)

        with pytest.raises(WbFbsWriteBlocked) as exc:
            await getattr(client, method)(*args)

        assert counter.calls == [], f"{method}: ушёл запрос в WB в режиме safe"
        assert exc.value.status == 409
        assert exc.value.code == "WriteBlocked"
        assert "safe" in exc.value.message

    @pytest.mark.asyncio
    async def test_write_blocked_is_client_error(self, monkeypatch):
        """Отказ контура — доменная 4xx: брейкер её игнорирует, а не «WB лежит»."""
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "safe")
        with pytest.raises(WbFbsClientError):
            await _client(_Counter()).put_stocks(1, [(1, 1)])

    @pytest.mark.parametrize("method,args", READ_CALLS)
    @pytest.mark.asyncio
    async def test_read_methods_work_in_safe_mode(self, monkeypatch, method, args):
        """Чтение (включая POST-стикеры и GET-QR) в safe работает как обычно."""
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "safe")
        counter = _Counter({"orders": [], "stocks": [], "supplies": [], "stickers": [], "next": 0})

        await getattr(_client(counter), method)(*args)

        assert counter.calls, f"{method}: чтение не должно блокироваться в safe"

    @pytest.mark.parametrize("mode", ["prod", "sandbox"])
    @pytest.mark.asyncio
    async def test_write_passes_when_mode_allows(self, monkeypatch, mode):
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", mode)
        counter = _Counter({"id": 5001})

        await _client(counter).create_warehouse("Склад", 507)

        assert len(counter.calls) == 1

    @pytest.mark.asyncio
    async def test_empty_payload_writes_nothing_and_does_not_raise(self, monkeypatch):
        """Пустой PUT/DELETE ничего не меняет — блокировать нечего."""
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "safe")
        counter = _Counter()
        await _client(counter).put_stocks(1, [])
        await _client(counter).delete_stocks(1, [])
        assert counter.calls == []


# ═════════════════════════════════════════════════════════════════════════════
# Выбор ключа по режиму
# ═════════════════════════════════════════════════════════════════════════════


class TestClientFactoryKeyByMode:
    @pytest.mark.parametrize("mode,expected", [("safe", FBS_SERVICE), ("prod", FBS_SERVICE)])
    def test_prod_modes_use_marketplace_key(self, monkeypatch, mode, expected):
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", mode)
        assert service_for_mode() == expected

    def test_sandbox_uses_dedicated_key(self, monkeypatch):
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
        assert service_for_mode() == FBS_SANDBOX_SERVICE
        assert FBS_SANDBOX_SERVICE == "wb_marketplace_sandbox"

    @pytest.mark.asyncio
    async def test_sandbox_without_key_raises_instead_of_falling_back(self, monkeypatch, db_session, project):
        """Боевой ключ есть, ключа песочницы нет → ошибка, а НЕ тихая подмена.

        Тихий фолбэк подсунул бы боевой токен в песочницу (там он всё равно 401)
        и, что хуже, приучил бы верить, что «песочница работает».
        """
        from backend.models import IntegrationKey
        from backend.utils.crypto import encrypt

        db_session.add(
            IntegrationKey(
                project_id=project.id,
                service=FBS_SERVICE,
                label="Маркетплейс",
                encrypted_key=encrypt(TOKEN),
                is_active=True,
            )
        )
        await db_session.commit()

        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
        with pytest.raises(FbsNotConfigured) as exc:
            await get_fbs_client(db_session, project.id)
        assert "Test" in str(exc.value)

        # В боевом режиме тот же ключ подходит — фабрика не сломана в целом.
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "prod")
        client = await get_fbs_client(db_session, project.id)
        assert client.base_url == WB_FBS_PROD_BASE

    @pytest.mark.asyncio
    async def test_sandbox_key_is_used_in_sandbox_mode(self, monkeypatch, db_session, project):
        from backend.models import IntegrationKey
        from backend.utils.crypto import encrypt

        db_session.add(
            IntegrationKey(
                project_id=project.id,
                service=FBS_SANDBOX_SERVICE,
                label="Песочница",
                encrypted_key=encrypt("sandbox-token"),
                is_active=True,
            )
        )
        await db_session.commit()

        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
        client = await get_fbs_client(db_session, project.id)
        assert client.api_key == "sandbox-token"
        assert client.base_url == WB_FBS_SANDBOX_BASE


# ═════════════════════════════════════════════════════════════════════════════
# HTTP: GET /fbs/mode и 409 на заблокированную запись
# ═════════════════════════════════════════════════════════════════════════════


async def _make_project(client, auth_headers, name: str) -> dict:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=auth_headers)
    assert resp.status_code in (200, 201), resp.text
    return {**auth_headers, "X-Project-Id": str(resp.json()["id"])}


class TestModeEndpoint:
    @pytest.mark.asyncio
    async def test_mode_endpoint_reports_safe_defaults(self, client, auth_headers, monkeypatch):
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "safe")
        headers = await _make_project(client, auth_headers, "FBS Mode Safe")

        resp = await client.get("/api/v1/fbs/mode", headers=headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["mode"] == "safe"
        assert body["write_enabled"] is False
        assert body["api_base"] == WB_FBS_PROD_BASE
        assert body["has_key"] is False
        assert body["has_sandbox_key"] is False

    @pytest.mark.asyncio
    async def test_mode_endpoint_reports_sandbox_and_keys(
        self, client, auth_headers, db_session, monkeypatch
    ):
        from sqlalchemy import delete

        from backend.models import IntegrationKey
        from backend.utils.crypto import encrypt

        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
        headers = await _make_project(client, auth_headers, "FBS Mode Sandbox")
        pid = int(headers["X-Project-Id"])
        db_session.add(
            IntegrationKey(
                project_id=pid,
                service=FBS_SANDBOX_SERVICE,
                label="Песочница",
                encrypted_key=encrypt("sandbox-token"),
                is_active=True,
            )
        )
        await db_session.commit()
        try:
            resp = await client.get("/api/v1/fbs/mode", headers=headers)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["mode"] == "sandbox"
            assert body["write_enabled"] is True
            assert body["api_base"] == WB_FBS_SANDBOX_BASE
            assert body["has_key"] is False
            assert body["has_sandbox_key"] is True
        finally:
            await db_session.execute(delete(IntegrationKey).where(IntegrationKey.project_id == pid))
            await db_session.commit()

    @pytest.mark.asyncio
    async def test_mode_requires_auth(self, client):
        resp = await client.get("/api/v1/fbs/mode")
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_write_blocked_maps_to_409_not_500(self, client, auth_headers, db_session, monkeypatch):
        """Ручка записи в safe отвечает 409 с русским текстом, а не 500."""
        from sqlalchemy import delete

        from backend.models import IntegrationKey
        from backend.utils.crypto import encrypt

        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "safe")
        headers = await _make_project(client, auth_headers, "FBS Write Blocked")
        pid = int(headers["X-Project-Id"])
        db_session.add(
            IntegrationKey(
                project_id=pid,
                service=FBS_SERVICE,
                label="Маркетплейс",
                encrypted_key=encrypt(TOKEN),
                is_active=True,
            )
        )
        await db_session.commit()
        try:
            resp = await client.post(
                "/api/v1/fbs/supplies",
                json={"name": "Поставка safe"},
                headers=headers,
            )
            assert resp.status_code == 409, resp.text
            assert "safe" in resp.text
        finally:
            await db_session.execute(delete(IntegrationKey).where(IntegrationKey.project_id == pid))
            await db_session.commit()


# ═════════════════════════════════════════════════════════════════════════════
# Тип ключа песочницы в интеграциях
# ═════════════════════════════════════════════════════════════════════════════


class TestSandboxKeyRegistration:
    @pytest.mark.asyncio
    async def test_sandbox_key_is_probed_against_sandbox_host(self, client, auth_headers, monkeypatch):
        """Проба ключа песочницы бьётся в sandbox-хост: боевой ping дал бы 401."""
        from backend.services import integrations_service

        probed: list[str] = []

        async def fake_check(api_key: str, base_url: str | None = None) -> str:
            probed.append(base_url or "")
            return "ok"

        monkeypatch.setattr(integrations_service, "check_marketplace_scope", fake_check)
        headers = await _make_project(client, auth_headers, "FBS Sandbox Key")

        resp = await client.post(
            "/api/v1/integrations/keys",
            json={
                "service": "wb_marketplace_sandbox",
                "api_key": "sandbox_token_1234567890",
                "label": "Песочница",
            },
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["service"] == "wb_marketplace_sandbox"
        assert probed == [WB_FBS_SANDBOX_BASE]

    @pytest.mark.asyncio
    async def test_sandbox_key_without_scope_rejected(self, client, auth_headers, monkeypatch):
        from backend.services import integrations_service

        async def fake_check(api_key: str, base_url: str | None = None) -> str:
            return "no_scope"

        monkeypatch.setattr(integrations_service, "check_marketplace_scope", fake_check)
        headers = await _make_project(client, auth_headers, "FBS Sandbox Bad Key")

        resp = await client.post(
            "/api/v1/integrations/keys",
            json={"service": "wb_marketplace_sandbox", "api_key": "bad_token_1234567890"},
            headers=headers,
        )

        assert resp.status_code == 400, resp.text


    @pytest.mark.asyncio
    async def test_prod_key_is_probed_against_prod_host_even_in_sandbox(
        self, client, auth_headers, monkeypatch
    ):
        """Хост пробы зависит от ТИПА ключа, а не от режима контура.

        Зеркало бага песочницы: при `WB_FBS_MODE=sandbox` боевой токен
        проверялся на sandbox-хосте, получал 401 → «Ключ без доступа
        Маркетплейс», и валидный ключ было не зарегистрировать.
        """
        from backend.services import integrations_service

        probed: list[str] = []

        async def fake_check(api_key: str, base_url: str | None = None) -> str:
            probed.append(base_url or "")
            return "ok"

        monkeypatch.setattr(integrations_service, "check_marketplace_scope", fake_check)
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
        headers = await _make_project(client, auth_headers, "FBS Prod Key In Sandbox")

        resp = await client.post(
            "/api/v1/integrations/keys",
            json={
                "service": "wb_marketplace",
                "api_key": "marketplace_token_0987654321",
                "label": "Маркетплейс",
            },
            headers=headers,
        )

        assert resp.status_code == 200, resp.text
        assert probed == [WB_FBS_PROD_BASE], "боевой ключ обязан проверяться на боевом хосте"


# ═════════════════════════════════════════════════════════════════════════════
# Фоновые джобы: отбор проектов по ключу ТЕКУЩЕГО режима
# ═════════════════════════════════════════════════════════════════════════════


class _SessionCtx:
    """Подмена `AsyncSessionLocal`: отдаёт сессию теста, не закрывая её."""

    def __init__(self, session) -> None:
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


async def _seed_fbs_project(db_session, project, service: str) -> None:
    """Проект с ключом `service` и активным складом продавца."""
    from backend.models import IntegrationKey, WbFbsWarehouse
    from backend.utils.crypto import encrypt

    db_session.add(
        IntegrationKey(
            project_id=project.id,
            service=service,
            label=service,
            encrypted_key=encrypt(TOKEN),
            is_active=True,
        )
    )
    db_session.add(
        WbFbsWarehouse(
            project_id=project.id,
            wb_warehouse_id=987654,
            name="Склад продавца",
            is_active=True,
        )
    )
    await db_session.commit()


class TestJobsKeyByMode:
    """Джобы обязаны отбирать проекты тем же резолвером, что и роутер.

    Литерал `wb_marketplace` в `_get_fbs_project_ids` делал фон мёртвым в
    песочнице: ручные кнопки работали (они идут через `service_for_mode`), а все
    пять джобов молча выходили на «нет проектов» — ни строки в SyncLog.
    """

    def test_service_follows_mode(self, monkeypatch):
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
        assert fbs_jobs._fbs_key_service() == FBS_SANDBOX_SERVICE == service_for_mode()

        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "prod")
        assert fbs_jobs._fbs_key_service() == FBS_SERVICE == service_for_mode()

    @pytest.mark.asyncio
    async def test_sandbox_project_is_selected_by_sandbox_key(self, monkeypatch, db_session, project):
        await _seed_fbs_project(db_session, project, FBS_SANDBOX_SERVICE)
        monkeypatch.setattr(fbs_jobs, "AsyncSessionLocal", lambda: _SessionCtx(db_session))

        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")
        selected = await fbs_jobs._get_fbs_project_ids()
        assert project.id in [pid for pid, _ in selected]

        # В боевом режиме тот же проект без боевого ключа не отбирается —
        # иначе каждый прогон падал бы FbsNotConfigured в SyncLog.
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "prod")
        selected = await fbs_jobs._get_fbs_project_ids()
        assert project.id not in [pid for pid, _ in selected]

    @pytest.mark.asyncio
    async def test_sync_log_key_belongs_to_current_contour(self, monkeypatch, db_session, project):
        """`SyncLog.integration_id` указывает на ключ ТОГО контура, куда пошёл прогон."""
        from backend.models import IntegrationKey

        await _seed_fbs_project(db_session, project, FBS_SANDBOX_SERVICE)
        monkeypatch.setattr(fbs_jobs, "AsyncSessionLocal", lambda: _SessionCtx(db_session))
        monkeypatch.setattr(wb_fbs_api.settings, "WB_FBS_MODE", "sandbox")

        selected = dict(await fbs_jobs._get_fbs_project_ids())
        key = await db_session.get(IntegrationKey, selected[project.id])
        assert key is not None and key.service == FBS_SANDBOX_SERVICE
