"""WB Advertising API — campaign management and stats.

Handles:
- fetch_ad_campaigns: list campaign IDs by status
- fetch_ad_stats: detailed per-nmId per-date ad stats with time budget
"""

import logging
import asyncio
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


async def fetch_ad_stats(api_key: str, campaign_ids: list[int],
                         begin_date: str, end_date: str) -> dict:
    """Fetch detailed ad stats per nmId per date.
    Returns {date: {nm_id: {sum, clicks, views}}}.

    Uses a 300-second time budget: returns partial data if exceeded.
    """
    result = {}
    chunks = [campaign_ids[i:i+50] for i in range(0, len(campaign_ids), 50)]
    skipped_chunks = 0
    budget_exceeded = False
    TIME_BUDGET = 300
    t_start = time.monotonic()

    async with httpx.AsyncClient(timeout=60) as client:
        for idx, chunk in enumerate(chunks):
            elapsed = time.monotonic() - t_start
            if elapsed >= TIME_BUDGET:
                remaining = len(chunks) - idx
                logger.warning(
                    f"WB adv: time budget {TIME_BUDGET}s exceeded after "
                    f"{elapsed:.0f}s — skipping {remaining} remaining chunks "
                    f"({begin_date}→{end_date})"
                )
                skipped_chunks += remaining
                budget_exceeded = True
                break

            if idx > 0:
                await asyncio.sleep(20)

            ids_param = ",".join(str(c) for c in chunk)
            url = (f"https://advert-api.wildberries.ru/adv/v3/fullstats"
                   f"?ids={ids_param}&beginDate={begin_date}&endDate={end_date}")

            chunk_ok = False
            for attempt in range(2):
                try:
                    resp = await client.get(url, headers={
                        "Accept": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    })
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
                            f"WB adv: sleep {wait}s would exceed budget, "
                            f"skipping chunk {idx+1}/{len(chunks)}"
                        )
                        break
                    await asyncio.sleep(wait)
                    continue
                elif resp.status_code != 200:
                    logger.error(f"WB adv stats error {resp.status_code}: {resp.text[:200]}")
                    break

                data = resp.json()
                if data is None:
                    logger.warning(
                        f"WB adv: empty JSON response for chunk {idx+1}, skipping"
                    )
                    break
                items = data if isinstance(data, list) else (data.get("data") or data)
                if not isinstance(items, list):
                    break

                for campaign in items:
                    if not campaign or not isinstance(campaign, dict):
                        continue
                    for day in campaign.get("days") or []:
                        res_date = (day.get("date") or "")[:10]
                        if not res_date:
                            continue
                        if res_date not in result:
                            result[res_date] = {}

                        for app in day.get("apps") or []:
                            for nm in app.get("nms") or []:
                                nm_id = nm.get("nmId")
                                if not nm_id:
                                    continue
                                if nm_id not in result[res_date]:
                                    result[res_date][nm_id] = {"sum": 0, "clicks": 0, "views": 0}
                                result[res_date][nm_id]["sum"] += nm.get("sum", 0)
                                result[res_date][nm_id]["clicks"] += nm.get("clicks", 0)
                                result[res_date][nm_id]["views"] += nm.get("views", 0)
                chunk_ok = True
                break

            if not chunk_ok:
                skipped_chunks += 1
                logger.warning(
                    f"WB adv: chunk {idx+1}/{len(chunks)} SKIPPED after retries "
                    f"({len(chunk)} campaigns, {begin_date}→{end_date})"
                )

    elapsed_total = time.monotonic() - t_start
    if skipped_chunks:
        logger.warning(
            f"WB adv: {skipped_chunks}/{len(chunks)} chunks skipped "
            f"for {begin_date}→{end_date} (elapsed {elapsed_total:.0f}s, "
            f"budget_exceeded={budget_exceeded})"
        )
    else:
        logger.info(
            f"WB adv: all {len(chunks)} chunks OK "
            f"for {begin_date}→{end_date} in {elapsed_total:.0f}s"
        )

    return result
