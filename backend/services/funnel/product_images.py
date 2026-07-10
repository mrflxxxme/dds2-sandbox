"""Кэш главных фото товаров WB.

Публичные картинки WB (basket-CDN) резолвятся один раз, сохраняются в MinIO и
дальше отдаются из нашего хранилища — чтобы не грузить их с сайта WB каждый раз
и не зависеть от угадывания basket-хоста (серые плейсхолдеры).
"""

import io
import logging
import math

import aiohttp
import httpx

from backend.config import settings
from backend.storage import get_minio

logger = logging.getLogger(__name__)

_PREFIX = "product-images/"
_SIZE = "c246x328"  # компактный размер карточки WB

# Известные (неравномерные) границы vol → номер basket-хоста WB.
_BASKET_RANGES: list[tuple[int, int]] = [
    (143, 1), (287, 2), (431, 3), (719, 4), (1007, 5), (1061, 6), (1115, 7), (1169, 8),
    (1313, 9), (1601, 10), (1655, 11), (1919, 12), (2045, 13), (2189, 14), (2405, 15),
    (2621, 16), (2837, 17), (3053, 18), (3269, 19), (3485, 20), (3701, 21), (3917, 22),
    (4133, 23), (4349, 24), (4565, 25), (4877, 26), (5189, 27), (5501, 28), (5813, 29), (6125, 30),
]


def _candidate_basket(vol: int) -> int:
    for max_vol, host in _BASKET_RANGES:
        if vol <= max_vol:
            return host
    return 30 + math.ceil((vol - 6125) / 312)


def _url(nm_id: int, basket: int) -> str:
    vol = nm_id // 100000
    part = nm_id // 1000
    host = f"{basket:02d}"
    return f"https://basket-{host}.wbbasket.ru/vol{vol}/part{part}/{nm_id}/images/{_SIZE}/1.webp"


def _key(nm_id: int) -> str:
    return f"{_PREFIX}{nm_id}.webp"


async def get_cached(nm_id: int) -> bytes | None:
    """Отдать байты из MinIO, если уже сохранены; иначе None."""
    client = await get_minio()
    if client is None:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            resp = await client.get_object(settings.MINIO_BUCKET, _key(nm_id), session)
            data: bytes = await resp.read()
            resp.close()
        return data
    except Exception:
        return None


async def _download_from_wb(nm_id: int) -> bytes | None:
    """Найти рабочий basket-хост и скачать фото. Сначала — вычисленный кандидат,
    затем ограниченный перебор (для nm_id, где формула промахивается)."""
    vol = nm_id // 100000
    candidate = _candidate_basket(vol)
    order: list[int] = [candidate]
    for n in range(1, 41):  # фолбэк-скан, cap 40
        if n not in order:
            order.append(n)
    async with httpx.AsyncClient(timeout=6.0, follow_redirects=False) as client:
        for basket in order:
            try:
                r = await client.get(_url(nm_id, basket))
            except httpx.HTTPError:
                continue
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                return r.content
    return None


async def fetch_and_cache(nm_id: int) -> bytes | None:
    """Скачать фото с WB и положить в MinIO. Возвращает байты или None."""
    data = await _download_from_wb(nm_id)
    if not data:
        return None
    client = await get_minio()
    if client is not None:
        try:
            await client.put_object(
                settings.MINIO_BUCKET, _key(nm_id), io.BytesIO(data),
                length=len(data), content_type="image/webp",
            )
        except Exception as e:  # noqa: BLE001 — кэш не критичен, отдаём байты всё равно
            logger.warning("product image cache put failed nm=%s: %s", nm_id, e)
    return data


async def get_or_fetch(nm_id: int) -> bytes | None:
    """Главная точка: из кэша, иначе скачать+сохранить."""
    cached = await get_cached(nm_id)
    if cached is not None:
        return cached
    return await fetch_and_cache(nm_id)
