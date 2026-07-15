"""Клиент WB Content API (content-api.wildberries.ru) — карточки товаров и их медиа.

Используется АБ-тестами главного фото: снимок текущей галереи карточки и замена
главного фото (X-Photo-Number=1). Токен — категория «Контент» с правом записи
(в интеграциях — service "wb_content").

ВНИМАНИЕ: media/file реально меняет карточку на витрине. Все вызовы записи идут
только из сервиса АБ-тестов, который перед сменой сверяет галерею со своим снимком
(защита от гонки с внешними правками карточки).
"""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://content-api.wildberries.ru"
_TIMEOUT = 60


class WbContentError(Exception):
    """Ошибка Content API (после ретраев). Текст безопасен для UI."""


def _headers(api_key: str) -> dict:
    return {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}


async def check_content_scope(api_key: str) -> str:
    """Есть ли у токена категория «Контент». Возвращает "ok" | "no_scope" | "unknown".

    Пробник — лёгкий POST get/cards/list (limit 1): валидирует именно контент-scope.
    "unknown" (429/5xx/сеть) НЕ считать невалидным ключом — зеркало check_advert_scope.
    """
    body = {"settings": {"cursor": {"limit": 1}, "filter": {"withPhoto": -1}}}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{_BASE}/content/v2/get/cards/list", headers=_headers(api_key), json=body)
    except httpx.HTTPError as e:
        logger.warning(f"Content scope check: network error, cannot verify — {e}")
        return "unknown"
    if resp.status_code == 200:
        return "ok"
    if resp.status_code in (401, 403):
        logger.info(f"Content scope check: token rejected ({resp.status_code}) — no Контент scope")
        return "no_scope"
    logger.warning(f"Content scope check: transient {resp.status_code} from WB content API, cannot verify")
    return "unknown"


async def fetch_card(api_key: str, nm_id: int) -> dict | None:
    """Карточка по nmID: {"title", "vendor_code", "photos": [{big, c246x328, ...}, ...]}.

    photos — в порядке галереи WB, photos[0] = главное фото. None — карточка не найдена.
    """
    body = {
        "settings": {
            "cursor": {"limit": 10},
            "filter": {"textSearch": str(nm_id), "withPhoto": -1},
        }
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(f"{_BASE}/content/v2/get/cards/list", headers=_headers(api_key), json=body)
    if resp.status_code != 200:
        raise WbContentError(f"WB Content API: HTTP {resp.status_code} при чтении карточки")
    cards = (resp.json() or {}).get("cards") or []
    for card in cards:
        # textSearch ищет и по артикулу продавца — сверяем nmID строго
        if card.get("nmID") == nm_id:
            return {
                "title": card.get("title") or "",
                "vendor_code": card.get("vendorCode") or "",
                "photos": card.get("photos") or [],
            }
    return None


async def upload_main_photo(api_key: str, nm_id: int, content: bytes, filename: str = "photo.jpg") -> None:
    """Заменить главное фото карточки (X-Photo-Number=1) загрузкой файла.

    WB обрабатывает фото асинхронно: 200 = принято, на витрине сменится с задержкой
    (CDN). Ошибки (4xx/5xx после ретрая на 429) → WbContentError.
    """
    headers = {
        "Authorization": f"Bearer {api_key}",
        "X-Nm-Id": str(nm_id),
        "X-Photo-Number": "1",
    }
    files = {"uploadfile": (filename, content, "image/jpeg")}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        for attempt in range(2):
            try:
                resp = await client.post(f"{_BASE}/content/v3/media/file", headers=headers, files=files)
            except httpx.HTTPError as e:
                raise WbContentError(f"WB Content API: сетевая ошибка загрузки фото — {e}") from e
            if resp.status_code == 429 and attempt == 0:
                await asyncio.sleep(15)
                continue
            break
    if resp.status_code != 200:
        detail = ""
        try:
            detail = (resp.json() or {}).get("errorText") or ""
        except ValueError:
            pass
        raise WbContentError(f"WB Content API: HTTP {resp.status_code} при загрузке фото. {detail}".strip())
