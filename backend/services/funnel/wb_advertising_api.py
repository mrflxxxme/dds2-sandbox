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
    windows = []
    w_start = d_start
    while w_start <= d_end:
        w_end = min(w_start + timedelta(days=30), d_end)  # 31 days = 0..30
        windows.append((w_start.isoformat(), w_end.isoformat()))
        w_start = w_end + timedelta(days=1)

    logger.info(f"WB adv stats: {begin_date}→{end_date} split into {len(windows)} window(s)")

    result = {}
    by_campaign: dict[str, dict[int, dict]] = {}  # date -> campaign_id -> stats
    chunks = [campaign_ids[i : i + 50] for i in range(0, len(campaign_ids), 50)]
    skipped_chunks = 0
    budget_exceeded = False
    TIME_BUDGET = 300
    t_start = time.monotonic()

    async with httpx.AsyncClient(timeout=60) as client:
        for w_begin, w_end in windows:
            if budget_exceeded:
                break

            for idx, chunk in enumerate(chunks):
                elapsed = time.monotonic() - t_start
                if elapsed >= TIME_BUDGET:
                    remaining = len(chunks) - idx
                    logger.warning(
                        f"WB adv: time budget {TIME_BUDGET}s exceeded after "
                        f"{elapsed:.0f}s — skipping {remaining} remaining chunks "
                        f"({w_begin}→{w_end})"
                    )
                    skipped_chunks += remaining
                    budget_exceeded = True
                    break

                if idx > 0 or w_begin != windows[0][0]:
                    await asyncio.sleep(5)

                ids_param = ",".join(str(c) for c in chunk)
                url = (
                    f"https://advert-api.wildberries.ru/adv/v3/fullstats"
                    f"?ids={ids_param}&beginDate={w_begin}&endDate={w_end}"
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
                        f"({len(chunk)} campaigns, {w_begin}→{w_end})"
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

    result["_by_campaign"] = by_campaign
    return result


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
                    if not isinstance(c, dict):
                        continue
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
                    # campaign_type: use payment_type string
                    ctype = payment_type or c.get("bid_type")
                    campaigns.append(
                        {
                            "advertId": advert_id,
                            "name": name or str(advert_id or ""),
                            "type": ctype,
                            "status": c.get("status"),
                            "nm_ids": nm_ids,
                        }
                    )
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
