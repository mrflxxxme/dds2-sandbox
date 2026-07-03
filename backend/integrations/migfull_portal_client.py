# ruff: noqa: RUF002, RUF003
"""
migfull-портал (plusvb.migfull.app) — клиент создания заявки на отгрузку у ФФ «Натали».

НЕ путать с read-only API-клиентом ``migfull_client.py`` (Bearer-токен на чтение).
Здесь — браузерная интеграция с порталом: это **Filament v5 / Livewire 3** (Laravel),
поэтому работаем не form-POST'ом (как gazelka), а stateful Livewire-протоколом:
каждый шаг — ``POST /livewire/update`` с JSON {snapshot, updates, calls}, заголовок
``X-Livewire: true`` обязателен (иначе 404). Сессия — куки Laravel
(``XSRF-TOKEN`` + ``migfull_session``), CSRF — из ``<meta name=csrf-token>``.

Поток создания заявки (реверс-инжиниринг подтверждён живьём):
1. ``authenticate``     — Livewire login (компонент ``Filament\\Auth\\Pages\\Login``).
2. ``create_shipment``  — Livewire ``create`` на ``CreateShipment``; .live-поля
                          (marketplace_id, filter_delivery_type) сетим ПОСЛЕДОВАТЕЛЬНО
                          по одному раунду (bulk-updates затирает .live), filter
                          delivery_type — последним. Успех = redirect /app/shipments/{guid}.
3. ``upload_opis``      — на edit-странице: mountAction('create') на FilesRelationManager →
                          Livewire 2-шаговый file-upload (_startUpload → POST signed
                          /…/upload-file → _finishUpload) → callMountedAction.

⚠ У клиента портала НЕТ delete/cancel — создание заявки НЕОБРАТИМО. Поэтому create/
upload — БЕЗ retry: ошибку/неопределённость пробрасываем, решает вызывающий.

Креды (login/password) уходят только в теле Livewire-POST, не в URL.
"""

import html as _html
import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import structlog

from backend.integrations.resilience import CircuitBreakerRegistry

logger = structlog.get_logger("dds.migfull_portal")

BASE_URL = "https://plusvb.migfull.app"
ALLOWED_HOST_SUFFIX = "migfull.app"  # SSRF-guard: host из config только в этом домене
TIMEOUT = 45
_REDIRECT_CODES = {301, 302, 303, 307, 308}

# Livewire-компоненты, чьи snapshot мы достаём из страниц
_LOGIN_COMPONENT = "Filament\\Auth\\Pages\\Login"
_CREATE_COMPONENT = "CreateShipment"
_EDIT_COMPONENT = "EditShipment"
_FILES_RM_COMPONENT = "FilesRelationManager"

# Путь поля загрузки файла в смонтированном action-модале
_UPLOAD_STATE_PATH = "mountedActions.0.data.file_uploads"

_GUID_RE = re.compile(r"/app/shipments/([0-9a-fA-F-]{36})")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class MigfullPortalError(ValueError):
    """Ошибка запроса/авторизации/валидации портала — НЕ деградация сервиса.

    Исключена из circuit breaker: неверный логин не должен ронять интеграцию
    на recovery_timeout.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# Per-project circuit breakers — сбои одного проекта не блокируют другие
_portal_circuits = CircuitBreakerRegistry(
    name_prefix="migfull_portal",
    failure_threshold=5,
    recovery_timeout=120.0,
    exclude_errors=(MigfullPortalError,),
)


# ─── Livewire-парсинг (stdlib) ───────────────────────────────────────────────


def _meta_csrf(html_text: str) -> str | None:
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html_text)
    return m.group(1) if m else None


def _find_snapshot(html_text: str, name_suffix: str) -> str | None:
    """Сырой (HTML-unescaped) snapshot Livewire-компонента, чей memo.name кончается на suffix.

    Значение атрибута ``wire:snapshot`` содержит только ``&quot;`` (не литеральные "),
    поэтому нежадный захват до первого ``"`` корректен.
    """
    for m in re.finditer(r'wire:snapshot="(.*?)"', html_text, re.S):
        raw = _html.unescape(m.group(1))
        try:
            snap = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            continue
        if str(snap.get("memo", {}).get("name", "")).endswith(name_suffix):
            return raw
    return None


def _snapshot_data(raw_snapshot: str) -> dict:
    """data-секция компонента CreateShipment/EditShipment: первый элемент массива."""
    try:
        data = json.loads(raw_snapshot).get("data", {})
    except (ValueError, json.JSONDecodeError):
        return {}
    inner = data.get("data")
    if isinstance(inner, list) and inner and isinstance(inner[0], dict):
        return inner[0]
    return data if isinstance(data, dict) else {}


def _normalize_host(raw: str) -> str:
    """SSRF-guard: host из config — только https://*.migfull.app, без пути/порта/кред."""
    value = (raw or BASE_URL).strip()
    if "://" not in value:
        value = "https://" + value
    p = urlparse(value)
    host = (p.hostname or "").lower()
    ok_host = host == ALLOWED_HOST_SUFFIX or host.endswith("." + ALLOWED_HOST_SUFFIX)
    if p.scheme != "https" or not ok_host:
        raise MigfullPortalError(
            "migfull-портал: недопустимый host интеграции (только https://*.migfull.app)", status_code=500
        )
    if p.port not in (None, 443) or (p.path not in ("", "/")) or p.query or p.username or p.password:
        raise MigfullPortalError("migfull-портал: host — только домен, без пути/порта/кред", status_code=500)
    return f"https://{host}"


@dataclass
class MigfullCreateResult:
    guid: str
    number: str | None = None  # reference PVB-… (если удалось прочитать)


@dataclass
class MigfullUploadResult:
    ok: bool
    reference: str | None = None
    excerpt: str = ""


# ─── Клиент ──────────────────────────────────────────────────────────────────


class MigfullPortalClient:
    """Cookie+Livewire-сессия migfull-портала: логин, создание заявки, загрузка описи."""

    def __init__(self, login: str, password: str, project_id: int | None = None, host: str = BASE_URL):
        self.login = login
        self.password = password
        self.project_id = project_id
        self.host = _normalize_host(host)
        self._circuit = _portal_circuits.get(project_id)
        self._client: httpx.AsyncClient | None = None
        self._csrf: str | None = None

    async def __aenter__(self) -> "MigfullPortalClient":
        # follow_redirects=False: login/create определяем по 3xx/effects вручную; авто-
        # следование унесло бы куку/креды на сторонний Location (SSRF-канон).
        self._client = httpx.AsyncClient(
            base_url=self.host,
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (DDS-migfull-portal-integration)"},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("MigfullPortalClient используется вне `async with`")
        return self._client

    def _redact(self, text: str) -> str:
        """Вырезать логин/email из текста (портал эхо-ит логин в ошибках)."""
        out = text.replace(self.login, "[login]") if self.login else text
        return _EMAIL_RE.sub("[email]", out)

    # ─── Низкоуровневый Livewire ─────────────────────────────────────────────

    async def _get(self, path: str) -> httpx.Response:
        resp = await self._http.get(path)
        if resp.status_code >= 500:
            raise ValueError(f"migfull GET {path} {resp.status_code}")
        return resp

    async def _livewire(self, snapshot: str, calls: list[dict] | None = None,
                        updates: dict | None = None, referer: str = "/app") -> dict:
        """Один Livewire-раунд: POST /livewire/update; вернуть components[0]."""
        if not self._csrf:
            raise MigfullPortalError("migfull-портал: нет CSRF-токена (сначала GET страницы)", status_code=500)
        resp = await self._http.post(
            "/livewire/update",
            json={"_token": self._csrf, "components": [{"snapshot": snapshot, "updates": updates or {}, "calls": calls or []}]},
            headers={
                "X-CSRF-TOKEN": self._csrf,
                "X-Livewire": "true",
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Referer": f"{self.host}{referer}",
            },
        )
        if resp.status_code >= 500:
            raise ValueError(f"migfull livewire {resp.status_code}")
        ctype = resp.headers.get("content-type", "")
        if resp.status_code != 200 or "application/json" not in ctype:
            raise MigfullPortalError(
                f"migfull-портал: неожиданный ответ Livewire ({resp.status_code})", status_code=resp.status_code
            )
        try:
            return resp.json()["components"][0]
        except (KeyError, IndexError, ValueError) as e:
            raise MigfullPortalError("migfull-портал: не разобрать ответ Livewire", status_code=502) from e

    # ─── Аутентификация ──────────────────────────────────────────────────────

    async def authenticate(self) -> None:
        """Логин по Livewire-сессии. Raises MigfullPortalError при неудаче."""
        async with self._circuit:
            page = await self._get("/app/login")
            self._csrf = _meta_csrf(page.text)
            snap = _find_snapshot(page.text, _LOGIN_COMPONENT)
            if not self._csrf or not snap:
                raise MigfullPortalError("migfull-портал: страница логина не распознана", status_code=502)
            comp = await self._livewire(
                snap,
                updates={"data.email": self.login, "data.password": self.password, "data.remember": False},
                calls=[{"path": "", "method": "authenticate", "params": []}],
                referer="/app/login",
            )
            redirect = comp.get("effects", {}).get("redirect") or ""
            errors = json.loads(comp["snapshot"])["memo"].get("errors") if comp.get("snapshot") else None
            if not str(redirect).rstrip("/").endswith("/app"):
                if errors:
                    raise MigfullPortalError("migfull-портал: не удалось войти — проверьте логин/пароль", status_code=401)
                raise MigfullPortalError("migfull-портал: вход не подтверждён", status_code=401)

    async def test_connection(self) -> bool:
        """Проверка кредов: логин прошёл. 4xx/HTML-ошибки → False; 5xx пробрасываются."""
        try:
            await self.authenticate()
        except (MigfullPortalError, httpx.HTTPError):
            return False
        return True

    # ─── Создание шапки заявки ───────────────────────────────────────────────

    async def create_shipment(self, header: dict[str, object]) -> MigfullCreateResult:
        """Создать шапку заявки (Livewire `create`). БЕЗ retry — создание необратимо.

        ``header`` — пары data-полей (marketplace_id, shipment_type, number,
        shipment_date, notes, filter_delivery_type). .live-поля сетятся последовательно,
        filter_delivery_type — последним (иначе reactive-перерисовка затрёт его).
        """
        async with self._circuit:
            page = await self._get("/app/shipments/create")
            if page.status_code != 200:
                raise MigfullPortalError("migfull-портал: форма создания недоступна (нужен логин)", status_code=page.status_code)
            self._csrf = _meta_csrf(page.text) or self._csrf
            snap = _find_snapshot(page.text, _CREATE_COMPONENT)
            if not snap:
                raise MigfullPortalError("migfull-портал: форма создания не распознана", status_code=502)

            # Последовательная установка полей; filter_delivery_type — строго последним.
            ordered = sorted(header.items(), key=lambda kv: kv[0] == "filter_delivery_type")
            for field, value in ordered:
                if value is None:
                    continue
                comp = await self._livewire(snap, updates={f"data.{field}": value}, referer="/app/shipments/create")
                snap = comp.get("snapshot") or snap

            comp = await self._livewire(snap, calls=[{"path": "", "method": "create", "params": []}], referer="/app/shipments/create")
            redirect = comp.get("effects", {}).get("redirect") or ""
            m = _GUID_RE.search(redirect)
            if not m:
                errors = json.loads(comp["snapshot"])["memo"].get("errors") if comp.get("snapshot") else None
                msg = "; ".join(str(v) for vs in (errors or {}).values() for v in (vs if isinstance(vs, list) else [vs]))
                raise MigfullPortalError(
                    self._redact(f"migfull-портал: заявка не создана — {msg or 'нет редиректа'}"), status_code=422
                )
            return MigfullCreateResult(guid=m.group(1))

    # ─── Загрузка описи (.xlsx) ──────────────────────────────────────────────

    async def upload_opis(self, guid: str, filename: str, content: bytes, content_type: str) -> MigfullUploadResult:
        """Загрузить опись на заявку: Livewire file-upload + submit action. БЕЗ retry.

        Возвращает MigfullUploadResult(ok, reference). reference (PVB-…) читается с
        edit-страницы заодно (там же снапшот FilesRelationManager).
        """
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", guid):
            raise MigfullPortalError("migfull-портал: некорректный guid заявки", status_code=400)
        ref = f"/app/shipments/{guid}/edit"
        async with self._circuit:
            page = await self._get(ref)
            if page.status_code != 200:
                raise MigfullPortalError(f"migfull-портал: заявка {guid} недоступна", status_code=page.status_code)
            self._csrf = _meta_csrf(page.text) or self._csrf
            reference = _snapshot_data(_find_snapshot(page.text, _EDIT_COMPONENT) or "{}").get("reference")
            files_snap = _find_snapshot(page.text, _FILES_RM_COMPONENT)
            if not files_snap:
                raise MigfullPortalError("migfull-портал: блок файлов заявки не распознан", status_code=502)

            # 1. mountAction('create') — открыть форму загрузки
            comp = await self._livewire(
                files_snap, calls=[{"path": "", "method": "mountAction", "params": ["create", {}, {"table": True}]}], referer=ref
            )
            snap = comp.get("snapshot") or files_snap

            # 2. _startUpload → signed URL (в dispatches upload:generatedSignedUrl)
            finfo = [{"name": filename, "size": len(content), "type": content_type}]
            comp = await self._livewire(
                snap, calls=[{"path": "", "method": "_startUpload", "params": [_UPLOAD_STATE_PATH, finfo, True]}], referer=ref
            )
            snap = comp.get("snapshot") or snap
            signed = None
            for d in comp.get("effects", {}).get("dispatches") or []:
                if d.get("name") == "upload:generatedSignedUrl":
                    signed = d.get("params", {}).get("url")
            if not signed:
                raise MigfullPortalError("migfull-портал: не получен URL загрузки файла", status_code=502)

            # 3. POST файла на signed URL → {paths:[...]}
            up = await self._http.post(
                signed,
                files={"files[]": (filename, content, content_type)},
                headers={"X-CSRF-TOKEN": self._csrf or "", "Accept": "*/*", "Referer": f"{self.host}{ref}"},
            )
            if up.status_code != 200:
                raise MigfullPortalError(f"migfull-портал: загрузка файла отклонена ({up.status_code})", status_code=502)
            try:
                paths = up.json().get("paths") or []
            except ValueError:
                paths = []
            if not paths:
                raise MigfullPortalError("migfull-портал: сервер не принял файл (нет paths)", status_code=502)

            # 4. _finishUpload — привязать tmp-файл к полю
            comp = await self._livewire(
                snap, calls=[{"path": "", "method": "_finishUpload", "params": [_UPLOAD_STATE_PATH, paths, True]}], referer=ref
            )
            snap = comp.get("snapshot") or snap

            # 5. callMountedAction — сохранить (переносит файл в постоянное хранилище)
            comp = await self._livewire(snap, calls=[{"path": "", "method": "callMountedAction", "params": []}], referer=ref)
            errors = json.loads(comp["snapshot"])["memo"].get("errors") if comp.get("snapshot") else None
            ok = not errors
            excerpt = "" if ok else self._redact(json.dumps(errors, ensure_ascii=False))[:300]
            return MigfullUploadResult(ok=ok, reference=reference, excerpt=excerpt)
