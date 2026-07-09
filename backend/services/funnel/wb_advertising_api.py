"""WB Advertising API — campaign management and stats.

Handles:
- fetch_ad_campaigns: list campaign IDs by status
- fetch_ad_stats: detailed per-nmId per-date ad stats with time budget
"""

import asyncio
import logging
import time

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


async def fetch_ad_stats(api_key: str, campaign_ids: list[int], begin_date: str, end_date: str) -> dict:
    """Fetch detailed ad stats per nmId per date.
    Returns {date: {nm_id: {sum, clicks, views}}}.

    Also populates result["_by_campaign"] = {date: {campaign_id: {sum, clicks, views}}}
    for per-campaign daily stats.

    WB API limit: max 31 days per request. Automatically splits into windows.
    Uses a 300-second time budget: returns partial data if exceeded.
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
    chunks = [campaign_ids[i : i + 50] for i in range(0, len(campaign_ids), 50)]
    skipped_chunks = 0
    budget_exceeded = False
    TIME_BUDGET = 300
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
                    await asyncio.sleep(5)

                ids_param = ",".join(str(c) for c in chunk)
                url = (
                    f"https://advert-api.wildberries.ru/adv/v3/fullstats"
                    f"?ids={ids_param}&beginDate={win_begin}&endDate={win_end}"
                )

                chunk_ok = False
                for attempt in range(2):
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
                        wait = [20, 40][attempt]
                        logger.warning(
                            f"WB adv 429 rate limit, waiting {wait}s "
                            f"(attempt {attempt+1}/2, elapsed {time.monotonic()-t_start:.0f}s)"
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
    return {
        "advertId": advert_id,
        "name": name or str(advert_id or ""),
        # campaign_type: payment_type ('cpm'/'cpc'); фолбэк на bid_type только если payment_type пуст
        "type": payment_type or c.get("bid_type"),
        # bid_mode: режим ставки WB — 'unified'/'manual' как есть (совпадает с контрактом фронта)
        "bid_mode": c.get("bid_type"),
        "status": c.get("status"),
        "nm_ids": nm_ids,
    }


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


# ─── Управление состоянием кампании (пауза / запуск) ─────────────────────────

# action → путь WB. start: запускает кампанию в статусе 4 (готова) или 11 (пауза)
# → 9 (активна); pause: 9 → 11. Требует WRITE-доступа скоупа «Продвижение».
_CAMPAIGN_STATE_PATHS = {"start": "start", "pause": "pause"}


async def set_campaign_state(api_key: str, campaign_id: int, action: str) -> dict:
    """GET /adv/v1/{start|pause}?id={campaign_id} — запуск или пауза кампании.

    action="start" — запуск/возобновление (статус 4/11 → 9);
    action="pause" — пауза (9 → 11).
    Возвращает {"ok": bool, "error": str | None}. 401/403 — обычно read-only
    токен (нужен write-доступ «Продвижение»); 429 — один повтор.
    """
    if action not in _CAMPAIGN_STATE_PATHS:
        return {"ok": False, "error": f"unknown action: {action}"}
    headers = {"Accept": "application/json", "Authorization": f"Bearer {api_key}"}
    url = f"https://advert-api.wildberries.ru/adv/v1/{_CAMPAIGN_STATE_PATHS[action]}?id={campaign_id}"
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
