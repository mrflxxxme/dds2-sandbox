# ruff: noqa: RUF002, RUF003
"""wmscelicom «Целиком» office-portal client — проставляет склад сдачи (WB-склад)
на заявке на отгрузку, чего НЕ умеет их публичный API.

`dispatchorders/add` (delivery=2, FBO-самовывоз) не принимает склад структурно —
в OpenAPI-схеме add поля склада нет. Но склад — это первоклассное поле заявки
(`warehouse` в ответе list). В кабинете (`/office/`, тот же хост) он хранится как
`WareHouse` = внутренний id склада «Целиком». Кабинет — server-rendered PHP; правим
как браузер: cookie-сессия `PHPSESSID` + form-POST на `/nohead/…admincontent/`.

Флоу (реверс-инжиниринг, проверен вживую на заявке 997 → warehouse «Электросталь»):
1. POST /nohead/auth/login/            {login,password}         → PHPSESSID (без CSRF)
2. POST …?action=edit&id={id}                                   → свежий IntegrityCRC
3. POST …?action=save&id={id}          {UpParent,Target,…}      → выставить контекст
   заявки (клиент+площадка+договор), чтобы ожил зависимый список складов
4. POST …?action=getajaxlist&id={id}&key=WareHouse              → [{name,id}] складов WB
5. POST …?action=save&id={id}          {…, WareHouse=<id>}      → проставить склад

Состав заявки живёт в отдельной дочерней таблице (`dispatchorders~items`) и этими
save шапки НЕ затрагивается (проверено). Best-effort: сбой не должен ронять
создание заявки у ФФ.

Креды (login/password) уходят только в теле POST, не в URL — httpx-ошибки безопасны
для логов/UI. Хост берётся из config ключа и валидируется allowlist'ом `.wmscelicom.ru`.
"""

import html as _html
import json
import re

import httpx
import structlog

from backend.integrations.resilience import CircuitBreakerRegistry
from backend.integrations.wmscelicom_client import normalize_base_url

logger = structlog.get_logger("dds.wmscelicom_portal")

TIMEOUT = 30
_REDIRECT_CODES = {301, 302, 303, 307, 308}

# Площадка в кабинете «Целиком»: Wildberries=1 (интеграция DDS — только WB FBO).
_TARGET_WILDBERRIES = "1"


class WmsCelicomPortalError(ValueError):
    """Ошибка логина/формы кабинета «Целиком» — НЕ деградация сервиса.

    Исключена из circuit breaker: неверный логин не должен ронять интеграцию
    на recovery_timeout.
    """

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


_portal_circuits = CircuitBreakerRegistry(
    name_prefix="wmscelicom_portal",
    failure_threshold=5,
    recovery_timeout=120.0,
    exclude_errors=(WmsCelicomPortalError,),
)

_CRC_RE = re.compile(r'name="IntegrityCRC"[^>]*value="([^"]*)"', re.I)


def _norm_wh(name: str) -> list[str]:
    """Токены имени склада для нечёткого сопоставления: lower, без скобочных
    уточнений и пунктуации. «Краснодар (Тихорецкая)» → ['краснодар']."""
    s = _html.unescape(name or "").lower()
    s = re.sub(r"\([^)]*\)", " ", s)  # убрать «(Тихорецкая)», «(Уткина Заводь)»
    s = re.sub(r"[^0-9а-яёa-z]+", " ", s)
    return [t for t in s.split() if t]


def resolve_warehouse_id(wh_name: str, options: list[dict]) -> tuple[str, str] | None:
    """Сопоставить наше имя WB-склада с id склада «Целиком».

    options — [{"name": str, "id": int}]. Возврат (id, matched_name) либо None,
    если совпадения нет ИЛИ оно неоднозначно (несколько равно-лучших) — чтобы не
    проставить чужой склад.
    """
    if not wh_name or not options:
        return None
    want = _norm_wh(wh_name)
    if not want:
        return None
    want_set = set(want)

    # 1) точное совпадение по нормализованной строке
    exact = [o for o in options if _norm_wh(str(o.get("name", ""))) == want]
    if len(exact) == 1:
        return str(exact[0]["id"]), str(exact[0]["name"])
    if len(exact) > 1:
        return None  # неоднозначно

    # 2) подмножество токенов (одна сторона ⊆ другой) — берём с макс. пересечением
    scored: list[tuple[int, dict]] = []
    for o in options:
        got = set(_norm_wh(str(o.get("name", ""))))
        if not got:
            continue
        if want_set <= got or got <= want_set:
            scored.append((len(want_set & got), o))
    if not scored:
        return None
    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0][0]
    top = [o for s, o in scored if s == best]
    if len(top) != 1:
        return None  # неоднозначно — не угадываем
    return str(top[0]["id"]), str(top[0]["name"])


class WmsCelicomPortalClient:
    """Кабинет «Целиком» (login + проставление склада сдачи на заявке)."""

    def __init__(
        self,
        host: str,
        login: str,
        password: str,
        *,
        project_id: int,
        client_id: str,
        target_id: str,
    ):
        self.base = normalize_base_url(host)  # https + host ∈ .wmscelicom.ru, без пути/порта
        self.login = login
        self._password = password
        self.client_id = str(client_id)
        self.target_id = str(target_id)
        self._circuit = _portal_circuits.get(project_id)
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "WmsCelicomPortalClient":
        # follow_redirects=False: не уносить логин/куку на сторонний редирект.
        self._http = httpx.AsyncClient(
            base_url=self.base,
            timeout=TIMEOUT,
            follow_redirects=False,
            headers={
                "User-Agent": "Mozilla/5.0 (DDS integration)",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base}/office/dispatchorders/",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError("WmsCelicomPortalClient используется вне async with")
        return self._http

    async def authenticate(self) -> None:
        """Логин в кабинет; успех — по наличию признаков залогиненности."""
        async with self._circuit:
            await self.client.get("/office/")  # выдаёт стартовую PHPSESSID
            resp = await self.client.post(
                "/nohead/auth/login/",
                data={"login": self.login, "password": self._password},
            )
            if resp.status_code >= 500:
                raise ValueError(f"Кабинет «Целиком» вернул {resp.status_code} на логине")
            # тело логина пустое; проверяем сессию по кабинету
            home = await self.client.get("/office/dispatchorders/")
            if "logout" not in home.text and "Выход" not in home.text:
                raise WmsCelicomPortalError("Не удалось войти в кабинет «Целиком» — проверьте логин/пароль")

    async def _fresh_crc(self, order_id: str) -> str:
        resp = await self.client.post(f"/nohead/dispatchorders/admincontent/?action=edit&id={order_id}")
        m = _CRC_RE.search(resp.text)
        if not m or not m.group(1):
            raise WmsCelicomPortalError(
                f"Заявка {order_id} не редактируется в кабинете (нет IntegrityCRC)"
            )
        return m.group(1)

    async def _save_header(self, order_id: str, extra: dict[str, str]) -> str:
        """Сохранить шапку заявки. Базовые поля — контекст WB-FBO (клиент+площадка+
        договор), состав не трогается. `extra` перекрывает/добавляет поля."""
        crc = await self._fresh_crc(order_id)
        data: dict[str, str] = {
            "IntegrityCRC": crc,
            "UpParent": self.client_id,
            "IsRejectState": "0",
            "StrictBarcodes": "0",
            "Target": _TARGET_WILDBERRIES,
            "TargetId": self.target_id,
            "AdditInfo[shipmenttype]": "0",
            "Transportation": "-",
            **extra,
        }
        resp = await self.client.post(
            f"/nohead/dispatchorders/admincontent/?action=save&id={order_id}", data=data
        )
        if resp.status_code >= 500:
            raise ValueError(f"Кабинет «Целиком» вернул {resp.status_code} на сохранении заявки {order_id}")
        if resp.status_code >= 400:
            # 4xx (напр. протухшая сессия 403) — проблема запроса, НЕ деградация:
            # не должна тратить бюджет circuit breaker (конвенция: брейкер только для 5xx)
            raise WmsCelicomPortalError(
                f"Кабинет «Целиком» отклонил сохранение заявки {order_id} (HTTP {resp.status_code})",
                status_code=resp.status_code,
            )
        return resp.text

    async def _warehouse_options(self, order_id: str) -> list[dict]:
        resp = await self.client.post(
            f"/nohead/dispatchorders/admincontent/?action=getajaxlist&id={order_id}&key=WareHouse",
            data={"search": "", "selected": "0", "page": 0, "loadlength": 100},
        )
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError:
            return []
        return [o for o in data if isinstance(o, dict) and o.get("id") is not None] if isinstance(data, list) else []

    async def set_order_warehouse(self, order_id: str, wh_name: str) -> str:
        """Проставить склад сдачи `wh_name` на заявке `order_id`. Возвращает имя
        сопоставленного склада «Целиком». Кидает WmsCelicomPortalError, если склад
        не сопоставлен/не найден в списке."""
        async with self._circuit:
            # 1) контекст заявки → оживает зависимый список складов
            await self._save_header(order_id, {})
            # 2) список складов WB и сопоставление имени
            options = await self._warehouse_options(order_id)
            match = resolve_warehouse_id(wh_name, options)
            if match is None:
                names = ", ".join(str(o.get("name")) for o in options[:12])
                raise WmsCelicomPortalError(
                    f"Склад «{wh_name}» не сопоставлен со складами «Целиком» ({names})"
                )
            wh_id, matched_name = match
            # 3) проставить склад
            await self._save_header(order_id, {"WareHouse": wh_id})
            logger.info(
                "wmscelicom_portal.warehouse_set", order_id=order_id, wh_name=wh_name, matched=matched_name, wh_id=wh_id
            )
            return matched_name
