# ruff: noqa: RUF002, RUF003
"""
gazelka.space — клиент передачи заявки логиста перевозчику.

У Газельки НЕТ публичного API: это server-rendered PHP-портал (nginx + PHP).
Работаем как браузер — cookie-сессия + form-POST:

1. GET  /                          — кука сессии + динамический CSRF-токен (скрытое
                                      поле со случайным именем, напр. j927b4gtyf0m)
2. POST /                          — логин (login/password/role=customer + CSRF)
3. GET  /customer/apply/{cid}      — форма заявки: справочники-селекты + свежий CSRF
4. POST /customer/apply/{cid}      — создание заявки (action=save_plan + поля формы)

Создание заявки (``create_order``) — РЕАЛЬНЫЙ заказ во внешнем сервисе, идемпотентности
на стороне клиента нет: повторный POST создаст вторую заявку. Поэтому БЕЗ retry —
ошибку/неопределённость пробрасываем, вызывающий решает.

Resilience:
- Per-project circuit breaker (5 failures → 120s); auth/HTML-ошибки (GazelkaApiError)
  исключены — плохой логин не должен блокировать интеграцию на recovery_timeout.

Креды (login/password) уходят только в теле POST, не в URL — httpx-ошибки безопасны
для логов/UI.
"""

import html as _html
import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import structlog

from backend.integrations.resilience import CircuitBreakerRegistry

logger = structlog.get_logger("dds.gazelka")

BASE_URL = "https://gazelka.space"
ALLOWED_HOST = "gazelka.space"  # SSRF-guard: host из config только в этом домене
TIMEOUT = 30
_REDIRECT_CODES = {301, 302, 303, 307, 308}


class GazelkaApiError(ValueError):
    """Ошибка запроса/авторизации/формы Газельки — НЕ деградация сервиса.

    Исключена из circuit breaker: неверный логин не должен ронять интеграцию
    на recovery_timeout.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# Per-project circuit breakers — сбои одного проекта не блокируют другие
_gazelka_circuits = CircuitBreakerRegistry(
    name_prefix="gazelka",
    failure_threshold=5,
    recovery_timeout=120.0,
    exclude_errors=(GazelkaApiError,),
)


# ─── HTML-парсинг (stdlib, без bs4/lxml) ────────────────────────────────────

_ATTR_RE = re.compile(r'([a-zA-Z_][\w:-]*)\s*=\s*"([^"]*)"')
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
_SELECT_RE = re.compile(r"<select\b([^>]*)>(.*?)</select>", re.I | re.S)
_OPTION_RE = re.compile(r"<option\b([^>]*)>(.*?)</option>", re.I | re.S)
_APPLY_FORM_RE = re.compile(r'<form\b[^>]*id="apply-form".*?</form>', re.I | re.S)
_TAGS_RE = re.compile(r"<[^>]+>")


def _attrs(raw: str) -> dict[str, str]:
    """Все name="value"-пары в куске тега (значения unescape'нуты)."""
    return {m.group(1).lower(): _html.unescape(m.group(2)) for m in _ATTR_RE.finditer(raw)}


def _input_attrs(block: str) -> list[dict[str, str]]:
    return [_attrs(t) for t in _INPUT_RE.findall(block)]


def _hidden_inputs(block: str) -> dict[str, str]:
    """Скрытые поля (включая CSRF-токен и action) — name → value."""
    out: dict[str, str] = {}
    for a in _input_attrs(block):
        if a.get("type", "").lower() == "hidden" and a.get("name"):
            out[a["name"]] = a.get("value", "")
    return out


def _visible_input_values(block: str) -> dict[str, str]:
    """Видимые именованные инпуты с прешитым value (напр. customer_phone аккаунта)."""
    out: dict[str, str] = {}
    for a in _input_attrs(block):
        t = a.get("type", "").lower()
        name = a.get("name")
        if not name or t in ("submit", "button", "hidden", "checkbox"):
            continue
        out[name] = a.get("value", "")
    return out


def _selects(block: str) -> dict[str, list[tuple[str, str]]]:
    """name → [(value, label), …] для каждого <select> в блоке."""
    out: dict[str, list[tuple[str, str]]] = {}
    for sm in _SELECT_RE.finditer(block):
        name = _attrs(sm.group(1)).get("name")
        if not name:
            continue
        opts: list[tuple[str, str]] = []
        for om in _OPTION_RE.finditer(sm.group(2)):
            value = _attrs(om.group(1)).get("value", "")
            label = _html.unescape(_TAGS_RE.sub("", om.group(2))).strip()
            opts.append((value, label))
        out[name] = opts
    return out


def _extract_apply_form(html_text: str) -> str:
    """Блок главной формы заявки (id=apply-form). Прочие формы страницы — сиблинги."""
    m = _APPLY_FORM_RE.search(html_text)
    return m.group(0) if m else html_text


@dataclass
class ApplyForm:
    """Снимок формы /customer/apply: селекты + предзаполненные значения + скрытые поля."""

    selects: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    inputs: dict[str, str] = field(default_factory=dict)
    hidden: dict[str, str] = field(default_factory=dict)


def _validate_customer_id(raw: str | int) -> str:
    """customer_id уходит в URL-путь — только цифры (guard от инъекции)."""
    value = str(raw or "").strip()
    if not value.isdigit():
        raise GazelkaApiError("Газелька: customer_id должен быть числом", status_code=400)
    return value


def _validate_plan_id(raw: str | int) -> str:
    """id заявки уходит в URL (path/query) — только цифры."""
    value = str(raw or "").strip()
    if not value.isdigit():
        raise GazelkaApiError("Газелька: id заявки должен быть числом", status_code=400)
    return value


def _extract_json_array(html_text: str, key: str) -> list[dict]:
    """Достать массив ``"key":[...]`` из встроенного в страницу JSON (данные Tabulator).

    Скобко-сбалансированный разбор с учётом строк (значения вроде ``history`` сами
    содержат ``[`` / ``]``). Значения могут нести HTML-сущности (``&quot;``) —
    unescape делается потребителем при показе.
    """
    marker = f'"{key}":['
    start = html_text.find(marker)
    if start < 0:
        return []
    open_idx = html_text.find("[", start)
    depth = 0
    in_str = False
    esc = False
    for k in range(open_idx, len(html_text)):
        c = html_text[k]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(html_text[open_idx : k + 1])
                except (ValueError, json.JSONDecodeError):
                    return []
                return [r for r in data if isinstance(r, dict)]
    return []


def _normalize_host(raw: str) -> str:
    """SSRF-guard: host из config — только https://gazelka.space, без пути/порта/userinfo."""
    value = (raw or BASE_URL).strip()
    if "://" not in value:
        value = "https://" + value
    p = urlparse(value)
    host = (p.hostname or "").lower()
    ok_host = host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST)
    if p.scheme != "https" or not ok_host:
        raise GazelkaApiError(
            "Газелька: недопустимый host интеграции (только https://gazelka.space)", status_code=500
        )
    if p.port not in (None, 443) or (p.path not in ("", "/")) or p.query or p.username or p.password:
        raise GazelkaApiError("Газелька: host — только домен, без пути/порта/кред", status_code=500)
    return f"https://{host}"


def _redirect_path(resp: httpx.Response) -> str | None:
    """Путь Location у 3xx-ответа (follow_redirects выключен). None для не-редиректа."""
    if resp.status_code not in _REDIRECT_CODES:
        return None
    loc = resp.headers.get("location")
    if not loc:
        return None
    return urlparse(loc).path or "/"


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s()\-]{9,}\d")


def _redact(text: str) -> str:
    """Вырезать email/телефон из текста (excerpt портала эхо-ит логин/контакты)."""
    return _PHONE_RE.sub("[phone]", _EMAIL_RE.sub("[email]", text))


# ─── Клиент ─────────────────────────────────────────────────────────────────


class GazelkaClient:
    """Cookie-сессия gazelka.space: логин, снятие формы заявки, создание заявки."""

    def __init__(
        self,
        login: str,
        password: str,
        customer_id: str | int,
        project_id: int | None = None,
        host: str = BASE_URL,
    ):
        self.login = login
        self.password = password
        self.customer_id = _validate_customer_id(customer_id)
        self.project_id = project_id
        self.host = _normalize_host(host)
        self._circuit = _gazelka_circuits.get(project_id)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "GazelkaClient":
        # follow_redirects=False: логин/save_plan определяем по 3xx + Location вручную.
        # Авто-следование за редиректом унесло бы login/password/куку на сторонний
        # Location (см. SSRF-канон normalize_base_url в wmscelicom_client).
        self._client = httpx.AsyncClient(
            base_url=self.host,
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (DDS-gazelka-integration)"},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GazelkaClient используется вне `async with`")
        return self._client

    @property
    def _apply_path(self) -> str:
        return f"/customer/apply/{self.customer_id}"

    async def authenticate(self) -> None:
        """Логин по cookie-сессии. Raises GazelkaApiError при неудаче."""
        async with self._circuit:
            home = await self._http.get("/")
            if home.status_code >= 500:
                raise ValueError(f"Gazelka home {home.status_code}")
            data = {
                **_hidden_inputs(home.text),  # CSRF-токен (динамическое имя поля)
                "login": self.login,
                "password": self.password,
                "role": "customer",
                "dosubmit": "Вход",
            }
            resp = await self._http.post("/", data=data)
            if resp.status_code >= 500:
                raise ValueError(f"Gazelka login {resp.status_code}")
            if not _login_ok(resp):
                raise GazelkaApiError(
                    "Газелька: не удалось войти — проверьте логин/пароль", status_code=401
                )

    async def fetch_apply_form(self) -> ApplyForm:
        """Снять справочники-селекты и предзаполнение формы заявки (нужен логин)."""
        async with self._circuit:
            resp = await self._http.get(self._apply_path)
            if resp.status_code >= 500:
                raise ValueError(f"Gazelka apply {resp.status_code}")
            if resp.status_code != 200:
                raise GazelkaApiError(
                    f"Газелька: форма заявки недоступна ({resp.status_code})",
                    status_code=resp.status_code,
                )
            block = _extract_apply_form(resp.text)
            return ApplyForm(
                selects=_selects(block),
                inputs=_visible_input_values(block),
                hidden=_hidden_inputs(block),
            )

    async def create_order(self, fields: dict[str, object]) -> "GazelkaCreateResult":
        """Создать заявку: POST action=save_plan. БЕЗ retry — это реальный заказ.

        Свежий CSRF и action берём с актуальной формы (GET перед POST).
        ``fields`` — уже отрендеренные строковые значения полей формы.
        """
        async with self._circuit:
            page = await self._http.get(self._apply_path)
            if page.status_code >= 500:
                raise ValueError(f"Gazelka apply {page.status_code}")
            if page.status_code != 200:
                raise GazelkaApiError(
                    f"Газелька: форма заявки недоступна ({page.status_code})",
                    status_code=page.status_code,
                )
            block = _extract_apply_form(page.text)
            payload: dict[str, object] = {**_hidden_inputs(block)}  # CSRF + action
            payload.setdefault("action", "save_plan")
            payload["action"] = "save_plan"
            for key, value in fields.items():
                if value is None:
                    continue
                payload[key] = value

            resp = await self._http.post(self._apply_path, data=payload)
            if resp.status_code >= 500:
                raise ValueError(f"Gazelka create {resp.status_code}")
            return _parse_create_result(resp, self._apply_path)

    async def _get_authed(self, path: str) -> str:
        """GET под сессией; 5xx → ValueError (циркуит/ретрай), не-200 → GazelkaApiError."""
        resp = await self._http.get(path)
        if resp.status_code >= 500:
            raise ValueError(f"Gazelka GET {path} {resp.status_code}")
        if resp.status_code != 200:
            raise GazelkaApiError(f"Газелька: {path} недоступен ({resp.status_code})", status_code=resp.status_code)
        return resp.text

    async def fetch_planned(self) -> dict[str, list[dict]]:
        """Запланированные заявки: встроенный JSON {plans[], marketplaces[]}."""
        async with self._circuit:
            text = await self._get_authed(f"/customer/planned/{self.customer_id}")
        return {
            "plans": _extract_json_array(text, "plans"),
            "marketplaces": _extract_json_array(text, "marketplaces"),
        }

    async def fetch_active(self) -> dict[str, list[dict]]:
        """Активные (в маршруте) заявки + справочники для JOIN (водитель/ТС/маршрут)."""
        async with self._circuit:
            text = await self._get_authed(f"/customer/orders/{self.customer_id}")
        return {
            key: _extract_json_array(text, key)
            for key in ("plans", "routes", "drivers", "vehicles", "carriers", "places", "marketplaces")
        }

    async def fetch_ttn(self, plan_id: str | int) -> tuple[bytes, str]:
        """Печатная форма (упаковочный лист/ТТН) одной заявки: /print-labels?ids[]=ID."""
        pid = _validate_plan_id(plan_id)
        async with self._circuit:
            resp = await self._http.get(f"/print-labels?ids[]={pid}")
            if resp.status_code >= 500:
                raise ValueError(f"Gazelka ttn {resp.status_code}")
            if resp.status_code != 200:
                raise GazelkaApiError(f"Газелька: ТТН недоступна ({resp.status_code})", status_code=resp.status_code)
            ct = resp.headers.get("content-type") or "text/html; charset=utf-8"
            return resp.content, ct

    async def fetch_edit_form(self, plan_id: str | int) -> ApplyForm:
        """Форма редактирования заявки (предзаполнена): GET /customer/apply/{cid}?update=ID."""
        pid = _validate_plan_id(plan_id)
        async with self._circuit:
            text = await self._get_authed(f"/customer/apply/{self.customer_id}?update={pid}")
        block = _extract_apply_form(text)
        return ApplyForm(
            selects=_selects(block),
            inputs=_visible_input_values(block),
            hidden=_hidden_inputs(block),
        )

    async def update_order(self, plan_id: str | int, fields: dict[str, object]) -> "GazelkaCreateResult":
        """Сохранить правку заявки: POST /customer/apply/{cid}?update=ID. БЕЗ retry."""
        pid = _validate_plan_id(plan_id)
        path = f"/customer/apply/{self.customer_id}?update={pid}"
        async with self._circuit:
            page = await self._http.get(path)
            if page.status_code >= 500:
                raise ValueError(f"Gazelka edit {page.status_code}")
            if page.status_code != 200:
                raise GazelkaApiError(f"Газелька: форма правки недоступна ({page.status_code})", status_code=page.status_code)
            block = _extract_apply_form(page.text)
            payload: dict[str, object] = {**_hidden_inputs(block)}
            payload["action"] = "save_plan"
            for key, value in fields.items():
                if value is None:
                    continue
                payload[key] = value
            resp = await self._http.post(path, data=payload)
            if resp.status_code >= 500:
                raise ValueError(f"Gazelka edit-save {resp.status_code}")
            return _parse_create_result(resp, self._apply_path)

    async def test_connection(self) -> bool:
        """Проверка кредов: логин прошёл. 4xx/HTML-ошибки → False; 5xx пробрасываются."""
        try:
            await self.authenticate()
        except (GazelkaApiError, httpx.HTTPError):
            return False
        return True


@dataclass
class GazelkaCreateResult:
    ok: bool
    ref: str | None
    message: str
    excerpt: str


def _login_ok(resp: httpx.Response) -> bool:
    """Успех = редирект (3xx) в кабинет /customer|/manager. follow_redirects выключен."""
    path = _redirect_path(resp)
    return bool(path and path.startswith(("/customer", "/manager")))


_ORDER_REF_RE = re.compile(r"/customer/(?:order|orders)/[^/]*?(\d{3,})", re.I)


def _parse_create_result(resp: httpx.Response, apply_path: str) -> GazelkaCreateResult:
    """Эвристика успеха: POST save_plan при успехе редиректит из формы в список заявок.

    Точного машинного признака у портала нет — поэтому всегда отдаём excerpt
    ответа, чтобы исход можно было сверить вручную.
    """
    path = _redirect_path(resp)
    if path and path.startswith(("/customer", "/manager")) and path.rstrip("/") != apply_path.rstrip("/"):
        m = _ORDER_REF_RE.search(path)
        ref = m.group(1) if m else None
        return GazelkaCreateResult(ok=True, ref=ref, message="Заявка отправлена в Газельку", excerpt="")

    # Форма вернулась без редиректа — вероятно ошибка валидации. НО заявка могла
    # и создаться: не ретраим и не утверждаем обратное, отдаём исход на сверку.
    excerpt = _TAGS_RE.sub(" ", resp.text)
    excerpt = _redact(re.sub(r"\s+", " ", excerpt).strip())[:300]
    return GazelkaCreateResult(
        ok=False,
        ref=None,
        message="Газелька: подтверждение не получено (форма не редиректнула). Сверьте в кабинете перед повтором.",
        excerpt=excerpt,
    )
