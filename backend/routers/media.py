"""Публичные медиа-эндпоинты (без авторизации).

Картинки товаров WB публичны, а тег <img> не может слать JWT — поэтому отдаём
кэш фото товара без auth. Хост запроса к WB жёстко фиксирован (basket-NN.wbbasket.ru),
nm_id — целое число, так что SSRF-вектора нет.
"""

import logging

from fastapi import APIRouter, HTTPException, Response

from backend.services.funnel.product_images import get_or_fetch

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media")


@router.get("/product-image/{nm_id}")
async def get_product_image(nm_id: int):
    """Главное фото товара WB из нашего кэша (MinIO). Первый запрос резолвит basket-хост,
    скачивает и сохраняет; дальше отдаётся из хранилища (не грузим с WB каждый раз)."""
    if nm_id <= 0:
        raise HTTPException(400, "bad nm_id")
    data = await get_or_fetch(nm_id)
    if not data:
        raise HTTPException(404, "image not found")
    return Response(
        content=data,
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=604800"},
    )
