# ruff: noqa: RUF001, RUF002, RUF003
"""Публичный card-API Wildberries (без ключа) — реальная цена покупателя с СПП.

Сидельский API «Цены и скидки» отдаёт только цену ДО СПП (discountedPrice) и
цену WB-Клуба (clubDiscountedPrice). Реальную цену, которую видит покупатель
СЕЙЧАС (с обычной СПП), публикует только публичный card-API:
  GET https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=<dest>&nm=<id;id;…>
  → data.products[].sizes[].price.product  (в копейках) = цена с СПП.

Неофициальный и нестабильный: версия дрейфует (v2→v4), узлы CDN иногда отдают
пустой ответ → батчи + ретраи. СПП теперь единый по регионам, поэтому dest
фиксирован.

ПОЧЕМУ НЕ httpx. Антибот WB (Angie) режет по TLS-отпечатку клиента: httpx —
403 Forbidden на любых заголовках, хостах и версиях HTTP, и с сервера, и с
локальной машины. Отсюда ходим через curl_cffi (libcurl с подменой отпечатка).
Замер 2026-08-01: профили `chrome99/chrome110/edge101` и голый libcurl дают 200,
а `chrome120/chrome124/safari*` — те же 403, поэтому список профилей
консервативный и на 403 мы перебираем следующий.

Пока этого не было, синк молча получал ноль строк, а «Цена с СПП» на странице
«Ценообразование» жила на фолбэке из BDR — среднем СПП за 30 дней, которое по
устройству не может отреагировать на сегодняшнюю цену.
"""

import asyncio
import json
import logging

logger = logging.getLogger("dds.pricing")

_CARD_URL = "https://card.wb.ru/cards/v4/detail"
_DEFAULT_DEST = -1257786  # любой валидный dest даёт ту же цену (СПП единый по РФ)
_BATCH = 50
_CONCURRENCY = 1  # последовательно: card-API шейпит частые/параллельные вызовы (пустые 200)
_RETRIES = 4
_INTER_REQUEST_DELAY = 0.4  # пауза между батчами — не триггерить анти-бот WB
#: Отпечатки в порядке предпочтения; "" — голый libcurl (тоже проходит).
_PROFILES = ("chrome110", "chrome99", "edge101", "")


def parse_card_products(data: dict) -> dict[int, dict]:
    """JSON card-API → {nm_id: {"product": ₽, "basic": ₽|None}}.

    product — цена покупателя с СПП; basic — цена продавца ДО seller-скидки
    (зачёркнутая). Цены в ответе в копейках (×100). Берём первый размер с
    ненулевым product.

    Форма ответа у v4 дрейфует: раньше товары лежали под `data.products`, сейчас
    (проверено 2026-08-01) приходят на верхнем уровне — читаем обе, иначе синк
    молча забирает ноль строк и «Цена с СПП» тихо уезжает на BDR-фолбэк.
    """
    out: dict[int, dict] = {}
    d = data or {}
    products = d.get("products") or (d.get("data") or {}).get("products") or []
    for p in products:
        nm = p.get("id")
        if nm is None:
            continue
        chosen = None
        for s in p.get("sizes") or []:
            pr = s.get("price") or {}
            if pr.get("product"):
                chosen = pr
                break
        if not chosen:
            continue
        product = chosen.get("product")
        basic = chosen.get("basic")
        if not product:
            continue
        out[int(nm)] = {
            "product": round(product / 100, 2),
            "basic": round(basic / 100, 2) if basic else None,
        }
    return out


async def fetch_card_buyer_prices(
    nm_ids: list[int], dest: int = _DEFAULT_DEST, batch_size: int = _BATCH
) -> dict[int, float]:
    """nm_id → цена покупателя с СПП (₽). Тонкая обёртка над `fetch_card_prices`."""
    full = await fetch_card_prices(nm_ids, dest=dest, batch_size=batch_size)
    return {nm: info["product"] for nm, info in full.items()}


async def fetch_card_prices(
    nm_ids: list[int], dest: int = _DEFAULT_DEST, batch_size: int = _BATCH
) -> dict[int, dict]:
    """nm_id → {"product": цена клиента ₽, "basic": цена до seller-скидки ₽|None}.

    Батчи + ретраи против флака CDN. Возвращает только успешно полученные nm;
    провалившиеся батчи пропускаются (best-effort), чтобы один пустой ответ не
    валил весь синк.
    """
    if not nm_ids:
        return {}
    from curl_cffi.requests import AsyncSession  # тяжёлый импорт — лениво

    uniq = list(dict.fromkeys(nm_ids))
    batches = [uniq[i : i + batch_size] for i in range(0, len(uniq), batch_size)]
    result: dict[int, dict] = {}
    sem = asyncio.Semaphore(_CONCURRENCY)
    profile = _PROFILES[0]  # рабочий отпечаток запоминаем на весь прогон

    async with AsyncSession(timeout=15.0) as client:

        async def one(batch: list[int]) -> dict[int, dict]:
            nonlocal profile
            # raw URL — сохраняем «;» как разделитель (params-энкодер его кодирует)
            nmstr = ";".join(str(n) for n in batch)
            url = f"{_CARD_URL}?appType=1&curr=rub&dest={dest}&nm={nmstr}"
            for attempt in range(_RETRIES):
                async with sem:
                    try:
                        r = await client.get(url, impersonate=profile or None)
                        if r.status_code == 200:
                            parsed = parse_card_products(json.loads(r.text))
                            if parsed:
                                return parsed
                        elif r.status_code == 403:
                            # антибот отверг отпечаток — берём следующий из списка
                            nxt = _PROFILES[(_PROFILES.index(profile) + 1) % len(_PROFILES)]
                            logger.warning("card-API 403 на профиле %r → пробуем %r", profile, nxt)
                            profile = nxt
                    except (OSError, ValueError) as exc:
                        logger.debug("card-API batch retry %d: %s", attempt + 1, exc)
                await asyncio.sleep(0.6 * (attempt + 1))
            return {}

        async def one_spaced(batch: list[int]) -> dict[int, dict]:
            res = await one(batch)
            await asyncio.sleep(_INTER_REQUEST_DELAY)
            return res

        maps = await asyncio.gather(*[one_spaced(b) for b in batches])

    for m in maps:
        result.update(m)
    return result
