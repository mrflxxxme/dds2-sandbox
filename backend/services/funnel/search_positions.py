"""
Органические позиции товара по поисковым фразам — для кластеризатора («Позиция»/«Была»).

Сбор — из публичного поиска WB (wb_search_client.find_position). Два режима:
- collect_one — синхронно одну фразу (кнопка-кругляшок в ячейке), сразу отдаёт результат;
- start_collection/_run — фоново по списку фраз, ПОРЦИОННЫМ коммитом (позиции появляются
  по ходу, не всё в конце), с Play/Stop и счётчиком троттлинга (429 → «слишком частый запрос»).

Снимки копятся в WbSearchPosition: «Позиция» = последний, «Была» = предыдущий. Позиция
привязана к (nm_id, фраза), не к кампании — переиспользуется.
"""

import asyncio
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import AsyncSessionLocal
from backend.integrations.wb_search_client import find_position
from backend.models.integrations import WbSearchPosition
from backend.utils.time import utcnow

logger = logging.getLogger("dds.funnel")

_CONCURRENCY = 3  # WB-поиск жёстко троттлит по IP — держим параллелизм низким
_CHUNK = 15  # коммитим порциями, чтобы позиции появлялись по ходу сбора
_MAX_PAGES_CAP = 10

# Состояние фонового сбора по (project_id, nm_id)
_progress: dict[tuple[int, int], dict] = {}
_tasks: dict[tuple[int, int], asyncio.Task] = {}  # держим ссылку (иначе GC убьёт задачу)
_stop: dict[tuple[int, int], bool] = {}


def _empty_progress() -> dict:
    return {"status": "idle", "done": 0, "total": 0, "throttled": 0, "error": None}


def get_progress(project_id: int, nm_id: int) -> dict:
    return _progress.get((project_id, nm_id), _empty_progress())


async def _phrase_snapshot(db: AsyncSession, project_id: int, nm_id: int, phrase: str) -> dict:
    """Последний + предыдущий снимок одной фразы: {position, prev, depth, at}."""
    rows = (
        await db.execute(
            select(WbSearchPosition.position, WbSearchPosition.depth, WbSearchPosition.captured_at)
            .where(
                WbSearchPosition.project_id == project_id,
                WbSearchPosition.nm_id == nm_id,
                WbSearchPosition.phrase == phrase,
            )
            .order_by(WbSearchPosition.captured_at.desc())
            .limit(2)
        )
    ).all()
    if not rows:
        return {"position": None, "prev": None, "depth": None, "at": None}
    pos, depth, at = rows[0]
    prev = rows[1][0] if len(rows) > 1 else None
    return {"position": pos, "prev": prev, "depth": depth, "at": at.isoformat() if at else None}


async def collect_one(
    db: AsyncSession, project_id: int, nm_id: int, phrase: str, max_pages: int = 5
) -> dict:
    """Синхронно собрать позицию ОДНОЙ фразы (кнопка в ячейке). Пишет снимок, отдаёт результат.

    throttled=True — WB ограничил (429, страницу получить не удалось) → «слишком частый запрос».
    """
    phrase = (phrase or "").strip()
    if not phrase:
        return {"phrase": phrase, "position": None, "prev": None, "depth": 0, "throttled": False}
    max_pages = max(1, min(int(max_pages), _MAX_PAGES_CAP))
    async with httpx.AsyncClient() as client:
        pos, depth = await find_position(client, nm_id, phrase, max_pages=max_pages)
    db.add(WbSearchPosition(
        project_id=project_id, nm_id=nm_id, phrase=phrase,
        position=pos, depth=depth, captured_at=utcnow(),
    ))
    await db.commit()
    snap = await _phrase_snapshot(db, project_id, nm_id, phrase)
    snap["phrase"] = phrase
    snap["throttled"] = pos is None and depth == 0  # ничего не проверено = троттлинг/пустая выдача
    return snap


def start_collection(project_id: int, nm_id: int, phrases: list[str], max_pages: int = 5) -> dict:
    """Запустить фоновый сбор. Идемпотентно: если уже идёт — вернуть текущий прогресс."""
    key = (project_id, nm_id)
    cur = _progress.get(key)
    if cur and cur.get("status") == "running":
        return {"started": False, **cur}
    uniq = list(dict.fromkeys(p.strip() for p in phrases if p and p.strip()))
    max_pages = max(1, min(int(max_pages), _MAX_PAGES_CAP))
    if not uniq:
        _progress[key] = {**_empty_progress(), "status": "done"}
        return {"started": False, **_progress[key]}
    _progress[key] = {"status": "running", "done": 0, "total": len(uniq), "throttled": 0, "error": None}
    _stop[key] = False
    task = asyncio.create_task(_run(project_id, nm_id, uniq, max_pages))
    _tasks[key] = task  # держим ссылку, иначе задачу соберёт GC (event loop хранит weak ref)
    task.add_done_callback(lambda _t: _tasks.pop(key, None))
    return {"started": True, **_progress[key]}


def stop_collection(project_id: int, nm_id: int) -> dict:
    """Мягко остановить сбор: флаг проверяется между порциями, частичный результат сохраняется."""
    key = (project_id, nm_id)
    _stop[key] = True
    cur = _progress.get(key)
    if cur and cur.get("status") == "running":
        cur["status"] = "stopping"
    return {"stopping": True}


async def _run(project_id: int, nm_id: int, phrases: list[str], max_pages: int) -> None:
    key = (project_id, nm_id)
    now = utcnow()
    sem = asyncio.Semaphore(_CONCURRENCY)
    done = 0
    throttled = 0

    async def fetch_one(client: httpx.AsyncClient, phrase: str) -> tuple[str, int | None, int] | None:
        async with sem:
            if _stop.get(key):
                return None
            try:
                pos, depth = await find_position(client, nm_id, phrase, max_pages=max_pages)
            except Exception:  # noqa: BLE001 — одна фраза не валит весь сбор
                pos, depth = None, 0
            return phrase, pos, depth

    try:
        async with httpx.AsyncClient() as client:
            for i in range(0, len(phrases), _CHUNK):
                if _stop.get(key):
                    break
                chunk = phrases[i:i + _CHUNK]
                res = await asyncio.gather(*(fetch_one(client, p) for p in chunk))
                rows = []
                for r in res:
                    if r is None:
                        continue
                    phrase, pos, depth = r
                    if pos is None and depth == 0:
                        throttled += 1
                    rows.append(WbSearchPosition(
                        project_id=project_id, nm_id=nm_id, phrase=phrase,
                        position=pos, depth=depth, captured_at=now,
                    ))
                    done += 1
                if rows:  # коммит порции — позиции появляются в таблице по ходу
                    async with AsyncSessionLocal() as db:
                        db.add_all(rows)
                        await db.commit()
                p = _progress.get(key)
                if p:
                    p.update(done=done, throttled=throttled)
        stopped = _stop.get(key, False)
        _progress[key] = {
            "status": "stopped" if stopped else "done",
            "done": done, "total": len(phrases), "throttled": throttled, "error": None,
        }
        logger.info("collect positions: project %s nm %s — %d/%d, throttled=%d, stopped=%s",
                    project_id, nm_id, done, len(phrases), throttled, stopped)
    except asyncio.CancelledError:
        _progress[key] = {"status": "stopped", "done": done, "total": len(phrases),
                          "throttled": throttled, "error": "cancelled"}
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("collect positions failed: project %s nm %s: %s", project_id, nm_id, e)
        _progress[key] = {"status": "error", "done": done, "total": len(phrases),
                          "throttled": throttled, "error": str(e)[:200]}
    finally:
        _stop.pop(key, None)


async def get_positions_map(db: AsyncSession, project_id: int, nm_id: int) -> dict[str, dict]:
    """Для каждой фразы nmId — последняя и предыдущая позиция из снимков.

    {phrase: {position, prev, depth, at}}. position/prev могут быть None (не найден).
    """
    rows = (
        await db.execute(
            select(
                WbSearchPosition.phrase, WbSearchPosition.position,
                WbSearchPosition.depth, WbSearchPosition.captured_at,
            )
            .where(WbSearchPosition.project_id == project_id, WbSearchPosition.nm_id == nm_id)
            .order_by(WbSearchPosition.phrase, WbSearchPosition.captured_at.desc())
            .limit(50000)
        )
    ).all()
    out: dict[str, dict] = {}
    for phrase, pos, depth, at in rows:
        e = out.get(phrase)
        if e is None:  # первый (самый свежий) снимок фразы
            out[phrase] = {"position": pos, "prev": None, "depth": depth,
                           "at": at.isoformat() if at else None, "_seen": 1}
        elif e["_seen"] == 1:  # второй снимок = «Была»
            e["prev"] = pos
            e["_seen"] = 2
    for e in out.values():
        e.pop("_seen", None)
    return out


async def get_position_history(
    db: AsyncSession, project_id: int, nm_id: int, phrase: str, limit: int = 30
) -> list[dict]:
    """История позиций одной фразы (по возрастанию времени) — для спарклайна."""
    rows = (
        await db.execute(
            select(WbSearchPosition.position, WbSearchPosition.captured_at)
            .where(
                WbSearchPosition.project_id == project_id,
                WbSearchPosition.nm_id == nm_id,
                WbSearchPosition.phrase == phrase,
            )
            .order_by(WbSearchPosition.captured_at.desc())
            .limit(limit)
        )
    ).all()
    return [{"position": p, "at": a.isoformat() if a else None} for p, a in reversed(rows)]
