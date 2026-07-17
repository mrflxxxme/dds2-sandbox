"""WB Advertising API — campaign management and stats.

Handles:
- fetch_ad_campaigns: list campaign IDs by status
- fetch_ad_stats: detailed per-nmId per-date ad stats with time budget
"""

import asyncio
import logging
import re
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
    # Ставка кампании по активной зоне (поиск приоритетнее): макс. bids_kopecks по nm → ₽.
    # Нужна для инлайн-правки ставки в списке (рендер из зеркала, без лишнего вызова WB).
    search_kop: list[int] = []
    reco_kop: list[int] = []
    for ns in c.get("nm_settings") or []:
        if not isinstance(ns, dict):
            continue
        bk = ns.get("bids_kopecks") or {}
        if isinstance(bk.get("search"), (int, float)):
            search_kop.append(int(bk["search"]))
        if isinstance(bk.get("recommendations"), (int, float)):
            reco_kop.append(int(bk["recommendations"]))
    if search_kop:
        default_bid = round(max(search_kop) / 100, 2)
    elif reco_kop:
        default_bid = round(max(reco_kop) / 100, 2)
    else:
        default_bid = None
    return {
        "advertId": advert_id,
        "name": name or str(advert_id or ""),
        # campaign_type: payment_type ('cpm'/'cpc'); фолбэк на bid_type только если payment_type пуст
        "type": payment_type or c.get("bid_type"),
        "advert_type": advert_type,
        "create_time": c.get("createTime"),  # ISO-строка создания кампании в WB (дата добавления)
        # bid_mode: режим ставки WB — 'unified'/'manual' как есть (совпадает с контрактом фронта)
        "bid_mode": c.get("bid_type"),
        "default_bid": default_bid,  # ставка ₽ по активной зоне (None если WB не прислал ставок)
        "status": c.get("status"),
        "nm_ids": nm_ids,
    }


async def fetch_campaign_detail(api_key: str, campaign_id: int) -> dict | None:
    """Деталь ОДНОЙ кампании из WB — для точечного догруза (имя/тип/статус/nm_ids/bid_mode).

    GET /api/advert/v2/adverts?ids={id} → adverts[0] → _parse_advert_item. Возвращает
    нормализованный dict той же формы, что элемент fetch_ad_campaigns_detailed, или None,
    если WB не отдал кампанию. Один запрос — без обхода всего кабинета.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"https://advert-api.wildberries.ru/api/advert/v2/adverts?ids={campaign_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            logger.warning(f"WB advert detail {resp.status_code} for {campaign_id}: {resp.text[:200]}")
            return None
        adverts = (resp.json() or {}).get("adverts") or []
    except Exception as e:
        logger.warning(f"WB advert detail failed for {campaign_id}: {e}")
        return None
    return _parse_advert_item(adverts[0]) if adverts else None


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


def _extract_min_bid(err_text: str) -> float | None:
    """Из ошибки WB «bid value must be no less than 555.00» вытащить минимум (₽).

    WB держит динамический аукционный «пол» ставки и отбивает запись ниже него
    сообщением с числом. Возвращает минимум ₽ или None, если это не тот случай.
    """
    m = re.search(r"no less than\s*([\d]+(?:\.[\d]+)?)", err_text or "")
    if not m:
        return None
    try:
        return round(float(m.group(1)), 2)
    except ValueError:
        return None


def _extract_bad_phrase(err_text: str) -> str | None:
    """Из глобальной ошибки батча вытащить «отравившую» фразу: `'фраза' norm_query disabled for nm`.

    WB-батч normquery/bids — ВСЁ-ИЛИ-НИЧЕГО: одна невалидная фраза (broad-match, «disabled»)
    отбивает весь запрос своей ошибкой. Имя фразы — в одинарных кавычках в начале сообщения.
    """
    m = re.match(r"^'([^']+)'", err_text or "")
    return m.group(1) if m else None


async def set_campaign_bid(
    api_key: str, campaign_id: int, nm_ids: list[int], bid_rub: float, placement: str
) -> dict:
    """PATCH /api/advert/v1/bids — сменить ставку кампании по её товарам.

    placement: "combined" (единая ставка), "search"/"recommendations" (ручная), для CPC — за клик.
    Тело: {"bids":[{"advert_id":id,"nm_bids":[{"nm_id","bid_kopecks","placement"} ...]}]}. Ставка —
    в копейках (₽×100). WB: только статусы 4/9/11; проверку типа/статуса делает вызывающий.

    Авто-минимум: WB держит динамический аукционный «пол» ставки и отбивает запись ниже
    него ошибкой «bid value must be no less than X». Тогда СРАЗУ повторяем запись с этим X
    (до 3 попыток — пол мог подняться ещё). Так пользователь может ввести любую ставку (хоть
    1 ₽) и получить минимально доступную. Возвращает {"ok","error","bid","adjusted","min_bid"}
    (bid — фактически применённая ставка ₽; adjusted=True если WB поднял до минимума).
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = "https://advert-api.wildberries.ru/api/advert/v1/bids"

    async def _send(kopecks: int) -> httpx.Response:
        nm_bids = [{"nm_id": nm, "bid_kopecks": kopecks, "placement": placement} for nm in nm_ids]
        body = {"bids": [{"advert_id": campaign_id, "nm_bids": nm_bids}]}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.patch(url, headers=headers, json=body)
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After", "1")
                await asyncio.sleep(int(ra) if ra.isdigit() else 1)
                resp = await client.patch(url, headers=headers, json=body)
        return resp

    kop = int(round(bid_rub * 100))
    adjusted = False
    last_min: float | None = None
    for _ in range(3):
        try:
            resp = await _send(kop)
        except httpx.HTTPError as e:
            logger.warning(f"WB set bid failed for {campaign_id}: {e}")
            return {"ok": False, "error": str(e)[:300], "bid": None}
        if resp.status_code == 200:
            applied = round(kop / 100, 2)
            logger.info(f"WB bid updated: campaign {campaign_id} → {applied} ₽ ({placement}, adjusted={adjusted})")
            return {"ok": True, "error": None, "bid": applied, "adjusted": adjusted, "min_bid": last_min}
        if resp.status_code in (401, 403):
            logger.warning(f"WB set bid forbidden {resp.status_code} for {campaign_id} — read-only токен?")
            return {"ok": False, "error": "Нет доступа: токен «Продвижение» должен быть с правом записи (не read-only).", "bid": None}
        err = resp.text[:300]
        min_bid = _extract_min_bid(err)
        if min_bid is None:
            logger.warning(f"WB set bid rejected {resp.status_code} for {campaign_id}: {err}")
            return {"ok": False, "error": f"HTTP {resp.status_code}: {err}", "bid": None}
        # Ставка ниже аукционного «пола» WB — повторяем с названным минимумом
        logger.info(f"WB bid below floor for {campaign_id}: auto-bumping to minimum {min_bid} ₽")
        kop = int(round(min_bid * 100))
        last_min = min_bid
        adjusted = True
    # Три раза подряд WB поднимал минимум — не сходится
    return {"ok": False, "error": f"WB минимум ставки {last_min:g} ₽ не удалось применить, повторите", "bid": None, "min_bid": last_min}


async def fetch_campaign_min_bid(
    api_key: str, campaign_id: int, nm_ids: list[int], placement: str = "search"
) -> float | None:
    """Минимальная (аукционный «пол») ставка кампании по зоне — READ-ONLY пробник.

    У WB нет API отдать пол (`/adv/v1/cpm` → 404). Трюк: шлём заведомо мизерную ставку 1 ₽
    (CPM требует ЦЕЛОЕ число рублей → bid_kopecks=100, не 1 — иначе «bid must be a whole number»)
    — WB отбивает валидацией «bid value must be no less than X» ДО применения, ставку НЕ меняем.
    X = текущий пол. None — узнать не удалось (нет прав/сеть/иной ответ).
    """
    if not nm_ids:
        return None
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = "https://advert-api.wildberries.ru/api/advert/v1/bids"
    nm_bids = [{"nm_id": nm, "bid_kopecks": 100, "placement": placement} for nm in nm_ids]
    body = {"bids": [{"advert_id": campaign_id, "nm_bids": nm_bids}]}
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.patch(url, headers=headers, json=body)
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After", "1")
                await asyncio.sleep(int(ra) if ra.isdigit() else 1)
                resp = await client.patch(url, headers=headers, json=body)
    except httpx.HTTPError as e:
        logger.warning(f"WB min-bid probe failed for {campaign_id}: {e}")
        return None
    if resp.status_code == 200:
        # 0.01 ₽ приняли — для CPM невозможно (пол десятки ₽); не трактуем как пол, чтобы не соврать
        logger.warning(f"WB min-bid probe unexpectedly accepted 0.01₽ for {campaign_id}")
        return None
    return _extract_min_bid(resp.text[:300])


async def fetch_campaign_default_bid(api_key: str, campaign_id: int) -> float | None:
    """Ставка кампании по умолчанию (₽) — по активной зоне (поиск приоритетнее)."""
    info = await fetch_campaign_placements(api_key, campaign_id)
    pl, bids = info["placements"], info["bids"]
    # Если поиск выключен, действует ставка рекомендаций
    if pl.get("search", True) and bids.get("search") is not None:
        return float(bids["search"])
    bid = bids.get("recommendations") or bids.get("search")
    return float(bid) if bid is not None else None


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
    time_budget: float = 600,
) -> dict:
    """Fetch remaining budget for each campaign.

    GET /adv/v1/budget?id={campaign_id}
    Returns {campaign_id: budget_value} — при исчерпании лимита времени ЧАСТИЧНЫЙ
    словарь (кампании отсортированы по расходу, так что успевают важные).

    ``time_budget`` — сколько секунд отведено на цикл. Вызывающий ОБЯЗАН задать его
    строго меньше своего внешнего таймаута: WB отвечает ~0.7с на кампанию (0.5с пауза
    против rate-limit + сам запрос), и при равных лимитах внешний wait_for убивает
    корутину раньше мягкой остановки → частичный результат теряется целиком.
    progress_cb: optional callback(done, total) for progress tracking.
    """
    from decimal import Decimal

    result: dict[int, Decimal] = {}
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    TIME_BUDGET = time_budget
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


# WB: normquery/stats.Items ограничен `lte 100` (проверено — 300 → 400). Батчим
# многие (advertId,nmId) пары в один запрос: запросов на порядок меньше, чем
# «кампания-на-запрос» → обходим жёсткий per-seller rate-limit (даже
# конкурентность 3 по кампаниям упирается в 429/461).
_NORMQUERY_ITEM_CAP = 100


async def fetch_search_daily_batch(
    api_key: str, pairs: list[tuple[int, int]], date_from: str, date_to: str,
) -> dict[tuple[int, int, str], dict]:
    """POST /adv/v1/normquery/stats батчами по многим кампаниям сразу.

    pairs — [(advert_id, nm_id), …]. Возвращает
    {(advert_id, nm_id, "YYYY-MM-DD"): {views, clicks, spend, atbs, orders, shks}}.
    Ответ echo'ит advertId+nmId на уровне item → демукс по кампаниям. Чанк, чей
    запрос упал/рейт-лимитнут (после одного бэкоффа) — пропускаем, остальные идут.
    """
    agg: dict[tuple[int, int, str], dict] = {}
    if not pairs:
        return agg
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = "https://advert-api.wildberries.ru/adv/v1/normquery/stats"

    async with httpx.AsyncClient(timeout=120) as client:
        for i in range(0, len(pairs), _NORMQUERY_ITEM_CAP):
            chunk = pairs[i : i + _NORMQUERY_ITEM_CAP]
            body = {
                "from": date_from, "to": date_to,
                "items": [{"advertId": a, "nmId": n} for a, n in chunk],
            }
            try:
                resp = await client.post(url, headers=headers, json=body)
                if resp.status_code in (429, 461):  # 461 = «too many requests» (per-seller лимитер)
                    await asyncio.sleep(int(resp.headers.get("Retry-After", "5")))
                    resp = await client.post(url, headers=headers, json=body)
                if resp.status_code != 200:
                    logger.warning(f"WB normquery/stats batch {resp.status_code}: {resp.text[:200]}")
                    continue
                items = (resp.json() or {}).get("items") or []
            except Exception as e:
                logger.warning(f"WB normquery/stats batch failed: {e!r}")
                continue

            for it in items:
                advert_id = it.get("advertId")  # echo'ится WB → демукс по кампании
                nm_id = it.get("nmId")
                if advert_id is None or nm_id is None:
                    continue
                for day in it.get("dailyStats") or []:
                    date_key = day.get("date")
                    if not date_key:
                        continue
                    st = day.get("stat") or {}
                    key = (int(advert_id), int(nm_id), date_key)
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
    # ВАЖНО: в отличие от get-minus (батч items[]), set-minus принимает поля на
    # ВЕРХНЕМ уровне. Обёртка items[] давала 400 «invalid advert id» — WB не
    # находил advert_id в корне тела (проверено на живом токене 2026-07-14).
    body = {"advert_id": advert_id, "nm_id": nm_id, "norm_queries": norm_queries}
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
        if resp.status_code == 400 and "is not valid for nm" in text:
            # WB принимает в минус только фразы из известной ему истории кластеров товара
            return False, "WB отклонил фразу: она не входит в известные кластеры этого товара"
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


def _parse_bids_result(resp: httpx.Response) -> tuple[bool, str]:
    """Разбор ответа normquery/bids → (ok, reason). WB отдаёт 200 И НА ОТКАЗ — истина в теле
    {"success":[...], "failed":[{...,"reason":...}]}; статус 200 сам по себе успеха не значит."""
    if resp.status_code in (500, 502, 503, 504):
        return False, "WB временно недоступен, попробуйте ещё раз"
    if resp.status_code not in (200, 204):
        # тело может быть HTML-страницей ошибки — сырьё пользователю не показываем
        return False, f"WB вернул {resp.status_code}"
    try:
        data = resp.json() or {}
    except Exception:
        return False, "WB вернул нечитаемый ответ"
    success = data.get("success") or []
    failed = data.get("failed") or []
    if success and not failed:
        return True, ""
    if failed and isinstance(failed[0], dict):
        return False, str(failed[0].get("reason") or "")
    return False, "WB отклонил ставку по фразе"


def _parse_bids_batch(resp: "httpx.Response | None") -> tuple[set[str], dict[str, str], str | None]:
    """Разбор ПАЧЕЧНОГО ответа normquery/bids → (успешные norm_query, {norm_query: reason} отбитых,
    global_error). global_error≠None — весь батч не разобрать (сеть/не-200): все items считать отбитыми.
    Фразы, которых нет ни в success, ни в failed (WB молча уронил), вызывающий помечает отказом."""
    if resp is None:
        return set(), {}, "Сеть недоступна, повторите позже"
    if resp.status_code == 429:
        return set(), {}, "WB ограничивает частоту запросов (429), повторите позже"
    if resp.status_code in (500, 502, 503, 504):
        return set(), {}, "WB временно недоступен, попробуйте ещё раз"
    if resp.status_code in (401, 403) and "read-only" in resp.text[:200]:
        return set(), {}, ("WB-токен только для чтения. Нужен токен «Продвижение» с правом записи.")
    if resp.status_code not in (200, 204):
        return set(), {}, f"WB вернул {resp.status_code}"
    try:
        data = resp.json() or {}
    except Exception:
        return set(), {}, "WB вернул нечитаемый ответ"
    succ = {str(x.get("norm_query")) for x in (data.get("success") or []) if isinstance(x, dict)}
    fail = {str(x.get("norm_query")): str(x.get("reason") or "") for x in (data.get("failed") or []) if isinstance(x, dict)}
    return succ, fail, None


async def _bids_request_with_retry(method: str, body: dict, headers: dict, tag: str) -> httpx.Response | None:
    """Запрос к normquery/bids (POST/DELETE) с ретраем rate-limit и транзиентных сбоев.

    WB-лимит на bid-эндпоинты ~1 запрос/сек — при массовой правке ставок пачка запросов
    ловит 429 «слишком много запросов». Ждём `Retry-After` (или 2с, кап 10с) и повторяем;
    5xx — короткий бэкофф. Возвращает последний ответ (в т.ч. 429 после исчерпания ретраев,
    его разберёт вызывающий), либо None при сетевой ошибке.
    """
    resp: httpx.Response | None = None
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                if method == "POST":
                    resp = await client.post(f"{_NQ_BASE}/bids", json=body, headers=headers)
                else:
                    resp = await client.request(method, f"{_NQ_BASE}/bids", json=body, headers=headers)
        except Exception as e:
            logger.error(f"WB normquery/bids {method} failed for {tag}: {e}")
            return None
        if resp.status_code == 429:
            ra = resp.headers.get("Retry-After", "")
            wait = float(ra) if ra.replace(".", "", 1).isdigit() else 2.0
            wait = min(max(wait, 1.0), 10.0)
            logger.warning(f"WB normquery/bids 429 rate limit, waiting {wait:.0f}s (attempt {attempt + 1}/5) for {tag}")
            await asyncio.sleep(wait)
            continue
        if resp.status_code in (500, 502, 503, 504) and attempt < 4:
            logger.warning(f"WB normquery/bids {resp.status_code} (transient), attempt {attempt + 1}/5 for {tag}")
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        return resp
    return resp  # исчерпали ретраи — вернём последний ответ (429/5xx) на разбор выше


async def set_normquery_bid(
    api_key: str,
    advert_id: int,
    nm_id: int,
    norm_query: str,
    bid_rub: float,
) -> tuple[bool, str | None, float | None]:
    """Ставит/снимает ставку одному кластеру (₽). Возвращает (ok, error, applied_₽).

    Контракт WB (adv/v0/normquery/bids), выверен эмпирически 2026-07-15:
    - установка: POST, тело {"bids":[{advert_id,nm_id,norm_query,"bid": <ЦЕЛЫЕ ₽>}]}. Поле именно
      `bid` в РУБЛЯХ — прежний `bid_kopecks`/копейки WB молча складывал в failed (ставка не менялась).
    - сброс к ставке кампании (bid_rub<=0): HTTP DELETE того же /bids с телом
      {"bids":[{advert_id,nm_id,norm_query}]} (bid:0 WB отвергает «invalid bid cost 0»).
    Успех — по массивам success/failed в теле, а НЕ по статусу 200. Авто-минимум: reason
    «no less than X» → повтор с X (до 3). applied_₽ = фактически применённая ставка (или минимум WB).
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    item = {"advert_id": advert_id, "nm_id": nm_id, "norm_query": norm_query}
    tag = f"{advert_id}/{nm_id} q={norm_query!r}"

    # Сброс к ставке кампании — снятие пофразовой ставки отдельным DELETE (bid=0 WB не принимает)
    if bid_rub is None or bid_rub <= 0:
        resp = await _bids_request_with_retry("DELETE", {"bids": [item]}, headers, tag)
        if resp is None:
            return False, "Сеть недоступна, повторите позже", None
        ok, reason = _parse_bids_result(resp)
        if ok:
            logger.info(f"WB normquery/bids reset ok: {tag}")
            return True, None, None
        logger.error(f"WB normquery/bids reset failed: {tag} reason={reason!r}")
        return False, reason or "WB не смог сбросить ставку по фразе", None

    bid = int(round(bid_rub))
    for _ in range(3):  # авто-минимум: reason «no less than X» → повтор с X
        resp = await _bids_request_with_retry("POST", {"bids": [{**item, "bid": bid}]}, headers, tag)
        if resp is None:
            return False, "Сеть недоступна, повторите позже", None
        if resp.status_code in (401, 403) and "read-only" in resp.text[:200]:
            return False, (
                "WB-токен только для чтения. Нужен токен «Продвижение» с правом записи, "
                "чтобы менять ставки по кластерам."
            ), None
        # 429/5xx уже переждал хелпер; если дошло досюда — это уже не транзиент
        if resp.status_code == 429:
            return False, "WB ограничивает частоту запросов (429), часть ставок не применилась. Повторите позже.", None
        if resp.status_code in (500, 502, 503, 504):
            return False, "WB временно недоступен (сервис ставок отвечает 503). Попробуйте ещё раз через минуту.", None
        ok, reason = _parse_bids_result(resp)
        if ok:
            applied = float(bid)
            logger.info(f"WB normquery/bids ok: {tag} bid={applied}")
            return True, None, applied
        logger.error(f"WB normquery/bids failed: {tag} reason={reason!r}")
        min_bid = _extract_min_bid(reason)
        if min_bid is not None:
            bid = int(round(min_bid))  # ниже аукционного пола — повторяем с минимумом WB
            continue
        low = reason.lower()
        if "views" in low or "100" in low:
            return False, "Кластер в стадии сбора данных — WB не принимает ставку.", None
        return False, reason or "WB отклонил ставку по фразе", None
    return False, "WB не принял даже минимальную ставку, повторите", None


_BID_BATCH_MAX = 100  # предохранитель на случай лимита размера тела WB; батч режем на чанки этого размера


def _bid_reason_to_error(reason: str) -> str:
    """Причина отказа WB по фразе → человекочитаемая ошибка (как в поштучном set_normquery_bid)."""
    low = reason.lower()
    if "views" in low or "100" in low:
        return "Кластер в стадии сбора данных — WB не принимает ставку."
    return reason or "WB отклонил ставку по фразе"


async def set_normquery_bids_batch(
    api_key: str,
    advert_id: int,
    items: list[tuple[int, str, float]],
) -> dict[str, dict]:
    """Ставит/снимает ставки ПАЧКОЙ — одним запросом на чанк (как это делает Mkeeper).

    WB `adv/v0/normquery/bids` принимает МАССИВ `bids[]` и отдаёт `success[]`/`failed[]` пофразно —
    т.е. эндпоинт батчевый по замыслу. Поштучный цикл упирался в лимит ~1 запрос/сек (429); пачка
    из 91 фразы = 1–2 запроса вместо 91 → мгновенно и без rate-limit.

    items: (nm_id, norm_query, bid_rub). bid>0 — установка (POST), bid<=0 — сброс к ставке кампании
    (DELETE, bid:0 WB не принимает). Авто-минимум: фразы, отбитые «no less than X», повторяются
    добатчем с X. Возврат: {norm_query: {"ok":bool, "bid":float|None, "error":str|None}}.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    result: dict[str, dict] = {}
    sets = [(nm, q, int(round(b))) for (nm, q, b) in items if b is not None and b > 0]
    resets = [(nm, q) for (nm, q, b) in items if b is None or b <= 0]

    def _mark_failed(qs: list[str], err: str) -> None:
        for q in qs:
            result[q] = {"ok": False, "bid": None, "error": err}

    # ── Сбросы (DELETE-батч) ──
    for i in range(0, len(resets), _BID_BATCH_MAX):
        chunk = resets[i:i + _BID_BATCH_MAX]
        body = {"bids": [{"advert_id": advert_id, "nm_id": nm, "norm_query": q} for (nm, q) in chunk]}
        resp = await _bids_request_with_retry("DELETE", body, headers, f"{advert_id} reset×{len(chunk)}")
        succ, fail, gerr = _parse_bids_batch(resp)
        if gerr is not None:
            _mark_failed([q for (_nm, q) in chunk], gerr)
            continue
        for (_nm, q) in chunk:
            if q in succ:
                result[q] = {"ok": True, "bid": None, "error": None}
            else:
                result[q] = {"ok": False, "bid": None, "error": fail.get(q) or "WB не сбросил ставку по фразе"}

    # ── Установки (POST-батч) + один добатч по авто-минимуму ──
    retry: list[tuple[int, str, int]] = []  # (nm, norm_query, min_bid) — отбитые «no less than X»
    for i in range(0, len(sets), _BID_BATCH_MAX):
        remaining = sets[i:i + _BID_BATCH_MAX]
        guard = len(remaining) + 1  # не больше 1 итерации на каждую отравившую фразу
        while remaining and guard > 0:
            guard -= 1
            body = {"bids": [{"advert_id": advert_id, "nm_id": nm, "norm_query": q, "bid": bid} for (nm, q, bid) in remaining]}
            resp = await _bids_request_with_retry("POST", body, headers, f"{advert_id} set×{len(remaining)}")
            succ, fail, gerr = _parse_bids_batch(resp)
            if gerr is not None:  # сеть/429/не-200 — весь остаток не разобрать
                _mark_failed([q for (_n, q, _b) in remaining], gerr)
                break
            # WB-батч ВСЁ-ИЛИ-НИЧЕГО: при одной невалидной «disabled» фразе WB кладёт ВЕСЬ батч
            # в failed, и reason каждой строки называет ту одну отравившую фразу. Вычленяем такие
            # (reason начинается с 'фраза', которая есть в батче), выкидываем и повторяем ОСТАТОК.
            poison = {named for (_n, q, _b) in remaining if q not in succ
                      for named in [_extract_bad_phrase(fail.get(q, ""))]
                      if named and any(t[1] == named for t in remaining)}
            if poison and len(remaining) > len(poison):
                for p in poison:
                    result[p] = {"ok": False, "bid": None, "error": _bid_reason_to_error(fail.get(p) or f"'{p}' norm_query disabled")}
                remaining = [t for t in remaining if t[1] not in poison]
                continue
            for (nm, q, bid) in remaining:  # нормальный пофразный разбор
                if q in succ:
                    result[q] = {"ok": True, "bid": float(bid), "error": None}
                    continue
                reason = fail.get(q, "")
                mn = _extract_min_bid(reason)
                if mn is not None:
                    retry.append((nm, q, int(round(mn))))  # ниже пола — добьём минимумом WB
                else:
                    result[q] = {"ok": False, "bid": None, "error": _bid_reason_to_error(reason)}
            break

    for i in range(0, len(retry), _BID_BATCH_MAX):
        retry_chunk = retry[i:i + _BID_BATCH_MAX]
        body = {"bids": [{"advert_id": advert_id, "nm_id": nm, "norm_query": q, "bid": bid} for (nm, q, bid) in retry_chunk]}
        resp = await _bids_request_with_retry("POST", body, headers, f"{advert_id} set-min×{len(retry_chunk)}")
        succ, fail, gerr = _parse_bids_batch(resp)
        for (nm, q, bid) in retry_chunk:
            if gerr is None and q in succ:
                result[q] = {"ok": True, "bid": float(bid), "error": None}
            else:
                result[q] = {"ok": False, "bid": None, "error": gerr or _bid_reason_to_error(fail.get(q, ""))}

    return result


async def fetch_ad_stats_today_nm(api_key: str, campaign_id: int, nm_id: int, day: str) -> dict | None:
    """Накопительная статистика одного товара в одной кампании за один день (обычно «сегодня» МСК).

    Лёгкий точечный запрос для АБ-тестов фото: дельты между такими снимками = метрики
    круга ротации (стиль get_intraday_metrics). None — не удалось получить (429/5xx/сеть):
    вызывающий пропускает тик, НЕ считая это нулевой статистикой.
    """
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"https://advert-api.wildberries.ru/adv/v3/fullstats?ids={campaign_id}&beginDate={day}&endDate={day}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as e:
        logger.warning(f"AB fullstats: network error campaign={campaign_id}: {e}")
        return None
    if resp.status_code != 200:
        logger.warning(f"AB fullstats: HTTP {resp.status_code} campaign={campaign_id}: {resp.text[:150]}")
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    out = {"views": 0, "clicks": 0, "atbs": 0, "orders": 0, "spend": 0.0, "orders_sum": 0.0}
    for campaign in data if isinstance(data, list) else []:
        for d in (campaign or {}).get("days") or []:
            for app in d.get("apps") or []:
                for nm in app.get("nm") or []:
                    if nm.get("nmId") != nm_id:
                        continue
                    out["views"] += int(nm.get("views") or 0)
                    out["clicks"] += int(nm.get("clicks") or 0)
                    out["atbs"] += int(nm.get("atbs") or 0)
                    out["orders"] += int(nm.get("orders") or 0)
                    out["spend"] += float(nm.get("sum") or 0)
                    out["orders_sum"] += float(nm.get("sum_price") or 0)
    return out
