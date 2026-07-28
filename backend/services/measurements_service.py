"""
Чтение замеров WB: контрольные замеры складов + удержания за габариты.

Все запросы строго project-scoped, с фильтрами по периоду/артикулу/предмету/бренду
и пагинацией. Бренд в данных замеров WB отсутствует → берём по nm_id из
wb_funnel_daily (тот же источник, что у фильтров воронки) для фильтра и обогащения.
"""

import html
from collections import Counter
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import pytz
from sqlalchemy import String, cast, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.cost import Nomenclature
from backend.models.integrations import WbFunnelDaily
from backend.models.wb_finance import WbFinanceRow
from backend.models.wb_measurements import WbMeasurementPenalty, WbWarehouseMeasurement


_MSK = pytz.timezone("Europe/Moscow")


def _day_bounds(date_from: date | None, date_to: date | None) -> tuple[datetime | None, datetime | None]:
    """Границы периода в МСК (WB-замеры и кабинет живут по Москве).

    `measured_at`/`penalty_date` хранятся в UTC, но «день» пользователь и WB
    понимают по МСК. Раньше границы строились в UTC → ранне-утренние по МСК
    замеры (00:00–03:00 МСК = 21:00–00:00 UTC пред. суток) выпадали из выборки.
    """
    df = _MSK.localize(datetime.combine(date_from, time.min)) if date_from else None
    dt = _MSK.localize(datetime.combine(date_to, time.max)) if date_to else None
    return df, dt


def _search_cond(model: Any, search: str) -> Any:
    """Поиск по артикулу (nm_id) ИЛИ номеру замера (dim_id) — подстрокой."""
    esc = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{esc}%"
    return or_(
        cast(model.nm_id, String).like(like),
        cast(model.dim_id, String).like(like),
    )


def _nm_ids_for_brand(project_id: int, brand: str) -> Any:
    """Подзапрос: nm_id, у которых в воронке этот бренд."""
    return (
        select(WbFunnelDaily.nm_id)
        .where(WbFunnelDaily.project_id == project_id, WbFunnelDaily.brand == brand)
        .distinct()
    )


async def _brand_map(db: AsyncSession, project_id: int, nm_ids: set[int]) -> dict[int, str]:
    """nm_id → бренд (последний известный по дате) из воронки."""
    if not nm_ids:
        return {}
    rows = (
        await db.execute(
            select(WbFunnelDaily.nm_id, WbFunnelDaily.brand, func.max(WbFunnelDaily.date))
            .where(
                WbFunnelDaily.project_id == project_id,
                WbFunnelDaily.nm_id.in_(nm_ids),
                WbFunnelDaily.brand.isnot(None),
            )
            .group_by(WbFunnelDaily.nm_id, WbFunnelDaily.brand)
        )
    ).all()
    # На случай нескольких брендов у nm_id — берём с самой поздней датой.
    best: dict[int, tuple[date, str]] = {}
    for nm_id, brand, last_dt in rows:
        prev = best.get(nm_id)
        if prev is None or (last_dt and last_dt > prev[0]):
            best[nm_id] = (last_dt, brand)
    return {nm_id: b for nm_id, (_d, b) in best.items()}


async def _subject_map(db: AsyncSession, project_id: int, nm_ids: set[int]) -> dict[int, str]:
    """nm_id → предмет (fallback). WB иногда не шлёт subjectName в строке удержания →
    добираем из замеров склада (тот же subjectName), затем из карточки (Nomenclature.subject).
    """
    if not nm_ids:
        return {}
    out: dict[int, str] = {}
    # 1) из замеров склада — собственный subject_name замера
    wh = (
        await db.execute(
            select(WbWarehouseMeasurement.nm_id, func.max(WbWarehouseMeasurement.subject_name))
            .where(
                WbWarehouseMeasurement.project_id == project_id,
                WbWarehouseMeasurement.nm_id.in_(nm_ids),
                WbWarehouseMeasurement.subject_name.isnot(None),
            )
            .group_by(WbWarehouseMeasurement.nm_id)
        )
    ).all()
    for nm, s in wh:
        if s and s.strip():
            out[nm] = s
    # 2) фолбэк из номенклатуры — subjectName карточки WB
    missing = nm_ids - out.keys()
    if missing:
        nom = (
            await db.execute(
                select(Nomenclature.article_wb, func.max(Nomenclature.subject))
                .where(
                    Nomenclature.project_id == project_id,
                    Nomenclature.article_wb.in_(missing),
                    Nomenclature.subject.isnot(None),
                )
                .group_by(Nomenclature.article_wb)
            )
        ).all()
        for nm, s in nom:
            if s and s.strip():
                out[nm] = s
    return out


async def _card_volume_map(db: AsyncSession, project_id: int, nm_ids: set[int]) -> dict[int, Decimal]:
    """nm_id → объём карточки WB (л) из номенклатуры (L×W×H/1000, WB Content API).

    Одна карточка = один объём на все её баркоды → берём max как представитель.
    Нулевой/пустой объём (нет габаритов в карточке) отбрасываем.
    """
    if not nm_ids:
        return {}
    rows = (
        await db.execute(
            select(Nomenclature.article_wb, func.max(Nomenclature.volume_l))
            .where(
                Nomenclature.project_id == project_id,
                Nomenclature.article_wb.in_(nm_ids),
                Nomenclature.volume_l.isnot(None),
            )
            .group_by(Nomenclature.article_wb)
        )
    ).all()
    return {nm: Decimal(v) for nm, v in rows if v and Decimal(v) > 0}


async def list_warehouse_measurements(
    db: AsyncSession,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    nm_id: int | None = None,
    subject: str | None = None,
    brand: str | None = None,
    search: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[WbWarehouseMeasurement], int]:
    df, dt = _day_bounds(date_from, date_to)
    conds = [WbWarehouseMeasurement.project_id == project_id]
    if df is not None:
        conds.append(WbWarehouseMeasurement.measured_at >= df)
    if dt is not None:
        conds.append(WbWarehouseMeasurement.measured_at <= dt)
    if nm_id is not None:
        conds.append(WbWarehouseMeasurement.nm_id == nm_id)
    if subject:
        conds.append(WbWarehouseMeasurement.subject_name == subject)
    if brand:
        conds.append(WbWarehouseMeasurement.nm_id.in_(_nm_ids_for_brand(project_id, brand)))
    if search and search.strip():
        conds.append(_search_cond(WbWarehouseMeasurement, search))

    total = await db.scalar(select(func.count()).select_from(WbWarehouseMeasurement).where(*conds))
    rows = (
        await db.execute(
            select(WbWarehouseMeasurement)
            .where(*conds)
            .order_by(WbWarehouseMeasurement.measured_at.desc().nullslast(), WbWarehouseMeasurement.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    rows = list(rows)
    nm_ids = {r.nm_id for r in rows}
    bmap = await _brand_map(db, project_id, nm_ids)
    cvmap = await _card_volume_map(db, project_id, nm_ids)
    for r in rows:
        r.brand = bmap.get(r.nm_id)  # type: ignore[attr-defined]  # transient attr для схемы
        r.card_volume = cvmap.get(r.nm_id)  # type: ignore[attr-defined]  # объём карточки WB (л)
    return rows, int(total or 0)


async def list_measurement_penalties(
    db: AsyncSession,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    nm_id: int | None = None,
    subject: str | None = None,
    brand: str | None = None,
    search: str | None = None,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[WbMeasurementPenalty], int, Decimal, Decimal]:
    df, dt = _day_bounds(date_from, date_to)
    conds = [WbMeasurementPenalty.project_id == project_id]
    if df is not None:
        conds.append(WbMeasurementPenalty.penalty_date >= df)
    if dt is not None:
        conds.append(WbMeasurementPenalty.penalty_date <= dt)
    if nm_id is not None:
        conds.append(WbMeasurementPenalty.nm_id == nm_id)
    if subject:
        conds.append(WbMeasurementPenalty.subject_name == subject)
    if brand:
        conds.append(WbMeasurementPenalty.nm_id.in_(_nm_ids_for_brand(project_id, brand)))
    if search and search.strip():
        conds.append(_search_cond(WbMeasurementPenalty, search))

    agg = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(WbMeasurementPenalty.penalty_amount), 0),
                func.coalesce(func.sum(WbMeasurementPenalty.reversal_amount), 0),
            ).where(*conds)
        )
    ).one()
    total, total_penalty, total_reversal = int(agg[0]), Decimal(agg[1]), Decimal(agg[2])

    rows = (
        await db.execute(
            select(WbMeasurementPenalty)
            .where(*conds)
            .order_by(WbMeasurementPenalty.penalty_date.desc().nullslast(), WbMeasurementPenalty.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    rows = list(rows)
    bmap = await _brand_map(db, project_id, {r.nm_id for r in rows})
    smap = await _subject_map(db, project_id, {r.nm_id for r in rows if not (r.subject_name and r.subject_name.strip())})
    for r in rows:
        r.brand = bmap.get(r.nm_id)  # type: ignore[attr-defined]  # transient attr для схемы
        if not (r.subject_name and r.subject_name.strip()):
            r.subject_name = smap.get(r.nm_id)  # добор предмета из замеров/карточки
    return rows, total, total_penalty, total_reversal


async def summarize_penalties_by_article(
    db: AsyncSession,
    project_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    subject: str | None = None,
    brand: str | None = None,
    search: str | None = None,
) -> tuple[list[dict], dict]:
    """Сводка удержаний по артикулам: суммы удержаний/сторно, нетто, кол-во."""
    df, dt = _day_bounds(date_from, date_to)
    conds = [WbMeasurementPenalty.project_id == project_id]
    if df is not None:
        conds.append(WbMeasurementPenalty.penalty_date >= df)
    if dt is not None:
        conds.append(WbMeasurementPenalty.penalty_date <= dt)
    if subject:
        conds.append(WbMeasurementPenalty.subject_name == subject)
    if brand:
        conds.append(WbMeasurementPenalty.nm_id.in_(_nm_ids_for_brand(project_id, brand)))
    if search and search.strip():
        conds.append(_search_cond(WbMeasurementPenalty, search))

    rows = (
        await db.execute(
            select(
                WbMeasurementPenalty.nm_id,
                func.max(WbMeasurementPenalty.subject_name),
                func.coalesce(func.sum(WbMeasurementPenalty.penalty_amount), 0),
                func.coalesce(func.sum(WbMeasurementPenalty.reversal_amount), 0),
                func.count(),
                func.count(distinct(WbMeasurementPenalty.dim_id)),
            )
            .where(*conds)
            .group_by(WbMeasurementPenalty.nm_id)
        )
    ).all()

    nm_ids = {r[0] for r in rows}
    bmap = await _brand_map(db, project_id, nm_ids)
    smap = await _subject_map(db, project_id, {nm for nm, subj, *_ in rows if not (subj and subj.strip())})
    items = [
        {
            "nm_id": nm_id,
            "subject_name": subj if (subj and subj.strip()) else smap.get(nm_id),
            "brand": bmap.get(nm_id),
            "total_penalty": Decimal(pen),
            "total_reversal": Decimal(rev),
            "net": Decimal(pen) + Decimal(rev),
            "penalties_count": int(cnt),
            "measurements_count": int(dim_cnt),
        }
        for nm_id, subj, pen, rev, cnt, dim_cnt in rows
    ]
    # Сортировка по величине нетто-удержания (самые дорогие сверху).
    items.sort(key=lambda x: x["net"], reverse=True)

    totals = {
        "articles": len(items),
        "total_penalty": sum((i["total_penalty"] for i in items), Decimal(0)),
        "total_reversal": sum((i["total_reversal"] for i in items), Decimal(0)),
        "net": sum((i["net"] for i in items), Decimal(0)),
    }
    return items, totals


async def get_filters(db: AsyncSession, project_id: int) -> dict:
    """Списки брендов и предметов для выпадашек — только релевантные замерам."""
    # Предметы — из самих таблиц замеров (собственный subject_name).
    subj_wh = select(WbWarehouseMeasurement.subject_name).where(
        WbWarehouseMeasurement.project_id == project_id,
        WbWarehouseMeasurement.subject_name.isnot(None),
    )
    subj_pen = select(WbMeasurementPenalty.subject_name).where(
        WbMeasurementPenalty.project_id == project_id,
        WbMeasurementPenalty.subject_name.isnot(None),
    )
    subjects = {r[0] for r in (await db.execute(subj_wh.union(subj_pen))).all() if r[0]}

    # nm_id, встречающиеся в замерах → бренды по ним из воронки.
    measured_nm = select(WbWarehouseMeasurement.nm_id).where(
        WbWarehouseMeasurement.project_id == project_id
    ).union(
        select(WbMeasurementPenalty.nm_id).where(WbMeasurementPenalty.project_id == project_id)
    )
    brands = {
        r[0]
        for r in (
            await db.execute(
                select(WbFunnelDaily.brand)
                .where(
                    WbFunnelDaily.project_id == project_id,
                    WbFunnelDaily.brand.isnot(None),
                    WbFunnelDaily.nm_id.in_(measured_nm),
                )
                .distinct()
            )
        ).all()
        if r[0]
    }
    return {"brands": sorted(brands), "subjects": sorted(subjects)}


# ─── Ежедневная сводка замеров для Telegram (джоб 09:00 MSK) ──────────────────

_DIGEST_ATTENTION_PCT = 10  # порог отклонения замера от карточки, %
_DIGEST_MAX_ATTENTION = 10  # сколько «проблемных» артикулов показываем детально


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение по числу: 1 замер / 2 замера / 5 замеров."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    d = n % 10
    if d == 1:
        return one
    if 2 <= d <= 4:
        return few
    return many


def _fmt_l(v: Decimal | float | None) -> str:
    """Объём в литрах без хвостовых нулей: 48.0→«48», 62.5→«62.5»."""
    if v is None:
        return "—"
    return f"{float(v):.3f}".rstrip("0").rstrip(".")


async def warehouse_digest_data(
    db: AsyncSession, project_id: int, df: datetime, dt: datetime
) -> dict:
    """Данные сводки замеров склада за период [df, dt]: всего, по предметам, отклонения.

    `attention` — замеры, где |объём замера − объём карточки| / карточка ≥ 10 %
    (потенциальные удержания за габариты), отсортированы по убыванию отклонения.
    """
    rows = (
        await db.execute(
            select(
                WbWarehouseMeasurement.nm_id,
                WbWarehouseMeasurement.subject_name,
                WbWarehouseMeasurement.volume,
            )
            .where(
                WbWarehouseMeasurement.project_id == project_id,
                WbWarehouseMeasurement.measured_at >= df,
                WbWarehouseMeasurement.measured_at <= dt,
            )
            .order_by(WbWarehouseMeasurement.measured_at.desc())
            .limit(5000)
        )
    ).all()

    by_subject: Counter[str] = Counter()
    for _nm, subj, _vol in rows:
        by_subject[subj or "Без предмета"] += 1

    cvmap = await _card_volume_map(db, project_id, {nm for nm, _s, _v in rows})
    attention: list[dict] = []
    for nm, subj, vol in rows:
        card = cvmap.get(nm)
        if card is None or card <= 0 or vol is None:
            continue
        dev = (Decimal(vol) - card) / card * 100
        if abs(dev) >= _DIGEST_ATTENTION_PCT:
            attention.append(
                {"nm_id": nm, "subject": subj, "meas": Decimal(vol), "card": card, "dev": dev}
            )
    attention.sort(key=lambda a: abs(a["dev"]), reverse=True)

    return {
        "total": len(rows),
        "subjects": by_subject.most_common(),  # [(subject, count)], по убыванию
        "attention": attention,
    }


def build_measurement_digest_text(day_from: date, day_to: date, data: dict) -> str | None:
    """HTML-сводка замеров за период. None, если замеров нет (шлём — незачем)."""
    total = int(data.get("total") or 0)
    if total == 0:
        return None

    e = html.escape
    subjects = data.get("subjects") or []
    period = f"{day_from:%d.%m} – {day_to:%d.%m}"
    z = _ru_plural(total, "замер", "замера", "замеров")
    p = _ru_plural(len(subjects), "предмету", "предметам", "предметам")

    lines = [
        f"📐 <b>Замеры WB за {period}</b>",
        "",
        f"Поступило <b>{total}</b> {z} по <b>{len(subjects)}</b> {p}:",
        "",
    ]
    for subj, cnt in subjects:
        cz = _ru_plural(cnt, "замер", "замера", "замеров")
        lines.append(f"• {e(str(subj))} — <b>{cnt}</b> {cz}")

    attention = data.get("attention") or []
    if attention:
        lines.append("")
        az = _ru_plural(len(attention), "замер", "замера", "замеров")
        lines.append(f"⚠️ <b>Отклонение от карточки ≥{_DIGEST_ATTENTION_PCT}%: {len(attention)} {az}</b>")
        for a in attention[:_DIGEST_MAX_ATTENTION]:
            subj = f" «{e(str(a['subject']))}»" if a.get("subject") else ""
            lines.append(
                f"   • <code>{a['nm_id']}</code>{subj} — "
                f"{_fmt_l(a['meas'])} л vs {_fmt_l(a['card'])} л ({a['dev']:+.0f}%)"
            )
        extra = len(attention) - _DIGEST_MAX_ATTENTION
        if extra > 0:
            lines.append(f"   … и ещё {extra}")

    return "\n".join(lines)


# ─── Штрафы за габариты (из финотчёта) — блок сводки + переисточник UI ────────

# WB в финотчёте: «Занижение фактических габаритов упаковки товара» и «Сторно. …».
# Источник ИСТИНЫ по деньгам (совпадает с кабинетом), в отличие от analytics-API,
# который отдаёт габаритные удержания с задержкой в несколько дней.
_DIM_PENALTY_LIKE = "%абарит%"
_DIGEST_MAX_PENALTY_ARTS = 15  # артикулов на категорию в блоке сводки


def _fmt_money0(v: Decimal | float | None) -> str:
    """Рубли без копеек с пробелом-разделителем: 29869.80 → «29 870»."""
    return f"{float(v or 0):,.0f}".replace(",", " ")


async def _volume_compare_map(
    db: AsyncSession, project_id: int, nm_ids: set[int]
) -> dict[int, dict]:
    """nm_id → {card, meas, dev} — объём карточки vs последний замер и отклонение %."""
    if not nm_ids:
        return {}
    card = await _card_volume_map(db, project_id, nm_ids)
    rows = (
        await db.execute(
            select(WbWarehouseMeasurement.nm_id, WbWarehouseMeasurement.volume)
            .where(
                WbWarehouseMeasurement.project_id == project_id,
                WbWarehouseMeasurement.nm_id.in_(nm_ids),
                WbWarehouseMeasurement.volume.isnot(None),
            )
            .order_by(WbWarehouseMeasurement.measured_at.desc().nullslast())
        )
    ).all()
    meas: dict[int, Decimal] = {}
    for nm, vol in rows:
        if nm not in meas:  # первый по desc = последний замер
            meas[nm] = Decimal(vol)
    out: dict[int, dict] = {}
    for nm in nm_ids:
        cv, mv = card.get(nm), meas.get(nm)
        dev = (mv - cv) / cv * 100 if (cv and cv > 0 and mv is not None) else None
        out[nm] = {"card": cv, "meas": mv, "dev": dev}
    return out


async def finance_penalties_digest_data(db: AsyncSession, project_id: int, day: date) -> dict:
    """Штрафы за занижение габаритов за день (rr_dt) из финотчёта, по категориям→артикулам.

    Суммы — из `wb_finance_rows` (нетто с учётом сторно). Каждый артикул обогащён
    сравнением литража (объём карточки vs последний замер).
    """
    rows = (
        await db.execute(
            select(
                WbFinanceRow.nm_id,
                WbFinanceRow.subject_name,
                WbFinanceRow.brand_name,
                func.coalesce(func.sum(WbFinanceRow.penalty), 0),
            )
            .where(
                WbFinanceRow.project_id == project_id,
                WbFinanceRow.rr_dt == day,
                WbFinanceRow.bonus_type_name.ilike(_DIM_PENALTY_LIKE),
            )
            .group_by(WbFinanceRow.nm_id, WbFinanceRow.subject_name, WbFinanceRow.brand_name)
        )
    ).all()

    arts = [(nm, subj, brand, Decimal(pen)) for nm, subj, brand, pen in rows if Decimal(pen) != 0]
    total = sum((p for *_rest, p in arts), Decimal(0))
    vmap = await _volume_compare_map(db, project_id, {nm for nm, *_r in arts if nm})

    by_subject: dict[str, list[dict]] = {}
    for nm, subj, _brand, pen in arts:
        by_subject.setdefault(subj or "Без категории", []).append(
            {"nm_id": nm, "penalty": pen, "vol": vmap.get(nm, {})}
        )
    subjects = []
    for subj, items in by_subject.items():
        items.sort(key=lambda x: x["penalty"], reverse=True)
        subjects.append({"subject": subj, "total": sum((i["penalty"] for i in items), Decimal(0)), "items": items})
    subjects.sort(key=lambda s: s["total"], reverse=True)
    return {"total": total, "count": len(arts), "subjects": subjects}


def _vol_compare_str(vol: dict) -> str:
    """Сравнение литража: «замер X / карт Y (+Z%)», «… · карточка ✓» при совпадении."""
    meas, card, dev = vol.get("meas"), vol.get("card"), vol.get("dev")
    if meas is None and card is None:
        return "нет замера/карточки"
    base = f"замер {_fmt_l(meas)} л / карт {_fmt_l(card)} л"
    if dev is None:
        return base
    if abs(dev) < Decimal("0.5"):  # округляется до 0% → карточка совпадает
        return f"{base} · карточка ✓"
    return f"{base} ({dev:+.0f}%)"


def build_penalties_digest_text(day: date, data: dict) -> str | None:
    """HTML-блок «ПРОВЕРЬТЕ ГАБАРИТЫ» — штрафы за габариты за день по категориям.

    Таблица артикулов — моноширинным `<pre>` (в Telegram нет настоящих таблиц).
    None, если штрафов за день нет.
    """
    subjects = data.get("subjects") or []
    if not subjects:
        return None
    e = html.escape
    cnt = int(data.get("count") or 0)
    az = _ru_plural(cnt, "артикул", "артикула", "артикулов")

    lines = [
        "⚠️ <b>ПРОВЕРЬТЕ ГАБАРИТЫ</b>",
        f"Штрафы за занижение габаритов за {day:%d.%m}: "
        f"<b>{_fmt_money0(data.get('total'))} ₽</b> · {cnt} {az}",
    ]
    for s in subjects:
        lines.append("")
        lines.append(f"<b>{e(str(s['subject']))}</b> — {_fmt_money0(s['total'])} ₽")
        tbl = []
        for it in s["items"][:_DIGEST_MAX_PENALTY_ARTS]:
            row = f"{it['nm_id']}  {_fmt_money0(it['penalty']):>7} ₽  {_vol_compare_str(it['vol'])}"
            tbl.append(e(row))
        lines.append("<pre>" + "\n".join(tbl) + "</pre>")
        extra = len(s["items"]) - _DIGEST_MAX_PENALTY_ARTS
        if extra > 0:
            lines.append(f"   … и ещё {extra}")
    return "\n".join(lines)
