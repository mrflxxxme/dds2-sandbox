# ruff: noqa: RUF001, RUF002, RUF003
"""Загрузка справочника БИК ЦБ РФ (ED807) в таблицу `cbr_bic`.

ЦБ публикует справочник в формате ED807 (XML в кодировке windows-1251), `CBR_BIK_URL`
(`https://www.cbr.ru/s/newbik`) отдаёт актуальный ZIP напрямую. Берём для каждого банка
корр. счёт (`Accounts[RegulationAccountType=CRSA, AccountStatus=ACAC]`, начинается с 301)
и название (`ParticipantInfo@NameP`). Полный upsert по БИК (стейл-записи не удаляем — к/с
исторически верен; банк с отозванной лицензией всё равно не платим).

Зависимостей не добавляем: httpx/SQLAlchemy уже есть; XML — stdlib ElementTree (учитывает
объявленную в декларации кодировку cp1251 при парсинге из bytes).
"""

import io
import logging
import zipfile
import xml.etree.ElementTree as ET

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.refs import CbrBic
from backend.utils.time import utcnow

logger = logging.getLogger("dds.payment_request")

_DOWNLOAD_TIMEOUT = 60.0
_MAX_ZIP_BYTES = 50 * 1024 * 1024   # справочник ~0.1 МБ; потолок — анти-bomb
_MAX_XML_BYTES = 200 * 1024 * 1024  # распакованный XML ~0.7 МБ; потолок распаковки


def _localname(tag: str) -> str:
    """Локальное имя тега без namespace (ED807 объявляет default-namespace urn:cbr-ru:ed:v2.0)."""
    return tag.rsplit("}", 1)[-1]


def parse_ed807(raw_xml: bytes) -> list[tuple[str, str, str]]:
    """ED807-XML → [(bic, corr_account, name)] для банков с активным корр-счётом.

    ElementTree парсит из bytes с учётом кодировки из XML-декларации (windows-1251),
    поэтому имена банков (NameP) декодируются корректно."""
    # ED807 — плоский data-XML без DTD/сущностей. DOCTYPE/ENTITY = подмена / billion-laughs
    # → отвергаем (defense-in-depth; stdlib expat и так не резолвит внешние сущности).
    if b"<!DOCTYPE" in raw_xml or b"<!ENTITY" in raw_xml:
        raise ValueError("ED807: неожиданный DTD/ENTITY в XML")
    root = ET.fromstring(raw_xml)
    out: list[tuple[str, str, str]] = []
    for entry in root:
        if _localname(entry.tag) != "BICDirectoryEntry":
            continue
        bic = (entry.get("BIC") or "").strip()
        if len(bic) != 9 or not bic.isdigit():
            continue
        name = ""
        corr: str | None = None
        for ch in entry:
            ln = _localname(ch.tag)
            if ln == "ParticipantInfo" and not name:
                name = (ch.get("NameP") or "").strip()
            elif (
                ln == "Accounts"
                and ch.get("RegulationAccountType") == "CRSA"  # корр. счёт самой КО
                and ch.get("AccountStatus") == "ACAC"          # действующий
            ):
                acc = (ch.get("Account") or "").strip()
                # к/с структурно валиден: 20 цифр, префикс 301, последние 3 = последние 3 БИК
                if len(acc) == 20 and acc.isdigit() and acc.startswith("301") and acc[-3:] == bic[-3:]:
                    corr = acc
        if corr:
            out.append((bic, corr, name))
    return out


async def fetch_ed807_xml(url: str | None = None) -> bytes:
    """Скачать ZIP справочника с cbr.ru и вернуть байты XML внутри (распаковка ограничена)."""
    url = url or settings.CBR_BIK_URL
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.content
    if len(data) > _MAX_ZIP_BYTES:
        raise ValueError(f"CBR BIK zip too large: {len(data)} bytes")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not xml_names:
            raise ValueError("CBR BIK zip: нет XML внутри")
        with zf.open(xml_names[0]) as f:
            raw = f.read(_MAX_XML_BYTES + 1)  # +1 — чтобы поймать превышение, а не молча усечь
        if len(raw) > _MAX_XML_BYTES:
            raise ValueError(f"CBR BIK xml слишком большой (>{_MAX_XML_BYTES} байт)")
        return raw


async def sync_cbr_bic(db: AsyncSession, url: str | None = None) -> int:
    """Скачать ED807, распарсить и upsert'нуть в `cbr_bic`. Возвращает число банков."""
    raw = await fetch_ed807_xml(url)
    rows = parse_ed807(raw)
    if not rows:
        logger.warning("CBR BIC: справочник пуст после парсинга — таблицу не трогаем")
        return 0
    now = utcnow()
    # Дедуп по БИК ДО upsert (executemany c дублями ключа → CardinalityViolation).
    by_bic = {bic: {"bic": bic, "corr_account": corr, "name": name, "updated_at": now} for bic, corr, name in rows}
    values = list(by_bic.values())
    stmt = pg_insert(CbrBic).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["bic"],
        set_={"corr_account": stmt.excluded.corr_account, "name": stmt.excluded.name, "updated_at": stmt.excluded.updated_at},
    )
    await db.execute(stmt)
    await db.commit()
    logger.info("CBR BIC directory upserted: %d banks", len(values))
    return len(values)
