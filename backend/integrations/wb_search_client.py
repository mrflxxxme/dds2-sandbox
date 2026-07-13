"""
Клиент публичного поиска WB (search.wb.ru) — органическая позиция товара по фразе.

НЕ официальный API, но публичный (ключ не нужен): та же выдача, что видит покупатель.
Хост жёстко зафиксирован → SSRF нет. Используется кластеризатором для колонок
«Позиция»/«Была»: ищем фразу постранично (100 товаров/стр.) и находим ранг nmId.

Регион доставки (`dest`) влияет на выдачу — по умолчанию Москва.
"""

import asyncio

import httpx

SEARCH_URL = "https://search.wb.ru/exactmatch/ru/common/v9/search"
DEST_DEFAULT = -1257786  # Москва (dest влияет на порядок выдачи)
PER_PAGE = 100
TIMEOUT = 15
_RETRIES = 5  # WB-поиск жёстко троттлит по IP (429) — ретраим страницу с бэкоффом
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


async def _fetch_page(client: httpx.AsyncClient, phrase: str, page: int, dest: int) -> list | None:
    """Одна страница выдачи. list товаров при 200; None — не удалось получить (после ретраев).

    Различаем: None = транзиентный сбой/троттлинг (не помечать «не найдено»), [] = 200 с
    пустым списком = конец выдачи. WB отдаёт 429 (HTML) при превышении лимита — уважаем
    Retry-After, иначе экспонента (кап 8с).
    """
    params = {
        "appType": 1, "curr": "rub", "dest": dest, "query": phrase,
        "resultset": "catalog", "sort": "popular", "spp": 30, "page": page, "lang": "ru",
    }
    for attempt in range(_RETRIES):
        try:
            r = await client.get(SEARCH_URL, params=params, headers=_HEADERS, timeout=TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                return (data.get("data") or {}).get("products") or data.get("products") or []
            if r.status_code == 429:
                ra = r.headers.get("Retry-After", "")
                delay = float(ra) if ra.isdigit() else min(2 ** attempt, 8)
                await asyncio.sleep(delay)
                continue
        except (httpx.HTTPError, ValueError):
            pass
        await asyncio.sleep(0.6 * (attempt + 1))
    return None


async def find_position(
    client: httpx.AsyncClient,
    nm_id: int,
    phrase: str,
    max_pages: int = 5,
    dest: int = DEST_DEFAULT,
) -> tuple[int | None, int]:
    """Ранг nmId в органической выдаче по фразе.

    Возвращает (position | None, depth): position — 1-based место (None = не найден в
    пределах проверенной глубины); depth — сколько позиций реально проверено. Идём
    постранично до max_pages либо до последней (неполной) страницы.
    """
    depth = 0
    for page in range(1, max_pages + 1):
        products = await _fetch_page(client, phrase, page, dest)
        if products is None:
            break  # страница недоступна после ретраев — не выдумываем «не найдено»
        if not products:
            break  # 200 с пустым списком = конец выдачи
        for i, p in enumerate(products):
            if p.get("id") == nm_id:
                return depth + i + 1, depth + len(products)
        depth += len(products)
        if len(products) < PER_PAGE:
            break  # последняя страница выдачи
    return None, depth
