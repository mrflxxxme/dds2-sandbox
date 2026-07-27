# ruff: noqa: RUF002, RUF003 — русские комментарии и docstring
"""
Service: зеркало карточек WB (wb_product_cards) + импорт базы знаний из карточек.

Источники (ПУБЛИЧНЫЕ API WB, без ключа продавца):
- card.json:  https://basket-XX.wbbasket.ru/vol{vol}/part{part}/{nm}/info/ru/card.json
  (vol = nm//100000, part = nm//1000, XX — basket по таблице _BASKET_BOUNDS).
  Поля: imt_name (название), subj_name (предмет), description, contents
  (комплектация), options[] {name, value, charc_type, is_variable, variable_values[]},
  media.photo_count (число фото — перебор по 404 НЕ нужен).
- detail:     https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=0&nm={nm}
  → products[0].brand (в card.json бренда нет), products[0].pics (запасной счётчик фото).
- фото:       /vol{vol}/part{part}/{nm}/images/big/{i}.webp, i = 1..photo_count (кап 10).
  Байты НЕ скачиваем — в зеркале только URL (photo_urls).

Сеть: прямой TLS из dev-окружения к WB режется фильтром → ходим через SOCKS5
(env WB_CARDS_SOCKS_PROXY="host:port"; в контейнере host.docker.internal:1080).
Raw-сокет рукопожатие (httpx в контейнере без socksio) — см. _http_get_json.
404 по карточке — товар удалён/не выгружен: пропускаем с подсчётом, прогон не валим.

Импорт в КБ (import_kb_from_cards): description → topic='Описание', contents →
topic='Комплект', каждая характеристика → topic по map_characteristic_topic,
answer="{name}: {value}", source='card'. Дедуп-ключ — md5("card:{nm}:{имя}"),
повторный импорт ОБНОВЛЯЕТ изменившиеся значения (upsert по hash), дублей нет.
Записи source='manual'/'import' импорт не трогает.

TODO: извлечение фактов с фото (размерная сетка, состав на этикетке) — нужен
vision LLM; photo_urls уже лежат в зеркале, осталось прогнать через модель.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import ssl
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WBFeedback, WBProductCard, WBProductKB, WBQuestion
from backend.services.reply_service import _norm_question
from backend.utils.time import utcnow

logger = logging.getLogger("dds.reviews.cards")

_THROTTLE_SEC = 0.5  # вежливый троттлинг публичных хостов WB
_MAX_PHOTOS = 10  # кап на число URL фото в зеркале
_DESC_KB_LIMIT = 3000  # лимит длины описания в записи КБ (в зеркале — целиком)

# Таблица диапазонов vol → номер basket-хоста (basket-01..basket-29).
# vol <= bound[i] → basket i+1; дальше экстраполяция шагом ~312 vol на basket.
_BASKET_BOUNDS: tuple[int, ...] = (
    143, 287, 431, 719, 1007, 1061, 1115, 1169, 1313, 1601, 1655, 1919, 2045, 2189,
    2405, 2621, 2837, 3053, 3269, 3485, 3701, 3917, 4133, 4349, 4565, 4877, 5189,
    5501, 5813,
)
_BASKET_STEP = 312  # шаг экстраполяции за пределами таблицы


def basket_number(nm_id: int) -> int:
    """Номер basket-хоста для nm_id (по vol = nm_id // 100000)."""
    vol = nm_id // 100000
    for i, bound in enumerate(_BASKET_BOUNDS, start=1):
        if vol <= bound:
            return i
    over = vol - _BASKET_BOUNDS[-1]
    return len(_BASKET_BOUNDS) + (over + _BASKET_STEP - 1) // _BASKET_STEP


def basket_host(nm_id: int) -> str:
    return f"basket-{basket_number(nm_id):02d}.wbbasket.ru"


def card_json_path(nm_id: int) -> str:
    """Путь card.json на basket-хосте."""
    return f"/vol{nm_id // 100000}/part{nm_id // 1000}/{nm_id}/info/ru/card.json"


def photo_urls(nm_id: int, count: int, basket: int | None = None) -> list[str]:
    """URL больших фото карточки (i = 1..count, кап _MAX_PHOTOS).

    basket — реальный хост, с которого скачана карточка (в экстраполяционной
    зоне отличается от табличного basket_number); None — табличный.
    """
    b = basket if basket is not None else basket_number(nm_id)
    base = f"https://basket-{b:02d}.wbbasket.ru/vol{nm_id // 100000}/part{nm_id // 1000}/{nm_id}/images/big"
    return [f"{base}/{i}.webp" for i in range(1, min(count, _MAX_PHOTOS) + 1)]


# ─── Сеть: raw-сокет GET (прямой TLS или через SOCKS5) ────────────────────────


# Полноценный UA браузера: WAF card.wb.ru режет голый "Mozilla/5.0" (403),
# basket-хосты лояльны, но единый UA проще и безопаснее.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _dechunk(raw: bytes) -> bytes:
    """Склеить тело HTTP/1.1 chunked encoding в один буфер."""
    out = b""
    pos = 0
    while True:
        eol = raw.find(b"\r\n", pos)
        if eol == -1:
            break
        try:
            size = int(raw[pos:eol].split(b";")[0].strip(), 16)
        except ValueError:
            break
        if size == 0:
            break
        pos = eol + 2
        out += raw[pos : pos + size]
        pos += size + 2  # данные + CRLF
    return out


def _http_get_json(host: str, path: str, proxy: tuple[str, int] | None = None, timeout: int = 30) -> tuple[int, dict]:
    """
    GET JSON с хоста WB (raw HTTP/1.1, Connection: close).

    proxy=("host", port) — SOCKS5 no-auth; None — прямое TLS-соединение.
    Возвращает (http_status, parsed_json|{}). Не-2xx НЕ бросает — решение вызывающему.
    """
    if proxy:
        s = socket.create_connection(proxy, timeout=timeout)
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            s.close()
            raise RuntimeError("SOCKS5: нет no-auth метода")
        h = host.encode()
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + (443).to_bytes(2, "big"))
        reply = s.recv(10)
        if len(reply) < 2 or reply[1] != 0:
            s.close()
            raise RuntimeError(f"SOCKS5 connect failed: status={reply[1] if len(reply) > 1 else '?'}")
    else:
        s = socket.create_connection((host, 443), timeout=timeout)
    try:
        tls = ssl.create_default_context().wrap_socket(s, server_hostname=host)
        req = (
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
            f"User-Agent: {_USER_AGENT}\r\nAccept: application/json\r\nConnection: close\r\n\r\n"
        )
        tls.sendall(req.encode())
        buf = b""
        while True:
            chunk = tls.recv(65536)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    head, _, body = buf.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    if b"transfer-encoding: chunked" in head.lower():
        body = _dechunk(body)
    if status != 200:
        return status, {}
    return status, json.loads(body)


def _proxy_from_env() -> tuple[str, int] | None:
    """SOCKS5-прокси из env WB_CARDS_SOCKS_PROXY="host:port" (dev-машины с DPI-фильтром)."""
    raw = (os.environ.get("WB_CARDS_SOCKS_PROXY") or "").strip()
    if not raw:
        return None
    host, _, port = raw.rpartition(":")
    return (host or "127.0.0.1", int(port or "1080"))


def fetch_nm_card(nm_id: int, proxy: tuple[str, int] | None = None) -> dict:
    """
    Скачать карточку nm_id с публичных хостов WB.

    Возвращает {"status": int, "card": dict|None, "detail": dict|None,
    "basket": int|None}: status — HTTP-код card.json (404 → карточки нет,
    detail не дёргаем); basket — РЕАЛЬНЫЙ basket-хост, с которого скачано
    (нужен для URL фото — в экстраполяционной зоне vol > 5813 границы таблицы
    «плавают», поэтому при не-200 сканируем соседние basket ±4/+2).
    detail — products[0] ответа cards/v4/detail (brand/pics), сбой не фатален.
    """
    if proxy is None:
        proxy = _proxy_from_env()
    guess = basket_number(nm_id)
    path = card_json_path(nm_id)
    status, card, basket = _try_card_on_basket(guess, path, proxy)
    if status != 200 and nm_id // 100000 > _BASKET_BOUNDS[-1]:
        # экстраполяционная зона: реальные границы шире/уже табличных — сканируем
        for b in (guess - 1, guess - 2, guess + 1, guess - 3, guess + 2, guess - 4):
            if b < 1:
                continue
            st2, card2, _ = _try_card_on_basket(b, path, proxy)
            if st2 == 200:
                status, card, basket = st2, card2, b
                break
            if st2 != 404:
                status = st2  # последний «не-404» — честнее в отчёте ошибок
    if status != 200:
        return {"status": status, "card": None, "detail": None, "basket": None}
    detail: dict | None = None
    try:
        d_status, d_data = _http_get_json(
            "card.wb.ru",
            f"/cards/v4/detail?appType=1&curr=rub&dest=-1257786&spp=0&nm={nm_id}",
            proxy,
        )
        products = d_data.get("products") or [] if d_status == 200 else []
        if products:
            detail = products[0]
    except Exception as e:  # noqa: BLE001 — бренд/pics необязательны, живём без detail
        logger.warning("WB cards: detail fetch failed for nm %d: %s", nm_id, e)
    return {"status": status, "card": card, "detail": detail, "basket": basket}


def _try_card_on_basket(
    basket: int, path: str, proxy: tuple[str, int] | None
) -> tuple[int, dict, int | None]:
    """GET card.json с конкретного basket-хоста; сетевые сбои → (0, {}, None)."""
    try:
        status, card = _http_get_json(f"basket-{basket:02d}.wbbasket.ru", path, proxy)
    except Exception as e:  # noqa: BLE001 — несуществующий basket/сбой прокси
        logger.debug("WB cards: basket-%02d недоступен: %s", basket, e)
        return 0, {}, None
    return status, card, basket if status == 200 else None


# ─── Парсинг card.json ────────────────────────────────────────────────────────


def normalize_options(options: list | None) -> list[dict]:
    """options[] card.json → нормализованный список {name, value} (пустые выбрасываем)."""
    out: list[dict] = []
    for o in options or []:
        if not isinstance(o, dict):
            continue
        name = str(o.get("name") or "").strip()
        value = str(o.get("value") or "").strip()
        if name and value:
            out.append({"name": name, "value": value})
    return out


def build_card_row(
    project_id: int,
    nm_id: int,
    card: dict,
    detail: dict | None,
    now: datetime,
    basket: int | None = None,
) -> dict:
    """card.json (+detail) → строка wb_product_cards для upsert.

    basket — реальный basket-хост ответа (для URL фото); None — табличный.
    """
    photo_count = 0
    media = card.get("media") or {}
    if isinstance(media, dict):
        photo_count = int(media.get("photo_count") or 0)
    if not photo_count and detail:
        photo_count = int(detail.get("pics") or 0)
    return {
        "project_id": project_id,
        "nm_id": nm_id,
        "title": (card.get("imt_name") or "").strip() or None,
        "brand": ((detail or {}).get("brand") or "").strip() or None,
        "subject": (card.get("subj_name") or "").strip() or None,
        "description": (card.get("description") or "").strip() or None,
        "contents": (card.get("contents") or "").strip() or None,
        "characteristics": normalize_options(card.get("options")),
        "photo_urls": photo_urls(nm_id, photo_count, basket),
        "synced_at": now,
    }


# ─── Синк карточек ────────────────────────────────────────────────────────────

# Тип fetcher'а: nm_id → {"status": int, "card": dict|None, "detail": dict|None}
CardFetcher = Callable[[int], Awaitable[dict]]


async def collect_project_nm_ids(db: AsyncSession, project_id: int) -> list[int]:
    """Distinct nm_id проекта из базы знаний и зеркал вопросов/отзывов."""
    out: set[int] = set()
    for model in (WBProductKB, WBQuestion, WBFeedback):
        rows = (
            await db.execute(
                select(model.nm_id)
                .where(model.project_id == project_id, model.nm_id.isnot(None))
                .distinct()
            )
        ).all()
        out.update(int(nm) for (nm,) in rows if nm)
    return sorted(out)


async def upsert_card_rows(db: AsyncSession, rows: list[dict]) -> int:
    """Upsert карточек по (project_id, nm_id). Возвращает число строк."""
    if not rows:
        return 0
    stmt = pg_insert(WBProductCard).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["project_id", "nm_id"],  # уникальный индекс uq_wb_product_cards_project_nm
        set_={
            "title": stmt.excluded.title,
            "brand": stmt.excluded.brand,
            "subject": stmt.excluded.subject,
            "description": stmt.excluded.description,
            "contents": stmt.excluded.contents,
            "characteristics": stmt.excluded.characteristics,
            "photo_urls": stmt.excluded.photo_urls,
            "synced_at": stmt.excluded.synced_at,
            "updated_at": utcnow(),
        },
    )
    await db.execute(stmt)
    return len(rows)


async def sync_project_cards(
    db: AsyncSession,
    project_id: int,
    nm_ids: list[int] | None = None,
    *,
    fetcher: CardFetcher | None = None,
    throttle_sec: float = _THROTTLE_SEC,
) -> dict:
    """
    Скачать карточки WB и upsert'нуть в wb_product_cards.

    nm_ids=None → собрать из КБ/зеркал (collect_project_nm_ids).
    fetcher — переопределение сети (тесты/dev-скрипт с SOCKS5); по умолчанию
    fetch_nm_card через env-прокси. 404 → not_found (пропуск), прочие сбои →
    errors (пропуск): прогон не валится на отдельных товарах.
    Возвращает {"cards_total", "synced", "not_found", "errors"}.
    """
    if nm_ids is None:
        nm_ids = await collect_project_nm_ids(db, project_id)

    if fetcher is None:

        async def fetcher(nm: int) -> dict:  # type: ignore[no-redef]
            return await asyncio.to_thread(fetch_nm_card, nm)

    synced = not_found = errors = 0
    now = utcnow()
    for i, nm in enumerate(nm_ids):
        if i and throttle_sec > 0:
            await asyncio.sleep(throttle_sec)
        try:
            res = await fetcher(int(nm))
        except Exception as e:  # noqa: BLE001 — сбой сети на одном товаре не валит прогон
            errors += 1
            logger.warning("WB cards sync: project %d — nm %d fetch error: %s", project_id, nm, e)
            continue
        if res.get("status") != 200 or not res.get("card"):
            if res.get("status") == 404:
                not_found += 1
            else:
                errors += 1
                logger.warning(
                    "WB cards sync: project %d — nm %d HTTP %s",
                    project_id, nm, res.get("status"),
                )
            continue
        row = build_card_row(
            project_id, int(nm), res["card"], res.get("detail"), now, res.get("basket")
        )
        await upsert_card_rows(db, [row])
        synced += 1
        if synced % 25 == 0:
            await db.commit()  # промежуточный коммит — прогресс не теряется при обрыве
    await db.commit()

    logger.info(
        "WB cards sync: project %d — total=%d, synced=%d, not_found=%d, errors=%d",
        project_id, len(nm_ids), synced, not_found, errors,
    )
    return {"cards_total": len(nm_ids), "synced": synced, "not_found": not_found, "errors": errors}


def _card_to_dict(c: WBProductCard) -> dict:
    return {
        "nm_id": c.nm_id,
        "title": c.title,
        "brand": c.brand,
        "subject": c.subject,
        "description": c.description,
        "contents": c.contents,
        "characteristics": c.characteristics or [],
        "photo_urls": c.photo_urls or [],
        "synced_at": c.synced_at.isoformat() if c.synced_at else None,
    }


async def get_card(db: AsyncSession, project_id: int, nm_id: int) -> dict | None:
    """Карточка товара из зеркала (None — ещё не синкнута)."""
    c = (
        await db.execute(
            select(WBProductCard).where(
                WBProductCard.project_id == project_id, WBProductCard.nm_id == nm_id
            )
        )
    ).scalar_one_or_none()
    return _card_to_dict(c) if c is not None else None


# ─── Импорт базы знаний из карточек ───────────────────────────────────────────

# Маппинг имени характеристики WB на тему КБ: первое совпадение побеждает,
# порядок важен (специфичные — раньше; «материал» раньше «длина/вес» и т.п.).
_CHAR_TOPIC_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Цвет", ("цвет", "оттенок", "расцветк")),
    ("Состав", ("состав", "материал", "ткань", "наполнител", "покрыти", "фурнитур")),
    ("Комплект", ("комплект", "количеств", "число ", "в наборе", "составляющ")),
    ("Гарантия", ("гаранти",)),
    ("Размер", ("размер", "длина", "ширина", "высота", "глубина", "вес", "обхват",
                "габарит", "диаметр", "объем", "объём", "толщина", "рост")),
)


def map_characteristic_topic(name: str | None) -> str:
    """Тема КБ по имени характеристики карточки (Цвет/Состав/Комплект/…/Прочее)."""
    t = (name or "").lower()
    for topic, keywords in _CHAR_TOPIC_KEYWORDS:
        if any(kw in t for kw in keywords):
            return topic
    return "Прочее"


def _card_hash(nm_id: int, key: str) -> str:
    """md5 дедуп-ключ записи КБ из карточки: устойчив к повторному импорту."""
    return hashlib.md5(f"card:{nm_id}:{_norm_question(key)}".encode()).hexdigest()  # noqa: S324 — дедуп-ключ, не секрет


def _desc_for_kb(description: str) -> str:
    """Описание для записи КБ: длинные (> _DESC_KB_LIMIT) режем по границе предложения."""
    if len(description) <= _DESC_KB_LIMIT:
        return description
    cut = description[:_DESC_KB_LIMIT]
    dot = cut.rfind(". ")
    if dot >= _DESC_KB_LIMIT // 2:
        return cut[: dot + 1]
    return cut.rstrip() + "…"


def _kb_entries_from_card(card: WBProductCard) -> list[dict]:
    """Карточка зеркала → список записей КБ (topic/answer/hash) для upsert.

    Дедуп по нормализованному answer ВНУТРИ карточки: contents и характеристика
    «Комплектация» часто дублируют один факт под разными ключами — берём один.
    """
    entries: list[dict] = []
    seen_answers: set[str] = set()

    def _add(topic: str, answer: str, hash_key: str) -> None:
        norm = _norm_question(answer)
        if not norm or norm in seen_answers:
            return
        seen_answers.add(norm)
        entries.append({"topic": topic, "answer": answer, "hash": _card_hash(nm, hash_key)})

    nm = int(card.nm_id)
    if card.description:
        _add("Описание", _desc_for_kb(card.description), "__description__")
    if card.contents:
        _add("Комплект", f"Комплектация: {card.contents}", "__contents__")
    for ch in card.characteristics or []:
        name = (ch.get("name") or "").strip()
        value = (ch.get("value") or "").strip()
        if not name or not value:
            continue
        _add(map_characteristic_topic(name), f"{name}: {value}", name)
    return entries


async def import_kb_from_cards(db: AsyncSession, project_id: int) -> dict:
    """
    Импорт базы знаний из зеркала карточек (wb_product_cards → wb_product_kb).

    Каждая карточка даёт записи: описание (topic='Описание'), комплектация
    (topic='Комплект') и по одной на характеристику (answer «{name}: {value}»,
    topic — map_characteristic_topic), все source='card', question_example=NULL.
    Дедуп по (project_id, nm_id, md5("card:{nm}:{ключ}")): повторный импорт
    не плодит дубли, а изменившееся значение ОБНОВЛЯЕТ answer/topic записи.
    Записи source='manual'/'import' не затрагиваются.
    """
    cards = (
        await db.execute(
            select(WBProductCard).where(WBProductCard.project_id == project_id)
        )
    ).scalars().all()

    existing: dict[tuple[int, str], WBProductKB] = {}
    rows = (
        await db.execute(
            select(WBProductKB).where(
                WBProductKB.project_id == project_id,
                WBProductKB.source == "card",
                WBProductKB.question_hash.isnot(None),
            )
        )
    ).scalars().all()
    for r in rows:
        existing[(int(r.nm_id), r.question_hash)] = r

    created = updated = unchanged = 0
    for card in cards:
        for e in _kb_entries_from_card(card):
            key = (int(card.nm_id), e["hash"])
            cur = existing.get(key)
            if cur is None:
                db.add(WBProductKB(
                    project_id=project_id,
                    nm_id=card.nm_id,
                    topic=e["topic"],
                    question_example=None,
                    answer=e["answer"],
                    source="card",
                    question_hash=e["hash"],
                ))
                created += 1
            elif cur.answer != e["answer"] or cur.topic != e["topic"]:
                # enabled не трогаем: мягкое отключение продавцом переживает ресинк
                cur.answer = e["answer"]
                cur.topic = e["topic"]
                updated += 1
            else:
                unchanged += 1
    await db.commit()

    logger.info(
        "KB import from cards: project %d — cards=%d, created=%d, updated=%d, unchanged=%d",
        project_id, len(cards), created, updated, unchanged,
    )
    return {
        "cards_total": len(cards),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
    }
