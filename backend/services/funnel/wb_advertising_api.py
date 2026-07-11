"""WB Advertising API — campaign management and stats.

Handles:
- fetch_ad_campaigns: list campaign IDs by status
- fetch_ad_stats: detailed per-nmId per-date ad stats with time budget
"""

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("dds.funnel")


async def fetch_ad_campaigns(api_key: str, include_completed: bool = False) -> list[int]:
    """Get list of ad campaign IDs.

    include_completed=False: status 9 (active) + 11 (paused) — for daily sync.
    include_completed=True: also includes status 7 (completed) — for historical resync.
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            "https://advert-api.wildberries.ru/adv/v1/promotion/count",
            headers=headers,
        )
        if resp.status_code != 200:
            logger.error(f"WB adv count error {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        allowed = ("7", "9", "11") if include_completed else ("9", "11")
        campaign_ids = []
        for adv in data.get("adverts") or []:
            status = str(adv.get("status", ""))
            if status in allowed:
                for compa in adv.get("advert_list") or []:
                    cid = compa.get("advertId")
                    if cid and cid not in campaign_ids:
                        campaign_ids.append(cid)
        logger.info(
            f"WB ad campaigns: {len(campaign_ids)} "
            f"({'incl. completed' if include_completed else 'active+paused only'})"
        )
        return campaign_ids


async def check_advert_scope(api_key: str) -> str:
    """Probe whether an API token has the «Продвижение» (advertising) scope.

    Calls GET /adv/v1/promotion/count on the WB advert API. Returns:
        "ok"       — HTTP 200: token is valid and has advert access.
        "no_scope" — HTTP 401/403: token rejected by the advert API (no scope).
        "unknown"  — 429/5xx/network error: could not verify right now (rate-limit
                     or transient). Caller MUST NOT treat this as an invalid key —
                     this endpoint is aggressively rate-limited and shares the
                     per-account limit with ads_autopay / ad_campaigns sync.
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                "https://advert-api.wildberries.ru/adv/v1/promotion/count",
                headers=headers,
            )
    except httpx.HTTPError as e:
        logger.warning(f"Advert scope check: network error, cannot verify — {e}")
        return "unknown"

    if resp.status_code == 200:
        return "ok"
    if resp.status_code in (401, 403):
        logger.info(f"Advert scope check: token rejected ({resp.status_code}) — no Продвижение scope")
        return "no_scope"
    logger.warning(f"Advert scope check: transient {resp.status_code} from WB advert API, cannot verify")
    return "unknown"


async def fetch_ad_stats(
    api_key: str,
    campaign_ids: list[int],
    begin_date: str,
    end_date: str,
    *,
    time_budget: float | None = 300,
    chunk_pause: float = 5,
    max_429_retries: int = 2,
) -> dict:
    """Fetch detailed ad stats per nmId per date.
    Returns {date: {nm_id: {sum, clicks, views}}} — суммарно по всем кампаниям.

    Служебные ключи (начинаются с "_", итерируя result по датам — пропускай их):
    - result["_by_campaign"]    = {date: {campaign_id: {sum, clicks, views}}}
    - result["_by_nm_campaign"] = {date: {campaign_id: {nm_id: {...полный набор WB}}}}
    - result["_skipped_chunks"] = сколько чанков кампаний не удалось получить

    Полный набор WB на уровне nm: views, clicks, sum (затраты), atbs (корзины),
    orders и shks (заказы/штуки, атрибуция рекламы), sum_price (сумма заказов ₽).

    WB API limit: max 31 days per request. Automatically splits into windows.

    Темп задаётся вызывающим:
    - интерактивный синк — `time_budget=300` (лучше частичные данные, чем висящий запрос);
    - фоновый бэкфилл — `time_budget=None`, длинные `chunk_pause` и больше ретраев на 429
      (полнота важнее скорости; данные копим у себя, второй раз их взять неоткуда).
    """
    from datetime import datetime, timedelta

    # Split date range into ≤31-day windows
    d_start = datetime.strptime(begin_date, "%Y-%m-%d").date()
    d_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    windows: list[tuple[str, str]] = []
    w_start = d_start
    while w_start <= d_end:
        w_end = min(w_start + timedelta(days=30), d_end)  # 31 days = 0..30
        windows.append((w_start.isoformat(), w_end.isoformat()))
        w_start = w_end + timedelta(days=1)

    logger.info(f"WB adv stats: {begin_date}→{end_date} split into {len(windows)} window(s)")

    result: dict[str, dict[int, dict]] = {}
    by_campaign: dict[str, dict[int, dict]] = {}  # date -> campaign_id -> stats
    by_nm_campaign: dict[str, dict[int, dict[int, dict]]] = {}  # date -> campaign_id -> nm_id -> stats
    chunks = [campaign_ids[i : i + 50] for i in range(0, len(campaign_ids), 50)]
    skipped_chunks = 0
    budget_exceeded = False
    TIME_BUDGET = time_budget if time_budget is not None else float("inf")
    t_start = time.monotonic()

    async with httpx.AsyncClient(timeout=60) as client:
        for win_begin, win_end in windows:
            if budget_exceeded:
                break

            for idx, chunk in enumerate(chunks):
                elapsed = time.monotonic() - t_start
                if elapsed >= TIME_BUDGET:
                    remaining = len(chunks) - idx
                    logger.warning(
                        f"WB adv: time budget {TIME_BUDGET}s exceeded after "
                        f"{elapsed:.0f}s — skipping {remaining} remaining chunks "
                        f"({win_begin}→{win_end})"
                    )
                    skipped_chunks += remaining
                    budget_exceeded = True
                    break

                if idx > 0 or win_begin != windows[0][0]:
                    await asyncio.sleep(chunk_pause)

                ids_param = ",".join(str(c) for c in chunk)
                url = (
                    f"https://advert-api.wildberries.ru/adv/v3/fullstats"
                    f"?ids={ids_param}&beginDate={win_begin}&endDate={win_end}"
                )

                chunk_ok = False
                for attempt in range(max_429_retries):
                    try:
                        resp = await client.get(
                            url,
                            headers={
                                "Accept": "application/json",
                                "Authorization": f"Bearer {api_key}",
                            },
                        )
                    except Exception as e:
                        logger.warning(f"Ad stats request failed: {e}")
                        break

                    if resp.status_code == 429:
                        # Уважаем Retry-After, иначе экспонента 20/40/80… (капаем на 120с)
                        try:
                            wait = float(resp.headers.get("Retry-After", "") or 0)
                        except ValueError:
                            wait = 0
                        wait = wait or min(20 * 2**attempt, 120)
                        logger.warning(
                            f"WB adv 429 rate limit, waiting {wait:.0f}s "
                            f"(attempt {attempt+1}/{max_429_retries}, elapsed {time.monotonic()-t_start:.0f}s)"
                        )
                        if time.monotonic() - t_start + wait >= TIME_BUDGET:
                            logger.warning(
                                f"WB adv: sleep {wait}s would exceed budget, " f"skipping chunk {idx+1}/{len(chunks)}"
                            )
                            break
                        await asyncio.sleep(wait)
                        continue
                    elif resp.status_code != 200:
                        logger.error(f"WB adv stats error {resp.status_code}: {resp.text[:200]}")
                        break

                    data = resp.json()
                    if data is None:
                        logger.warning(f"WB adv: empty JSON response for chunk {idx+1}, skipping")
                        break
                    items = data if isinstance(data, list) else (data.get("data") or data)
                    if not isinstance(items, list):
                        break

                    for campaign in items:
                        if not campaign or not isinstance(campaign, dict):
                            continue
                        camp_id = campaign.get("advertId") or campaign.get("id")
                        for day in campaign.get("days") or []:
                            res_date = (day.get("date") or "")[:10]
                            if not res_date:
                                continue
                            if res_date not in result:
                                result[res_date] = {}

                            day_sum = 0
                            day_clicks = 0
                            day_views = 0

                            for app in day.get("apps") or []:
                                for nm in app.get("nms") or []:
                                    nm_id = nm.get("nmId")
                                    if not nm_id:
                                        continue
                                    s = nm.get("sum", 0)
                                    cl = nm.get("clicks", 0)
                                    v = nm.get("views", 0)
                                    if nm_id not in result[res_date]:
                                        result[res_date][nm_id] = {"sum": 0, "clicks": 0, "views": 0}
                                    result[res_date][nm_id]["sum"] += s
                                    result[res_date][nm_id]["clicks"] += cl
                                    result[res_date][nm_id]["views"] += v
                                    day_sum += s
                                    day_clicks += cl
                                    day_views += v

                                    # Разбивка кампания × товар: суммируем по площадкам (apps)
                                    if camp_id:
                                        nm_slot = by_nm_campaign.setdefault(res_date, {}).setdefault(camp_id, {})
                                        cell = nm_slot.setdefault(
                                            nm_id,
                                            {
                                                "views": 0,
                                                "clicks": 0,
                                                "sum": 0.0,
                                                "atbs": 0,
                                                "orders": 0,
                                                "shks": 0,
                                                "sum_price": 0.0,
                                            },
                                        )
                                        cell["views"] += v
                                        cell["clicks"] += cl
                                        cell["sum"] += s
                                        cell["atbs"] += nm.get("atbs", 0) or 0
                                        cell["orders"] += nm.get("orders", 0) or 0
                                        cell["shks"] += nm.get("shks", 0) or 0
                                        cell["sum_price"] += nm.get("sum_price", 0) or 0

                            # Per-campaign daily aggregation
                            if camp_id and (day_sum or day_clicks or day_views):
                                if res_date not in by_campaign:
                                    by_campaign[res_date] = {}
                                if camp_id not in by_campaign[res_date]:
                                    by_campaign[res_date][camp_id] = {"sum": 0, "clicks": 0, "views": 0}
                                by_campaign[res_date][camp_id]["sum"] += day_sum
                                by_campaign[res_date][camp_id]["clicks"] += day_clicks
                                by_campaign[res_date][camp_id]["views"] += day_views
                    chunk_ok = True
                    break

                if not chunk_ok:
                    skipped_chunks += 1
                    logger.warning(
                        f"WB adv: chunk {idx+1}/{len(chunks)} SKIPPED after retries "
                        f"({len(chunk)} campaigns, {win_begin}→{win_end})"
                    )

    elapsed_total = time.monotonic() - t_start
    if skipped_chunks:
        logger.warning(
            f"WB adv: {skipped_chunks} chunks skipped "
            f"for {begin_date}→{end_date} (elapsed {elapsed_total:.0f}s, "
            f"budget_exceeded={budget_exceeded})"
        )
    else:
        logger.info(f"WB adv: all chunks OK " f"for {begin_date}→{end_date} in {elapsed_total:.0f}s")

    result["_by_campaign"] = by_campaign  # type: ignore[assignment]
    result["_by_nm_campaign"] = by_nm_campaign  # type: ignore[assignment]
    result["_skipped_chunks"] = skipped_chunks  # type: ignore[assignment]
    return result


def _parse_advert_item(c: dict) -> dict | None:
    """Элемент ответа WB /api/advert/v2/adverts → нормализованный dict кампании.

    settings.payment_type ('cpm'/'cpc') → campaign_type;
    bid_type ('unified'=единая / 'manual'=ручная) → bid_mode (для CPM; у CPC тоже приходит).
    """
    if not isinstance(c, dict):
        return None
    nm_ids: list[int] = []
    # New API: nm_settings[].nm_id
    for ns in c.get("nm_settings") or []:
        if isinstance(ns, dict) and ns.get("nm_id"):
            nm_ids.append(ns["nm_id"])
    # Fallback: params[].nms[]
    if not nm_ids:
        for param in c.get("params") or []:
            if isinstance(param, dict):
                nm_ids.extend(param.get("nms") or [])
    # Name and payment_type from settings
    name = None
    payment_type = None
    settings = c.get("settings")
    if isinstance(settings, dict):
        name = settings.get("name")
        payment_type = settings.get("payment_type")
    if not name:
        name = c.get("name")
    # ID: "id" or "advertId"
    advert_id = c.get("id") or c.get("advertId")
    # Числовой тип рекламы WB (8=авто/рекомендации, 9=аукцион и т.п.) — для цветовой кодировки.
    # Хранится отдельно от payment_type, который перетирает "type" ниже.
    raw_type = c.get("type")
    advert_type = raw_type if isinstance(raw_type, int) else None
    return {
        "advertId": advert_id,
        "name": name or str(advert_id or ""),
        # campaign_type: payment_type ('cpm'/'cpc'); фолбэк на bid_type только если payment_type пуст
        "type": payment_type or c.get("bid_type"),
        "advert_type": advert_type,
        "create_time": c.get("createTime"),  # ISO-строка создания кампании в WB (дата добавления)
        # bid_mode: режим ставки WB — 'unified'/'manual' как есть (совпадает с контрактом фронта)
        "bid_mode": c.get("bid_type"),
        "status": c.get("status"),
        "nm_ids": nm_ids,
    }


async def fetch_campaign_placements(api_key: str, campaign_id: int) -> dict:
    """Зоны показов кампании и ставки по каждой из них.

    GET /api/advert/v2/adverts?ids={id}:
    - settings.placements = {"search": bool, "recommendations": bool} — включена ли зона;
    - nm_settings[].bids_kopecks = {"search": коп., "recommendations": коп.}.

    Ставка у товаров кампании обычно одна; при расхождении берём максимальную.
    Возвращает {"placements": {...}, "bids": {"search": ₽|None, "recommendations": ₽|None}}.
    """
    empty = {"placements": {}, "bids": {"search": None, "recommendations": None}}
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"https://advert-api.wildberries.ru/api/advert/v2/adverts?ids={campaign_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.warning(f"WB adverts detail {resp.status_code}: {resp.text[:200]}")
            return empty
        adverts = (resp.json() or {}).get("adverts") or []
    except Exception as e:
        logger.warning(f"WB adverts detail failed: {e}")
        return empty

    placements: dict[str, bool] = {}
    kopecks: dict[str, list[int]] = {"search": [], "recommendations": []}
    for adv in adverts:
        settings = adv.get("settings") or {}
        for zone, on in (settings.get("placements") or {}).items():
            placements[zone] = bool(on)
        for ns in adv.get("nm_settings") or []:
            bids = ns.get("bids_kopecks") or {}
            for zone in kopecks:
                if bids.get(zone):
                    kopecks[zone].append(int(bids[zone]))
    return {
        "placements": placements,
        "bids": {z: (round(max(v) / 100, 2) if v else None) for z, v in kopecks.items()},
    }


async def set_campaign_placements(api_key: str, campaign_id: int, placements: dict[str, bool]) -> dict:
    """PUT /adv/v0/auction/placements — включить/выключить зоны показов кампании.

    Тело: {"placements": [{"advert_id": id, "placements": {"search": bool, "recommendations": bool}}]}.
    Успех — 204 без тела.

    WB разрешает менять зоны ТОЛЬКО у кампаний с ручной ставкой (bid_type=manual)
    и оплатой за показы (cpm), в статусах 4/9/11. Проверку типа делает вызывающий
    (`ads_manager.set_campaign_zones`) — WB на нарушение отвечает 400.
    Лимит WB — 1 запрос в секунду, поэтому 429 повторяем один раз.

    Возвращает {"ok": bool, "error": str | None}.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = "https://advert-api.wildberries.ru/adv/v0/auction/placements"
    body = {"placements": [{"advert_id": campaign_id, "placements": placements}]}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(url, headers=headers, json=body)
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After", "2")
                await asyncio.sleep(int(ra) if ra.isdigit() else 2)
                resp = await client.put(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        logger.warning(f"WB set placements failed for {campaign_id}: {e}")
        return {"ok": False, "error": str(e)[:300]}

    if resp.status_code in (200, 204):
        logger.info(f"WB placements updated: campaign {campaign_id} → {placements}")
        return {"ok": True, "error": None}
    if resp.status_code in (401, 403):
        logger.warning(f"WB set placements forbidden {resp.status_code} for {campaign_id} — read-only токен?")
        return {"ok": False, "error": "Нет доступа: токен «Продвижение» должен быть с правом записи (не read-only)."}
    err = resp.text[:300]
    logger.warning(f"WB set placements rejected {resp.status_code} for {campaign_id}: {err}")
    return {"ok": False, "error": f"HTTP {resp.status_code}: {err}"}


async def set_campaign_bid(
    api_key: str, campaign_id: int, nm_ids: list[int], bid_rub: float, placement: str
) -> dict:
    """PATCH /api/advert/v1/bids — сменить ставку кампании по её товарам.

    placement: "combined" (единая ставка), "search"/"recommendations" (ручная), для CPC — за клик.
    Тело: {"bids":[{"advert_id":id,"nm_bids":[{"nm_id","bid_kopecks","placement"} ...]}]}. Ставка —
    в копейках (₽×100). WB: только статусы 4/9/11; проверку типа/статуса делает вызывающий.
    Лимит WB — 1 запрос/сек, 429 повторяем один раз. Возвращает {"ok","error","bid"}.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = "https://advert-api.wildberries.ru/api/advert/v1/bids"
    kop = int(round(bid_rub * 100))
    nm_bids = [{"nm_id": nm, "bid_kopecks": kop, "placement": placement} for nm in nm_ids]
    body = {"bids": [{"advert_id": campaign_id, "nm_bids": nm_bids}]}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(url, headers=headers, json=body)
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After", "1")
                await asyncio.sleep(int(ra) if ra.isdigit() else 1)
                resp = await client.patch(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        logger.warning(f"WB set bid failed for {campaign_id}: {e}")
        return {"ok": False, "error": str(e)[:300], "bid": None}

    if resp.status_code == 200:
        logger.info(f"WB bid updated: campaign {campaign_id} → {bid_rub} ₽ ({placement})")
        return {"ok": True, "error": None, "bid": round(kop / 100, 2)}
    if resp.status_code in (401, 403):
        logger.warning(f"WB set bid forbidden {resp.status_code} for {campaign_id} — read-only токен?")
        return {"ok": False, "error": "Нет доступа: токен «Продвижение» должен быть с правом записи (не read-only).", "bid": None}
    err = resp.text[:300]
    logger.warning(f"WB set bid rejected {resp.status_code} for {campaign_id}: {err}")
    return {"ok": False, "error": f"HTTP {resp.status_code}: {err}", "bid": None}


async def fetch_campaign_default_bid(api_key: str, campaign_id: int) -> float | None:
    """Ставка кампании по умолчанию (₽) — по активной зоне (поиск приоритетнее)."""
    info = await fetch_campaign_placements(api_key, campaign_id)
    pl, bids = info["placements"], info["bids"]
    # Если поиск выключен, действует ставка рекомендаций
    if pl.get("search", True) and bids.get("search") is not None:
        return bids["search"]
    return bids.get("recommendations") or bids.get("search")


async def fetch_ad_campaigns_detailed(
    api_key: str,
    include_completed: bool = False,
) -> list[dict]:
    """Get campaign details: name, type, status, linked nmIds.

    1. Get campaign IDs via fetch_ad_campaigns()
    2. Fetch details via GET /api/advert/v2/adverts?ids=... (chunks of 50)
    """
    campaign_ids = await fetch_ad_campaigns(
        api_key,
        include_completed=include_completed,
    )
    if not campaign_ids:
        return []

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    campaigns: list[dict] = []
    chunks = [campaign_ids[i : i + 50] for i in range(0, len(campaign_ids), 50)]

    async with httpx.AsyncClient(timeout=30) as client:
        for idx, chunk in enumerate(chunks):
            if idx > 0:
                await asyncio.sleep(0.3)
            try:
                ids_param = ",".join(str(c) for c in chunk)
                resp = await client.get(
                    "https://advert-api.wildberries.ru" f"/api/advert/v2/adverts?ids={ids_param}",
                    headers=headers,
                )
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", "10"))
                    logger.warning(f"WB adv details 429, waiting {wait}s")
                    await asyncio.sleep(wait)
                    continue
                if resp.status_code != 200:
                    logger.error(f"WB adv details error {resp.status_code}: " f"{resp.text[:200]}")
                    continue

                data = resp.json()
                # Response: {"adverts": [...]} or [...]
                items = data
                if isinstance(data, dict):
                    items = data.get("adverts") or []
                if not isinstance(items, list):
                    continue

                for c in items:
                    parsed = _parse_advert_item(c)
                    if parsed is not None:
                        campaigns.append(parsed)
            except Exception as e:
                logger.warning(f"WB adv details chunk {idx + 1} failed: {e}")

    logger.info(f"WB ad campaigns detailed: {len(campaigns)} fetched")
    return campaigns


async def fetch_campaign_budgets_batch(
    api_key: str,
    campaign_ids: list[int],
    progress_cb: object = None,
) -> dict:
    """Fetch remaining budget for each campaign.

    GET /adv/v1/budget?id={campaign_id}
    Returns {campaign_id: budget_value}.
    Time budget: 120 seconds.
    progress_cb: optional callback(done, total) for progress tracking.
    """
    from decimal import Decimal

    result: dict[int, Decimal] = {}
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    TIME_BUDGET = 600  # 10 min — enough for 750+ campaigns
    t_start = time.monotonic()

    async with httpx.AsyncClient(timeout=15) as client:
        for idx, cid in enumerate(campaign_ids):
            if progress_cb and callable(progress_cb):
                progress_cb(idx, len(campaign_ids))
            elapsed = time.monotonic() - t_start
            if elapsed >= TIME_BUDGET:
                logger.warning(
                    f"WB adv budget: time budget {TIME_BUDGET}s exceeded " f"after {idx}/{len(campaign_ids)} campaigns"
                )
                break

            if idx > 0:
                await asyncio.sleep(0.5)

            try:
                resp = await client.get(
                    f"https://advert-api.wildberries.ru/adv/v1/budget" f"?id={cid}",
                    headers=headers,
                )
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", "5"))
                    logger.warning(f"WB adv budget 429 for {cid}, waiting {wait}s")
                    await asyncio.sleep(wait)
                    resp = await client.get(
                        f"https://advert-api.wildberries.ru/adv/v1/budget" f"?id={cid}",
                        headers=headers,
                    )
                if resp.status_code != 200:
                    continue

                data = resp.json()
                total = data.get("total", 0) or data.get("cash", 0) or data.get("netting", 0)
                result[cid] = Decimal(str(total))
            except Exception as e:
                logger.warning(f"WB adv budget for {cid} failed: {e}")

    logger.info(f"WB ad budgets: {len(result)}/{len(campaign_ids)} fetched")
    return result


# ─── Финансы: баланс и пополнение бюджета (реальные деньги!) ─────────────────

# Источники пополнения WB (параметр type в /adv/v1/budget/deposit)
DEPOSIT_SOURCE_ACCOUNT = 0  # счёт кабинета Продвижения (пополняет продавец)
DEPOSIT_SOURCE_BALANCE = 1  # баланс взаиморасчёта (удержание из будущих продаж)

DEPOSIT_SOURCE_LABELS = {DEPOSIT_SOURCE_ACCOUNT: "счёт", DEPOSIT_SOURCE_BALANCE: "баланс"}


async def fetch_adv_balance(api_key: str) -> dict | None:
    """GET /adv/v1/balance — счёт/баланс/бонусы кабинета Продвижения.

    Ответ WB: {"balance": ..., "net": ..., "bonus": ..., "currency": "RUB"}.
    None — если запрос не удался (не роняем вызывающего).
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://advert-api.wildberries.ru/adv/v1/balance", headers=headers)
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                resp = await client.get("https://advert-api.wildberries.ru/adv/v1/balance", headers=headers)
            if resp.status_code != 200:
                logger.warning(f"WB adv balance error {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning(f"WB adv balance failed: {e}")
        return None


async def fetch_adv_upd(api_key: str, date_from: str, date_to: str) -> list[dict]:
    """GET /adv/v1/upd?from=&to= — история фактических затрат (списаний) за период.

    Даты — YYYY-MM-DD. Возвращает список списаний:
    [{updNum, updTime, updSum, advertId, campName, advertType, paymentType, advertStatus, currency}].
    204 (нет данных) → []. Ошибки не роняют вызывающего — возвращаем [].
    """
    return await _fetch_adv_history(api_key, "/adv/v1/upd", date_from, date_to)


async def fetch_adv_payments(api_key: str, date_from: str, date_to: str) -> list[dict]:
    """GET /adv/v1/payments?from=&to= — история пополнений счёта WB Продвижение.

    Возвращает список [{id, date, sum, type, statusId, currency, cardStatus}]. 204 → [].
    """
    return await _fetch_adv_history(api_key, "/adv/v1/payments", date_from, date_to)


async def _fetch_adv_history(api_key: str, path: str, date_from: str, date_to: str) -> list[dict]:
    """Общая обёртка для GET-эндпоинтов истории (upd/payments): период from/to, 204 → []."""
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"https://advert-api.wildberries.ru{path}"
    params = {"from": date_from, "to": date_to}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url, headers=headers, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                resp = await client.get(url, headers=headers, params=params)
        if resp.status_code == 204:
            return []
        if resp.status_code != 200:
            logger.warning(f"WB{path} error {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"WB{path} failed: {e}")
        return []


async def deposit_campaign_budget(api_key: str, campaign_id: int, sum_rub: int, source_type: int) -> dict:
    """POST /adv/v1/budget/deposit?id={campaign_id} — пополнение бюджета кампании.

    РЕАЛЬНОЕ СПИСАНИЕ ДЕНЕГ. Политика ретраев консервативная:
    - 429 — один повтор (списания не было);
    - 4xx — не ретраим, возвращаем ошибку (списания не было);
    - timeout/5xx — НЕ ретраим (неизвестно, прошло ли списание) → status "unknown",
      вызывающий обязан сверить бюджет перед новой попыткой.

    Возвращает {"ok": bool, "status": "ok"|"error"|"unknown",
                "total": новый бюджет | None, "error": текст | None}.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"https://advert-api.wildberries.ru/adv/v1/budget/deposit?id={campaign_id}"
    payload = {"sum": int(sum_rub), "type": int(source_type), "return": True}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200:
                total = None
                try:
                    body = resp.json()
                    raw_total = body.get("total") if isinstance(body, dict) else None
                    total = float(raw_total) if raw_total is not None else None
                except ValueError:
                    pass
                logger.info(f"WB adv deposit ok: campaign {campaign_id} +{sum_rub}₽ (type={source_type}), total={total}")
                return {"ok": True, "status": "ok", "total": total, "error": None}
            if 400 <= resp.status_code < 500:
                err = resp.text[:300]
                logger.warning(f"WB adv deposit rejected {resp.status_code} for {campaign_id}: {err}")
                return {"ok": False, "status": "error", "total": None, "error": f"HTTP {resp.status_code}: {err}"}
            # 5xx — исход неизвестен
            logger.error(f"WB adv deposit UNKNOWN outcome {resp.status_code} for {campaign_id}: {resp.text[:200]}")
            return {"ok": False, "status": "unknown", "total": None, "error": f"HTTP {resp.status_code}"}
    except httpx.TimeoutException:
        logger.error(f"WB adv deposit TIMEOUT for {campaign_id} — исход неизвестен, не ретраим")
        return {"ok": False, "status": "unknown", "total": None, "error": "timeout"}
    except Exception as e:
        logger.error(f"WB adv deposit failed for {campaign_id}: {e}")
        return {"ok": False, "status": "error", "total": None, "error": str(e)[:300]}


# ─── Управление кампанией (запуск / пауза / завершение / переим. / товары / ставки) ──

async def _adv_mutation(api_key: str, method: str, path: str, body: dict | None = None) -> dict:
    """Единая обёртка для write-эндпоинтов рекламы (rename/delete/bids/nms).

    200/204 → ok; 401/403 → read-only токен; 429 → один повтор; иначе HTTP {code}.
    Возвращает {"ok": bool, "error": str | None}. Для операций с деньгами (deposit)
    и состоянием (start/pause/stop) — свои функции с особой политикой.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"https://advert-api.wildberries.ru{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.request(method, url, headers=headers, json=body)
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                resp = await client.request(method, url, headers=headers, json=body)
    except httpx.HTTPError as e:
        logger.warning(f"WB {method} {path} failed: {e}")
        return {"ok": False, "error": str(e)[:300]}
    if resp.status_code in (200, 204):
        return {"ok": True, "error": None}
    if resp.status_code in (401, 403):
        return {"ok": False, "error": "Нет доступа: токен «Продвижение» должен быть с правом записи (не read-only)."}
    err = resp.text[:300]
    logger.warning(f"WB {method} {path} rejected {resp.status_code}: {err}")
    return {"ok": False, "error": f"HTTP {resp.status_code}: {err}"}


async def rename_campaign(api_key: str, campaign_id: int, name: str) -> dict:
    """POST /adv/v0/rename — переименовать кампанию (можно в любом статусе)."""
    return await _adv_mutation(api_key, "POST", "/adv/v0/rename", {"advertId": campaign_id, "name": name})


async def delete_campaign(api_key: str, campaign_id: int) -> dict:
    """GET /adv/v0/delete?id= — удалить кампанию (только статус 4 «готова»)."""
    return await _adv_mutation(api_key, "GET", f"/adv/v0/delete?id={campaign_id}")


async def set_card_bids(api_key: str, campaign_id: int, bids: list[dict]) -> dict:
    """PATCH /api/advert/v1/bids — ставки карточек товаров (статусы 4/9/11).

    bids — [{"nm_id": int, "bid_kopecks": int, "placement": "search"|"recommendations"}].
    """
    body = {"bids": [{"advert_id": campaign_id, "nm_bids": bids}]}
    return await _adv_mutation(api_key, "PATCH", "/api/advert/v1/bids", body)


async def change_campaign_nms(api_key: str, campaign_id: int, add: list[int], delete: list[int]) -> dict:
    """PATCH /adv/v0/auction/nms — добавить/убрать товары в кампании (статусы 4/9/11).

    Добавляемым WB ставит текущую минимальную ставку.
    """
    body = {"nms": [{"advert_id": campaign_id, "nms": {"add": add, "delete": delete}}]}
    return await _adv_mutation(api_key, "PATCH", "/adv/v0/auction/nms", body)


# ─── Создание кампании ───────────────────────────────────────────────────────

async def fetch_search_daily(api_key: str, campaign_id: int, nm_ids: list[int], date_from: str, date_to: str) -> dict:
    """POST /adv/v1/normquery/stats — посуточная статистика поисковых кластеров.

    Тело: {"from","to","items":[{"advertId","nmId"}]}. Возвращает по каждому товару
    dailyStats (кластер × день). Суммируем кластеры по (nm_id, date) — это зона «Поиск».

    Возвращает {(nm_id, "YYYY-MM-DD"): {views, clicks, spend, atbs, orders, shks}}.
    nmId обязателен — без него WB отдаёт items=null.
    """
    if not nm_ids:
        return {}
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = "https://advert-api.wildberries.ru/adv/v1/normquery/stats"
    body = {"from": date_from, "to": date_to, "items": [{"advertId": campaign_id, "nmId": nm} for nm in nm_ids]}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            logger.warning(f"WB normquery/stats {resp.status_code}: {resp.text[:200]}")
            return {}
        items = (resp.json() or {}).get("items") or []
    except Exception as e:
        logger.warning(f"WB normquery/stats failed: {e}")
        return {}

    agg: dict[tuple[int, str], dict] = {}
    for it in items:
        nm_id = it.get("nmId")  # nmId — на уровне item; в dailyStats его нет
        if nm_id is None:
            continue
        for day in it.get("dailyStats") or []:
            date_key = day.get("date")
            st = day.get("stat") or {}
            key = (int(nm_id), date_key)
            a = agg.setdefault(key, {"views": 0, "clicks": 0, "spend": 0.0, "atbs": 0, "orders": 0, "shks": 0})
            a["views"] += int(st.get("views") or 0)
            a["clicks"] += int(st.get("clicks") or 0)
            a["spend"] += float(st.get("spend") or 0)
            a["atbs"] += int(st.get("atbs") or 0)
            a["orders"] += int(st.get("orders") or 0)
            a["shks"] += int(st.get("shks") or 0)
    return agg


async def fetch_ad_subjects(api_key: str) -> list[dict]:
    """GET /adv/v1/supplier/subjects — предметы, по которым можно создать кампанию.

    Возвращает [{"id": int, "name": str, "count": int}] (count — сколько товаров в предмете).
    """
    return await _fetch_adv_history_get(api_key, "/adv/v1/supplier/subjects")


async def _fetch_adv_history_get(api_key: str, path: str) -> list[dict]:
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"https://advert-api.wildberries.ru{path}", headers=headers)
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                resp = await client.get(f"https://advert-api.wildberries.ru{path}", headers=headers)
        if resp.status_code == 204:
            return []
        if resp.status_code != 200:
            logger.warning(f"WB{path} error {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"WB{path} failed: {e}")
        return []


async def fetch_ad_nms(api_key: str, subject_ids: list[int]) -> list[dict]:
    """POST /adv/v2/supplier/nms — карточки товаров для кампании по предметам.

    Тело — массив ID предметов. Возвращает [{"nm": int, "title": str, "subjectId": int}].
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = "https://advert-api.wildberries.ru/adv/v2/supplier/nms"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=subject_ids)
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                resp = await client.post(url, headers=headers, json=subject_ids)
        if resp.status_code != 200:
            logger.warning(f"WB supplier/nms error {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning(f"WB supplier/nms failed: {e}")
        return []


async def create_campaign(
    api_key: str, name: str, nms: list[int], bid_type: str, payment_type: str,
    placement_types: list[str] | None,
) -> dict:
    """POST /adv/v2/seacat/save-ad — создать кампанию. Возвращает advertId.

    placement_types указывается только для ручной ставки (bid_type=manual).
    Возвращает {"ok": bool, "campaign_id": int | None, "error": str | None}.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = "https://advert-api.wildberries.ru/adv/v2/seacat/save-ad"
    body: dict = {"name": name, "nms": nms, "bid_type": bid_type, "payment_type": payment_type}
    if bid_type == "manual" and placement_types:
        body["placement_types"] = placement_types
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=body)
            if resp.status_code == 429:
                await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                resp = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        logger.warning(f"WB create campaign failed: {e}")
        return {"ok": False, "campaign_id": None, "error": str(e)[:300]}
    if resp.status_code == 200:
        try:
            campaign_id = int(resp.json())
        except (ValueError, TypeError):
            campaign_id = None
        logger.info(f"WB campaign created: {campaign_id} (name={name!r}, {len(nms)} nms)")
        return {"ok": True, "campaign_id": campaign_id, "error": None}
    if resp.status_code in (401, 403):
        return {"ok": False, "campaign_id": None, "error": "Нет доступа: токен «Продвижение» должен быть с правом записи (не read-only)."}
    err = resp.text[:300]
    logger.warning(f"WB create campaign rejected {resp.status_code}: {err}")
    return {"ok": False, "campaign_id": None, "error": f"HTTP {resp.status_code}: {err}"}


# ─── Управление состоянием кампании (пауза / запуск / завершение) ────────────

# action → путь WB. start: 4/11 → 9; pause: 9 → 11; stop: 4/9/11 → 7 (завершена,
# НЕОБРАТИМО). Требует WRITE-доступа скоупа «Продвижение».
_CAMPAIGN_STATE_PATHS = {"start": "start", "pause": "pause", "stop": "stop"}


async def set_campaign_state(api_key: str, campaign_id: int, action: str) -> dict:
    """GET /adv/v0/{start|pause}?id={campaign_id} — запуск или пауза кампании.

    action="start" — запуск/возобновление (статус 4/11 → 9);
    action="pause" — пауза (9 → 11).
    Возвращает {"ok": bool, "error": str | None}. 401/403 — обычно read-only
    токен (нужен write-доступ «Продвижение»); 429 — один повтор.
    """
    if action not in _CAMPAIGN_STATE_PATHS:
        return {"ok": False, "error": f"unknown action: {action}"}
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    # ВАЖНО: управление статусом — на adv/v0 (v1 отдаёт 404 «path not found»,
    # подтверждено на проде 2026-07-09). auth-слой s2s-api-auth-adv токен принимает.
    url = f"https://advert-api.wildberries.ru/adv/v0/{_CAMPAIGN_STATE_PATHS[action]}?id={campaign_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After", "5")
                await asyncio.sleep(int(ra) if ra.isdigit() else 5)  # WB иногда шлёт HTTP-date
                resp = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        logger.warning(f"WB adv {action} failed for {campaign_id}: {e}")
        return {"ok": False, "error": str(e)[:300]}

    if resp.status_code == 200:
        logger.info(f"WB adv {action} ok: campaign {campaign_id}")
        return {"ok": True, "error": None}
    if resp.status_code in (401, 403):
        logger.warning(f"WB adv {action} forbidden {resp.status_code} for {campaign_id} — read-only токен?")
        return {"ok": False, "error": "Нет доступа: токен «Продвижение» должен быть с правом записи (не read-only)."}
    err = resp.text[:300]
    logger.warning(f"WB adv {action} rejected {resp.status_code} for {campaign_id}: {err}")
    return {"ok": False, "error": f"HTTP {resp.status_code}: {err}"}


# ── Search-cluster (normquery) stats & minus-phrases ──────────────────────────
# WB нормализует пользовательские запросы в «кластеры» (norm_query) и копит по ним
# статистику. Доступно только для CPM-кампаний. Эндпоинты:
#   POST /adv/v0/normquery/stats      — статистика по кластерам за период (≤30 дней)
#   POST /adv/v0/normquery/get-minus  — текущие минус-фразы кампаний
#   POST /adv/v0/normquery/set-minus  — установка/удаление минус-фраз (полный список)
_NQ_BASE = "https://advert-api.wildberries.ru/adv/v0/normquery"


def _split_windows(date_from: str, date_to: str, max_days: int = 30) -> list[tuple[str, str]]:
    from datetime import datetime, timedelta

    d0 = datetime.strptime(date_from, "%Y-%m-%d").date()
    d1 = datetime.strptime(date_to, "%Y-%m-%d").date()
    out: list[tuple[str, str]] = []
    ws = d0
    while ws <= d1:
        we = min(ws + timedelta(days=max_days - 1), d1)
        out.append((ws.isoformat(), we.isoformat()))
        ws = we + timedelta(days=1)
    return out


def _merge_clusters(acc: dict[str, dict[str, Any]], clusters: list[dict[str, Any]]) -> None:
    """Суммирует кластеры по norm_query через несколько окон.
    Счётчики складываются, производные (ctr/cpc/cpm/avg_pos) пересчитываются из сумм."""
    for c in clusters or []:
        q = c.get("norm_query")
        if not q:
            continue
        a = acc.get(q)
        if a is None:
            a = acc[q] = {
                "norm_query": q, "views": 0, "clicks": 0, "spend": 0.0,
                "orders": 0, "atbs": 0, "shks": 0, "_pos_w": 0.0,
            }
        a["views"] += c.get("views", 0) or 0
        a["clicks"] += c.get("clicks", 0) or 0
        a["spend"] += c.get("spend", 0.0) or 0.0
        a["orders"] += c.get("orders", 0) or 0
        a["atbs"] += c.get("atbs", 0) or 0
        a["shks"] += c.get("shks", 0) or 0
        a["_pos_w"] += (c.get("avg_pos", 0.0) or 0.0) * (c.get("views", 0) or 0)


def _finalize_clusters(acc: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for a in acc.values():
        v, cl, sp = a["views"], a["clicks"], a["spend"]
        a["ctr"] = round(cl / v * 100, 2) if v else 0.0
        a["cpc"] = round(sp / cl, 2) if cl else 0.0
        a["cpm"] = round(sp / v * 1000, 2) if v else 0.0
        a["avg_pos"] = round(a.pop("_pos_w") / v, 2) if v else 0.0
        a["spend"] = round(sp, 2)
        out.append(a)
    out.sort(key=lambda x: -x["spend"])
    return out


async def fetch_normquery_stats(
    api_key: str,
    pairs: list[tuple[int, int]],
    date_from: str,
    date_to: str,
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """Статистика по поисковым кластерам для пар (advert_id, nm_id).

    Разбивает период на окна ≤30 дней и чанкует пары по 50 в запрос.
    Возвращает {(advert_id, nm_id): [cluster, ...]} с полями
    norm_query/views/clicks/ctr/cpc/cpm/spend/orders/atbs/shks/avg_pos.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    windows = _split_windows(date_from, date_to)
    chunks = [pairs[i : i + 50] for i in range(0, len(pairs), 50)]
    # acc[(advert_id, nm_id)][norm_query] -> merged dict
    acc: dict[tuple[int, int], dict[str, dict[str, Any]]] = {p: {} for p in pairs}

    async with httpx.AsyncClient(timeout=60) as client:
        first = True
        for win_from, win_to in windows:
            for chunk in chunks:
                if not first:
                    await asyncio.sleep(0.4)
                first = False
                body = {
                    "from": win_from,
                    "to": win_to,
                    "items": [{"advert_id": a, "nm_id": n} for a, n in chunk],
                }
                try:
                    resp = await client.post(f"{_NQ_BASE}/stats", json=body, headers=headers)
                except Exception as e:
                    logger.warning(f"WB normquery/stats request failed: {e}")
                    continue
                if resp.status_code == 429:
                    await asyncio.sleep(15)
                    resp = await client.post(f"{_NQ_BASE}/stats", json=body, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"WB normquery/stats {resp.status_code}: {resp.text[:200]}")
                    continue
                data = resp.json() or {}
                for item in data.get("stats") or []:
                    key = (item.get("advert_id"), item.get("nm_id"))
                    if key in acc:
                        _merge_clusters(acc[key], item.get("stats") or [])

    return {p: _finalize_clusters(acc[p]) for p in pairs}


async def get_normquery_minus(
    api_key: str,
    pairs: list[tuple[int, int]],
) -> dict[tuple[int, int], list[str]]:
    """Текущие минус-фразы для пар (advert_id, nm_id).

    ВАЖНО: пара присутствует в результате ТОЛЬКО если её чанк реально прочитан (HTTP 200).
    При сетевом сбое/не-200 пары чанка ОТСУТСТВУЮТ в ответе — вызывающий обязан отличать
    «нет минус-фраз» (ключ есть, список пуст) от «не прочитано» (ключа нет). Без этого
    replace-семантика set-minus затёрла бы живой список при транзиентном сбое чтения.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    result: dict[tuple[int, int], list[str]] = {}
    chunks = [pairs[i : i + 50] for i in range(0, len(pairs), 50)]
    async with httpx.AsyncClient(timeout=30) as client:
        for idx, chunk in enumerate(chunks):
            if idx > 0:
                await asyncio.sleep(0.4)
            body = {"items": [{"advert_id": a, "nm_id": n} for a, n in chunk]}
            try:
                resp = await client.post(f"{_NQ_BASE}/get-minus", json=body, headers=headers)
            except Exception as e:
                logger.warning(f"WB normquery/get-minus failed: {e}")
                continue  # чанк не прочитан → его пары отсутствуют в result
            if resp.status_code != 200:
                logger.warning(f"WB normquery/get-minus {resp.status_code}: {resp.text[:200]}")
                continue
            # чанк прочитан успешно: сеем пустой список для всех его пар, затем заполняем
            for p in chunk:
                result[p] = []
            for item in (resp.json() or {}).get("items") or []:
                key = (item.get("advert_id"), item.get("nm_id"))
                if key in result:
                    result[key] = list(item.get("norm_queries") or [])
    return result


async def set_normquery_minus(
    api_key: str,
    advert_id: int,
    nm_id: int,
    norm_queries: list[str],
) -> tuple[bool, str | None]:
    """Устанавливает ПОЛНЫЙ список минус-фраз для (advert_id, nm_id) — replace-семантика.

    Всегда вызывать после get_normquery_minus + слияния: WB заменяет весь список,
    отсутствующие фразы удаляются. Возвращает (ok, error) — error читаемый для UI.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    body = {"items": [{"advert_id": advert_id, "nm_id": nm_id, "norm_queries": norm_queries}]}
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(f"{_NQ_BASE}/set-minus", json=body, headers=headers)
        except Exception as e:
            logger.error(f"WB normquery/set-minus failed for {advert_id}/{nm_id}: {e}")
            return False, "Сеть недоступна, повторите позже"
        if resp.status_code in (200, 204):
            logger.info(f"WB normquery/set-minus ok: advert={advert_id} nm={nm_id} n={len(norm_queries)}")
            return True, None
        text = resp.text[:200]
        logger.error(f"WB normquery/set-minus {resp.status_code}: {text}")
        if resp.status_code in (401, 403) and "read-only" in text:
            return False, (
                "WB-токен только для чтения. Заведите в кабинете WB токен с категорией "
                "«Продвижение» и правом записи (без «Только на чтение») — тогда минус-фразы "
                "будут применяться в кабинете."
            )
        return False, f"WB вернул {resp.status_code}"


async def get_normquery_bids(
    api_key: str,
    pairs: list[tuple[int, int]],
) -> dict[tuple[int, int], dict[str, float]]:
    """Текущие ставки по кластерам: {(advert_id, nm_id): {norm_query: bid_rub}}.

    Ставки задаются индивидуально по кластеру; заданы не у всех (у остальных — дефолт кампании).
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    result: dict[tuple[int, int], dict[str, float]] = {}
    chunks = [pairs[i : i + 50] for i in range(0, len(pairs), 50)]
    async with httpx.AsyncClient(timeout=30) as client:
        for idx, chunk in enumerate(chunks):
            if idx > 0:
                await asyncio.sleep(0.4)
            body = {"items": [{"advert_id": a, "nm_id": n} for a, n in chunk]}
            try:
                resp = await client.post(f"{_NQ_BASE}/get-bids", json=body, headers=headers)
            except Exception as e:
                logger.warning(f"WB normquery/get-bids failed: {e}")
                continue
            if resp.status_code != 200:
                logger.warning(f"WB normquery/get-bids {resp.status_code}: {resp.text[:200]}")
                continue
            for b in (resp.json() or {}).get("bids") or []:
                key = (b.get("advert_id"), b.get("nm_id"))
                q = b.get("norm_query")
                if key[0] is None or not q:
                    continue
                result.setdefault(key, {})[q] = float(b.get("bid", 0) or 0)
    return result


async def set_normquery_bid(
    api_key: str,
    advert_id: int,
    nm_id: int,
    norm_query: str,
    bid_rub: float,
) -> tuple[bool, str | None]:
    """Ставит ставку одному кластеру (₽ → копейки). WB не даёт ставку кластерам <100 показов.

    Схема set-bids подтверждается вживую при write-scoped токене (сейчас токен read-only → 401).
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    body = {
        "bids": [
            {
                "advert_id": advert_id,
                "nm_id": nm_id,
                "norm_query": norm_query,
                "bid_kopecks": int(round(bid_rub * 100)),
            }
        ]
    }
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(f"{_NQ_BASE}/bids", json=body, headers=headers)
        except Exception as e:
            logger.error(f"WB normquery/bids failed for {advert_id}/{nm_id}: {e}")
            return False, "Сеть недоступна, повторите позже"
        if resp.status_code in (200, 204):
            logger.info(f"WB normquery/bids ok: advert={advert_id} nm={nm_id} q={norm_query!r} bid={bid_rub}")
            return True, None
        text = resp.text[:200]
        logger.error(f"WB normquery/bids {resp.status_code}: {text}")
        if resp.status_code in (401, 403) and "read-only" in text:
            return False, (
                "WB-токен только для чтения. Нужен токен «Продвижение» с правом записи, "
                "чтобы менять ставки по кластерам."
            )
        if resp.status_code == 400 and ("100" in text or "views" in text.lower()):
            return False, "Кластер в стадии сбора данных (<100 показов) — WB не принимает ставку."
        return False, f"WB вернул {resp.status_code}"
