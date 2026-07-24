# ruff: noqa: RUF002, RUF003
"""
mprocket.ru («Нитропак» / MP Rocket) — фулфилмент-портал БЕЗ публичного API.

Это server-rendered Laravel-портал (nginx + PHP). Работаем как браузер
(cookie-сессия), но ТОЛЬКО НА ЧТЕНИЕ: у селлер-аккаунта портал не даёт создавать
поставки/приёмки (маршрут `/ff/*/create` → 403; их заводит оператор ФФ). Поэтому
mprocket — read-only провайдер (как migfull/«Натали»): зеркалим остатки + заявки,
ничего не пишем.

1. GET  /login                                 — кука сессии + CSRF-поле `_token`
2. POST /login                                 — вход (phone + password + _token),
                                                 успех = 3xx-редирект НЕ на /login
3. GET  /businesses/{bid}/ff/stocks?format=xls — выгрузка остатков в .xlsx
4. GET  /businesses/{bid}/ff/shipments         — список поставок (HTML, постранично)
5. GET  /businesses/{bid}/ff/receivings        — список приёмок (HTML, постранично)

ОСТАТКИ (`fetch_all_products`): портал отдаёт готовый .xlsx (колонки Название/
Размер/Цвет/ШК WB/ШК OZON/Количество на складе/Количество в отгрузке/Сумма).
Парсим openpyxl, ключ матчинга — ШК WB.

ЗАЯВКИ (`fetch_shipments`/`fetch_receivings`): списки — server-rendered HTML,
строка несёт `data-sortable-id` (id заявки) + №/тип/дату/статус/кол-во/описание.
Парсим строки (`parse_request_rows`), зеркалим в FulfillmentRequest.

Безопасность (канон gazelka/wmscelicom):
- SSRF-allowlist хоста (`_normalize_host`): только https://seller.mprocket.ru.
- `follow_redirects=False` — логин определяем по 3xx+Location вручную (иначе
  phone/password/кука утекли бы на сторонний редирект).
- Креды (phone/password) уходят только в теле POST; тексты ошибок чистим `_redact`.
"""

import html as _html
import io
import re
from urllib.parse import urlparse

import httpx
import structlog

from backend.integrations.resilience import CircuitBreakerRegistry

logger = structlog.get_logger("dds.mprocket")

BASE_URL = "https://seller.mprocket.ru"
ALLOWED_HOST = "seller.mprocket.ru"  # SSRF-guard: host из config только в этом домене
TIMEOUT = 60  # выгрузка .xlsx может быть тяжёлой
_REDIRECT_CODES = {301, 302, 303, 307, 308}

# Заголовки колонок остатков в .xlsx (матчим без учёта регистра/пробелов)
_COL_BARCODE = "шк wb"
_COL_NAME = "название"
_COL_SIZE = "размер"
_COL_COLOR = "цвет"
_COL_QTY_STOCK = "количество на складе"
_COL_QTY_SHIPPING = "количество в отгрузке"


class MprocketApiError(ValueError):
    """Ошибка запроса/авторизации/формы Нитропака — НЕ деградация сервиса.

    Исключена из circuit breaker: неверный логин не должен ронять интеграцию
    на recovery_timeout.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# Per-project circuit breakers — сбои одного проекта не блокируют другие
_mprocket_circuits = CircuitBreakerRegistry(
    name_prefix="mprocket",
    failure_threshold=5,
    recovery_timeout=120.0,
    exclude_errors=(MprocketApiError,),
)

_TOKEN_RE = re.compile(r'name="_token"[^>]*value="([^"]+)"', re.I)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s()\-]{9,}\d")


def _extract_csrf(html_text: str) -> str | None:
    """CSRF-токен `_token` со страницы /login (Laravel; значение в скрытом поле)."""
    m = _TOKEN_RE.search(html_text)
    return _html.unescape(m.group(1)) if m else None


def _redact(text: str) -> str:
    """Вырезать email/телефон из текста (портал эхо-ит логин/контакты)."""
    return _PHONE_RE.sub("[phone]", _EMAIL_RE.sub("[email]", text))


def _normalize_host(raw: str) -> str:
    """SSRF-guard: host из config — только https://seller.mprocket.ru, без пути/порта/кред."""
    value = (raw or BASE_URL).strip()
    if "://" not in value:
        value = "https://" + value
    p = urlparse(value)
    host = (p.hostname or "").lower()
    ok_host = host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST)
    if p.scheme != "https" or not ok_host:
        raise MprocketApiError(
            "Нитропак: недопустимый host интеграции (только https://seller.mprocket.ru)", status_code=500
        )
    if p.port not in (None, 443) or (p.path not in ("", "/")) or p.query or p.username or p.password:
        raise MprocketApiError("Нитропак: host — только домен, без пути/порта/кред", status_code=500)
    return f"https://{host}"


def _validate_business_id(raw: str | int) -> str:
    """business_id уходит в URL-путь — только цифры (guard от инъекции)."""
    value = str(raw or "").strip()
    if not value.isdigit():
        raise MprocketApiError("Нитропак: business_id должен быть числом", status_code=400)
    return value


def _redirect_path(resp: httpx.Response) -> str | None:
    """Путь Location у 3xx-ответа (follow_redirects выключен). None для не-редиректа."""
    if resp.status_code not in _REDIRECT_CODES:
        return None
    loc = resp.headers.get("location")
    if not loc:
        return None
    return urlparse(loc).path or "/"


def _login_ok(resp: httpx.Response) -> bool:
    """Успех логина = 3xx-редирект КУДА-УГОДНО, кроме обратно на /login."""
    path = _redirect_path(resp)
    return bool(path and not path.startswith("/login"))


def parse_stock_xlsx(content: bytes) -> list[dict]:
    """Распарсить .xlsx остатков Нитропака → список нормализованных строк.

    Колонки матчим по ЗАГОЛОВКУ (а не позиции) — устойчиво к перестановке.
    Возвращает [{barcode, name, qty_stock, qty_shipping}]; строки без ШК WB
    пропускаются. Raises MprocketApiError, если формат не распознан.
    """
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 — любой сбой парсинга = «формат не тот»
        raise MprocketApiError(f"Нитропак: не удалось прочитать .xlsx остатков ({e})", status_code=502) from e
    ws = wb.active
    if ws is None:
        raise MprocketApiError("Нитропак: пустой .xlsx остатков", status_code=502)

    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
        raise MprocketApiError("Нитропак: в .xlsx остатков нет заголовка", status_code=502)

    # заголовок → индекс колонки (нормализуем регистр/пробелы)
    col: dict[str, int] = {}
    for idx, cell in enumerate(header):
        key = str(cell or "").strip().lower()
        if key:
            col[key] = idx
    if _COL_BARCODE not in col:
        raise MprocketApiError("Нитропак: в .xlsx остатков нет колонки «ШК WB»", status_code=502)

    def _cell(row: tuple, key: str) -> str:
        idx = col.get(key)
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx] if row[idx] is not None else "").strip()

    def _int(row: tuple, key: str) -> int:
        raw = _cell(row, key)
        if not raw:
            return 0
        try:
            return int(float(raw.replace(" ", "").replace(",", ".")))
        except ValueError:
            return 0

    out: list[dict] = []
    for row in rows_iter:
        barcode = _cell(row, _COL_BARCODE)
        if not barcode:
            continue
        name_parts = [_cell(row, _COL_NAME), _cell(row, _COL_SIZE), _cell(row, _COL_COLOR)]
        name = " · ".join(p for p in name_parts if p) or None
        out.append(
            {
                "barcode": barcode,
                "name": name,
                "qty_stock": _int(row, _COL_QTY_STOCK),
                "qty_shipping": _int(row, _COL_QTY_SHIPPING),
            }
        )
    return out


# ─── Списки заявок (поставки / приёмки) — server-rendered HTML ────────────────

# Защита от бесконечной пагинации списков
MAX_LIST_PAGES = 15

_ROW_SPLIT = '<div class="uk-grid mprs-data-row"'
_SORTABLE_RE = re.compile(r'data-sortable-id="(\d+)"')
_NUMBER_RE = re.compile(r"№\s*(\d+)")
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
_QTY_RE = re.compile(r"(\d[\d\s]*)\s*шт\b")
_LINK_RE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.S)
_TYPE_DIV_RE = re.compile(r"</a>\s*<div>([^<]+)</div>", re.S)
_COLORED_STATUS_RE = re.compile(r'<div style="color:[^"]*">\s*([^<]+?)\s*</div>', re.S)
# Известные статусы приёмок (у поставок статус — в цветном div)
_RECV_STATUS_RE = re.compile(
    r"(Создана|Доставлена на ФФ|Принята|Завершена|Отменена|Инвентаризац\w+|Обработка|Ожидает|В пути|Перемещение)"
)
_NOTES_RE = re.compile(r'uk-text-break">(.*?)</div>', re.S)


def _strip_tags(text: str) -> str:
    return _html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"\s+", " ", text))).strip()


def parse_request_rows(html_text: str) -> list[dict]:
    """Распарсить HTML-список поставок/приёмок → строки заявок (read-only зеркало).

    Каждая карточка-строка несёт `data-sortable-id` (id заявки у ФФ) + №/тип/дату/
    статус/кол-во/описание. Возвращает [{external_id, number, type_name,
    status_title, date_str(ISO), total_qty, notes}]; строки без id пропускаются.
    Best-effort: отсутствующее поле → None. Не бросает.
    """
    out: list[dict] = []
    for part in html_text.split(_ROW_SPLIT)[1:]:
        m_id = _SORTABLE_RE.search(part)
        if not m_id:
            continue
        external_id = m_id.group(1)
        m_num = _NUMBER_RE.search(part)
        m_date = _DATE_RE.search(part)
        m_qty = _QTY_RE.search(part)
        m_type = _TYPE_DIV_RE.search(part)
        m_link = _LINK_RE.search(part)
        m_status = _COLORED_STATUS_RE.search(part) or _RECV_STATUS_RE.search(part)
        m_notes = _NOTES_RE.search(part)

        # тип: для поставок — <div> после ссылки; для приёмок — текст ссылки без «№N:»
        type_name = _strip_tags(m_type.group(1)) if m_type else None
        if not type_name and m_link:
            link_text = _strip_tags(m_link.group(1))
            type_name = re.sub(r"№\s*\d+:?", "", link_text).strip(" :—-") or None

        out.append(
            {
                "external_id": external_id,
                "number": m_num.group(1) if m_num else external_id,
                "type_name": type_name,
                "status_title": _strip_tags(m_status.group(1)) if m_status else None,
                # dd.mm.yyyy → ISO yyyy-mm-dd (для _parse_date в сервисе)
                "date_str": f"{m_date.group(3)}-{m_date.group(2)}-{m_date.group(1)}" if m_date else None,
                "total_qty": int(re.sub(r"\s+", "", m_qty.group(1))) if m_qty else None,
                "notes": (_strip_tags(m_notes.group(1))[:500] or None) if m_notes else None,
            }
        )
    return out


class MprocketClient:
    """Cookie-сессия seller.mprocket.ru: логин, выгрузка остатков (.xlsx), заявка."""

    def __init__(
        self,
        login: str,
        password: str,
        business_id: str | int,
        project_id: int | None = None,
        host: str = BASE_URL,
    ):
        self.login = login
        self.password = password
        self.business_id = _validate_business_id(business_id)
        self.project_id = project_id
        self.host = _normalize_host(host)
        self._circuit = _mprocket_circuits.get(project_id)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "MprocketClient":
        # follow_redirects=False: логин определяем по 3xx + Location вручную (иначе
        # phone/password/кука утекли бы на сторонний Location — SSRF-канон).
        self._client = httpx.AsyncClient(
            base_url=self.host,
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0 (DDS-mprocket-integration)"},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("MprocketClient используется вне `async with`")
        return self._client

    async def authenticate(self) -> None:
        """Логин по cookie-сессии (Laravel). Raises MprocketApiError при неудаче."""
        async with self._circuit:
            page = await self._http.get("/login")
            if page.status_code >= 500:
                raise ValueError(f"Mprocket login page {page.status_code}")
            token = _extract_csrf(page.text)
            if not token:
                raise MprocketApiError("Нитропак: не найден CSRF-токен на /login", status_code=502)
            resp = await self._http.post(
                "/login",
                data={"_token": token, "phone": self.login, "password": self.password},
            )
            if resp.status_code >= 500:
                raise ValueError(f"Mprocket login {resp.status_code}")
            if not _login_ok(resp):
                raise MprocketApiError(
                    "Нитропак: не удалось войти — проверьте телефон/пароль", status_code=401
                )

    async def test_connection(self) -> bool:
        """Проверка кредов: логин прошёл. 4xx/HTML-ошибки → False; 5xx пробрасываются."""
        try:
            await self.authenticate()
        except (MprocketApiError, httpx.HTTPError):
            return False
        return True

    async def fetch_all_products(self) -> list[dict]:
        """Остатки бизнеса: выгрузка .xlsx → [{barcode, name, qty_stock, qty_shipping}].

        Требует предварительного `authenticate()` (та же сессия/кука).
        """
        path = f"/businesses/{self.business_id}/ff/stocks?format=xls"
        async with self._circuit:
            resp = await self._http.get(path)
            if resp.status_code >= 500:
                raise ValueError(f"Mprocket stocks {resp.status_code}")
            if resp.status_code in _REDIRECT_CODES:
                # редирект на /login = сессия отвалилась
                raise MprocketApiError("Нитропак: сессия истекла при выгрузке остатков", status_code=401)
            if resp.status_code != 200:
                raise MprocketApiError(
                    f"Нитропак: выгрузка остатков недоступна ({resp.status_code})", status_code=resp.status_code
                )
        return parse_stock_xlsx(resp.content)

    async def _fetch_request_list(self, section: str) -> list[dict]:
        """Постранично собрать список заявок раздела (`shipments` | `receivings`).

        Требует предварительного `authenticate()`. Останавливается на пустой странице
        или когда новых id не появилось (дедуп по external_id); кап MAX_LIST_PAGES.
        """
        rows: list[dict] = []
        seen: set[str] = set()
        page = 1
        while page <= MAX_LIST_PAGES:
            async with self._circuit:
                resp = await self._http.get(f"/businesses/{self.business_id}/ff/{section}?page={page}")
                if resp.status_code >= 500:
                    raise ValueError(f"Mprocket {section} {resp.status_code}")
                if resp.status_code in _REDIRECT_CODES:
                    raise MprocketApiError("Нитропак: сессия истекла при чтении заявок", status_code=401)
                if resp.status_code != 200:
                    raise MprocketApiError(
                        f"Нитропак: раздел {section} недоступен ({resp.status_code})", status_code=resp.status_code
                    )
                text = resp.text
            batch = parse_request_rows(text)
            fresh = [r for r in batch if r["external_id"] not in seen]
            if not fresh:
                break  # пустая страница или повтор (за последней) — конец
            for r in fresh:
                seen.add(r["external_id"])
            rows.extend(fresh)
            page += 1
        else:
            logger.warning("mprocket.list_page_cap", section=section, cap=MAX_LIST_PAGES)
        return rows

    async def fetch_shipments(self) -> list[dict]:
        """Поставки (отгрузки на МП) — read-only зеркало (нужен `authenticate()`)."""
        return await self._fetch_request_list("shipments")

    async def fetch_receivings(self) -> list[dict]:
        """Приёмки на склад ФФ — read-only зеркало (нужен `authenticate()`)."""
        return await self._fetch_request_list("receivings")
