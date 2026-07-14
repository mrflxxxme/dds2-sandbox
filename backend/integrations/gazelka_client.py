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

POST повторяет браузер буквально: отправляются ВСЕ поля формы с их дефолтами (портал
отвечает 500, если поле отсутствует в теле, — напр. ``volume``), а свои значения
накладываются сверху. Допустимые дни отправки/доставки лежат в JS-переменной
``schedule`` на той же странице (см. ``_parse_schedule``).

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
from datetime import date
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


_TEXTAREA_RE = re.compile(r"<textarea\b([^>]*)>(.*?)</textarea>", re.I | re.S)
_SELECTED_RE = re.compile(r"\bselected\b", re.I)
# Поля, которые браузер НЕ отправляет (submit-кнопки, невыбранные чекбоксы)
_NON_SUBMITTED_TYPES = ("submit", "button", "image", "reset", "checkbox", "radio", "hidden")


def _form_defaults(block: str) -> dict[str, str]:
    """Значения ВСЕХ именованных полей формы «как их отправил бы браузер».

    Портал 500-ит, если поле формы вовсе отсутствует в POST (напр. ``volume``:
    у него в разметке прешит ``value="0"``, а пустой строки клиент не слал).
    Поэтому payload строим от полного снимка формы, а свои значения накладываем сверху.
    """
    out: dict[str, str] = {}
    for a in _input_attrs(block):
        name = a.get("name")
        if name and a.get("type", "text").lower() not in _NON_SUBMITTED_TYPES:
            out[name] = a.get("value", "")
    for sm in _SELECT_RE.finditer(block):
        name = _attrs(sm.group(1)).get("name")
        if not name:
            continue
        first: str | None = None
        selected: str | None = None
        for om in _OPTION_RE.finditer(sm.group(2)):
            value = _attrs(om.group(1)).get("value", "")
            if first is None:
                first = value
            if _SELECTED_RE.search(om.group(1)):
                selected = value
        out[name] = selected if selected is not None else (first or "")
    for tm in _TEXTAREA_RE.finditer(block):
        name = _attrs(tm.group(1)).get("name")
        if name:
            out[name] = _html.unescape(_TAGS_RE.sub("", tm.group(2))).strip()
    return out


def _data_min(block: str, input_id: str) -> date | None:
    """``data-min`` датапикера (нижняя граница, ISO). Портал не принимает даты раньше."""
    m = re.search(rf'<input\b[^>]*id="{re.escape(input_id)}"[^>]*>', block, re.I)
    if not m:
        return None
    raw = _attrs(m.group(0)).get("data-min", "")
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


@dataclass
class DeliveryPlace:
    """Опция склада назначения: ``value`` == название, но привязка — по place/marketplace.

    Одно и то же название встречается у разных маркетплейсов («Волгоград» = Ozon 87
    и WB 77), поэтому идентичность опции — пара ``(marketplace_id, place_id)``.
    """

    value: str
    label: str
    place_id: str
    marketplace_id: str


# Коды дней недели портала → номер дня как у JS ``Date.getDay()`` (0 = Вс)
_DAY_CODES = {11: 1, 12: 2, 13: 3, 14: 4, 15: 5, 16: 6, 17: 0}


def decode_days(raw: object) -> list[int] | None:
    """``"151113"`` → ``[5, 1, 3]`` (Пт/Пн/Ср). ``None`` = ограничения нет.

    Зеркало ``decodeDays`` из их ``applyv9.min.js``: коды идут парами (11=Пн … 17=Вс),
    ``2``/``3`` означают «любой день», префикс ``1111`` — «все дни, кроме последней пары».
    """
    try:
        num = int(str(raw or "0"))
    except ValueError:
        return None
    if num <= 3:  # 0/1 — не задано, 2/3 — все дни
        return None
    s = str(num)
    if s[:4] == "1111":
        excluded = _DAY_CODES.get(int(s[-2:]))
        return [d for d in range(7) if d != excluded] if excluded is not None else None
    days = [_DAY_CODES[code] for i in range(0, len(s) - 1, 2) if (code := int(s[i : i + 2])) in _DAY_CODES]
    return days or None


@dataclass
class SchedulePlan:
    """График склада: в какие дни он грузит, в какие принимает, и срок в пути."""

    loading_days: list[int] | None  # None = ограничения нет
    delivery_days: list[int] | None
    eta_days: int
    active: bool


def _parse_schedule(html_text: str) -> dict[str, SchedulePlan]:
    """Встроенный в страницу ``schedule`` — ключ ``"{price_id}-{place_id}"``."""
    raw = _extract_json_object(html_text, "schedule")
    out: dict[str, SchedulePlan] = {}
    for key, row in raw.items():
        if not isinstance(row, dict):
            continue
        try:
            eta = int(str(row.get("delivery_time") or "1"))
        except ValueError:
            eta = 1
        out[key] = SchedulePlan(
            loading_days=decode_days(row.get("loading_days")),
            delivery_days=decode_days(row.get("delivery_days")),
            eta_days=eta or 1,  # у них 0 трактуется как «сегодня же», минимум 1 день
            active=str(row.get("active") or "").lower() in ("t", "true", "1"),
        )
    return out


def _delivery_places(block: str) -> list[DeliveryPlace]:
    """Опции ``delivery_address`` с их ``data-plid`` / ``data-mpid``."""
    sm = re.search(r'<select\b[^>]*name="delivery_address"[^>]*>(.*?)</select>', block, re.I | re.S)
    if not sm:
        return []
    out: list[DeliveryPlace] = []
    for om in _OPTION_RE.finditer(sm.group(1)):
        a = _attrs(om.group(1))
        value = a.get("value", "")
        plid, mpid = a.get("data-plid", ""), a.get("data-mpid", "")
        if not value or not plid:
            continue
        label = _html.unescape(_TAGS_RE.sub("", om.group(2))).strip() or value
        out.append(DeliveryPlace(value=value, label=label, place_id=plid, marketplace_id=mpid))
    return out


@dataclass
class ApplyForm:
    """Снимок формы /customer/apply: селекты + предзаполненные значения + скрытые поля."""

    selects: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    inputs: dict[str, str] = field(default_factory=dict)
    hidden: dict[str, str] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    places: list[DeliveryPlace] = field(default_factory=list)
    schedule: dict[str, SchedulePlan] = field(default_factory=dict)
    min_departure: date | None = None
    min_delivery: date | None = None


def _parse_apply_page(html_text: str) -> ApplyForm:
    """Страница /customer/apply → снимок формы (справочники, дефолты, график)."""
    block = _extract_apply_form(html_text)
    return ApplyForm(
        selects=_selects(block),
        inputs=_visible_input_values(block),
        hidden=_hidden_inputs(block),
        defaults=_form_defaults(block),
        places=_delivery_places(block),
        schedule=_parse_schedule(html_text),  # schedule живёт в <script>, вне формы
        min_departure=_data_min(block, "departure_date"),
        min_delivery=_data_min(block, "delivery_date"),
    )


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


def _balanced_json(html_text: str, open_idx: int, opener: str, closer: str) -> object | None:
    """Скобко-сбалансированный разбор JSON-литерала с учётом строк.

    Значения (напр. ``history``) сами содержат скобки, поэтому наивный поиск закрывашки
    не годится. Значения могут нести HTML-сущности (``&quot;``) — unescape делается
    потребителем при показе.
    """
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
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html_text[open_idx : k + 1])
                except (ValueError, json.JSONDecodeError):
                    return None
    return None


def _extract_json_array(html_text: str, key: str) -> list[dict]:
    """Достать массив ``"key":[...]`` из встроенного в страницу JSON (данные Tabulator)."""
    start = html_text.find(f'"{key}":[')
    if start < 0:
        return []
    data = _balanced_json(html_text, html_text.find("[", start), "[", "]")
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


def _extract_json_object(html_text: str, var_name: str) -> dict:
    """Достать объект из JS-переменной ``const {var_name} = {...}`` внутри <script>."""
    m = re.search(rf"\b{re.escape(var_name)}\s*=\s*\{{", html_text)
    if not m:
        return {}
    data = _balanced_json(html_text, m.end() - 1, "{", "}")
    return data if isinstance(data, dict) else {}


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
        """Снять справочники-селекты, предзаполнение и график формы заявки (нужен логин)."""
        async with self._circuit:
            return _parse_apply_page(await self._get_form_page(self._apply_path))

    async def _get_form_page(self, path: str) -> str:
        resp = await self._http.get(path)
        if resp.status_code >= 500:
            raise ValueError(f"Gazelka apply {resp.status_code}")
        if resp.status_code != 200:
            raise GazelkaApiError(
                f"Газелька: форма заявки недоступна ({resp.status_code})",
                status_code=resp.status_code,
            )
        return resp.text

    async def create_order(
        self, fields: dict[str, object], form: ApplyForm | None = None
    ) -> "GazelkaCreateResult":
        """Создать заявку: POST action=save_plan. БЕЗ retry — это реальный заказ.

        ``form`` — снимок формы, с которого берём свежий CSRF и дефолты полей; если не
        передан, снимаем сами. ``fields`` — наши значения поверх дефолтов; ``None``
        оставляет дефолт формы (пустое поле в POST всё равно уходит, как у браузера).
        """
        async with self._circuit:
            if form is None:
                form = _parse_apply_page(await self._get_form_page(self._apply_path))
            resp = await self._http.post(self._apply_path, data=_merge_payload(form, fields))
            if resp.status_code >= 500:
                raise ValueError(f"Gazelka create {resp.status_code}: {_excerpt(resp.text)}")
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

    def _edit_path(self, plan_id: str) -> str:
        return f"/customer/apply/{self.customer_id}?update={plan_id}"

    async def fetch_edit_form(self, plan_id: str | int) -> ApplyForm:
        """Форма редактирования заявки (предзаполнена): GET /customer/apply/{cid}?update=ID."""
        pid = _validate_plan_id(plan_id)
        async with self._circuit:
            return _parse_apply_page(await self._get_authed(self._edit_path(pid)))

    async def update_order(
        self, plan_id: str | int, fields: dict[str, object], form: ApplyForm | None = None
    ) -> "GazelkaCreateResult":
        """Сохранить правку заявки: POST /customer/apply/{cid}?update=ID. БЕЗ retry."""
        pid = _validate_plan_id(plan_id)
        path = self._edit_path(pid)
        async with self._circuit:
            if form is None:
                form = _parse_apply_page(await self._get_form_page(path))
            resp = await self._http.post(path, data=_merge_payload(form, fields))
            if resp.status_code >= 500:
                raise ValueError(f"Gazelka edit-save {resp.status_code}: {_excerpt(resp.text)}")
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


def _merge_payload(form: ApplyForm, fields: dict[str, object]) -> dict[str, object]:
    """Дефолты формы + CSRF/action + наши значения. ``None`` = оставить дефолт."""
    payload: dict[str, object] = {**form.defaults, **form.hidden}
    payload["action"] = "save_plan"
    payload.update({k: v for k, v in fields.items() if v is not None})
    return payload


def _excerpt(text: str, limit: int = 200) -> str:
    """Текстовая выжимка HTML-ответа без тегов и контактов — для логов и сверки."""
    stripped = re.sub(r"\s+", " ", _TAGS_RE.sub(" ", text)).strip()
    return _redact(stripped)[:limit]


_ORDER_REF_RE = re.compile(r"/customer/(?:order|orders)/[^/]*?(\d{3,})", re.I)


def _parse_create_result(resp: httpx.Response, apply_path: str) -> GazelkaCreateResult:
    """Эвристика успеха: POST save_plan при успехе редиректит из формы в список заявок.

    Точного машинного признака у портала нет — поэтому всегда отдаём excerpt
    ответа, чтобы исход можно было сверить вручную. Надёжное подтверждение
    (появилась ли заявка в «Запланированных») делает слой выше — gazelka_service.
    """
    path = _redirect_path(resp)
    if path and path.startswith(("/customer", "/manager")) and path.rstrip("/") != apply_path.rstrip("/"):
        m = _ORDER_REF_RE.search(path)
        ref = m.group(1) if m else None
        # /customer/orders/{id} — id КЛИЕНТА (страница списка), не номер заявки
        if ref and ref == apply_path.rstrip("/").rsplit("/", 1)[-1]:
            ref = None
        return GazelkaCreateResult(ok=True, ref=ref, message="Заявка отправлена в Газельку", excerpt="")

    # Форма вернулась без редиректа — вероятно ошибка валидации. НО заявка могла
    # и создаться: не ретраим и не утверждаем обратное, отдаём исход на сверку.
    excerpt = _excerpt(resp.text, limit=300)
    return GazelkaCreateResult(
        ok=False,
        ref=None,
        message="Газелька: подтверждение не получено (форма не редиректнула). Сверьте в кабинете перед повтором.",
        excerpt=excerpt,
    )
