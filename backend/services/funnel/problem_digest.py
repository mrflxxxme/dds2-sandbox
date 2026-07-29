# ruff: noqa: RUF001, RUF002, RUF003
"""«Проблемные товары» — ежедневная сводка по брендам в Telegram.

Собирает из воронки продаж (неделя + вчера) и «Управления рекламой» товары
с плохими показателями и раскладывает по брендам в секции:
🔥 высокий ДРР · 💸 низкая маржа · 🐌 низкая оборачиваемость · 💤 реклама не
работает · 📦 заканчиваются остатки ВБ · 🚫 нет остатков ВБ · ⏳ нехватка
бюджета (кампании). В чат уходит текст с ОБЩЕЙ выручкой по всем товарам
(бренды → категории; решение юзера 23.07: проблемные товары в тексте НЕ
перечисляем — они в приложенном xlsx и Google-таблице).

Настройка — project_settings key `ads_problem_digest` (без миграций):
{"enabled": bool, "chat_ids": [int], "exclude_brands": [str], пороги}.
Рассылает scheduler-джоба problem_digest в 09:30 МСК; по понедельникам —
дополнительный недельный файл (прошлая неделя против позапрошлой).
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

import pytz
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Project

logger = logging.getLogger("dds.funnel.problem_digest")

MSK = pytz.timezone("Europe/Moscow")

DIGEST_SETTINGS_KEY = "ads_problem_digest"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": False,
    "chat_ids": [],
    "exclude_brands": [],
    "turnover_days": 70,   # 🐌 оборачиваемость хуже N дней
    "margin_pct": 5,       # 💸 маржа ниже N%
    "drr_pct": 10,         # 🔥 ДРР выше N%
    "no_ads_stock_days": 30,  # 💤 без рекламы при запасе от N дней
    "wb_low_days": 14,     # 📦 на ВБ меньше чем на N дней
    "top_n": 30,           # видимых строк в секции (остальное — свёрнутая группа)
    # Google-таблица (problem_digest_gsheet): id пользовательских таблиц,
    # расшаренных на сервисный аккаунт (у SA нет своей квоты Диска — владеть
    # файлами он не может). share_emails — справочно, кому выдан доступ.
    "sheet_id": "",
    "sheet_id_weekly": "",
    "share_emails": [],
}

INF_DAYS = 900  # compute_days_left отдаёт 999 как «∞» — всё выше порога считаем ∞

# Товарные секции в порядке вывода на листе бренда
PRODUCT_SECTIONS = ("drr", "margin", "turnover", "no_ads", "low_stock", "out_stock")

# Лимит выборки воронки: выручка в сообщении — «за все товары», дефолтные 500
# строк UI могут срезать хвост ассортимента
FUNNEL_LIMIT = 5000


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def sanitize_settings(raw: Any) -> dict[str, Any]:
    """Настройка сводки: дефолты + валидация типов (мусор в JSON не роняет джобу)."""
    cfg = dict(DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return cfg
    cfg["enabled"] = bool(raw.get("enabled", False))
    chat_ids = raw.get("chat_ids")
    if isinstance(chat_ids, list):
        cfg["chat_ids"] = [int(c) for c in chat_ids if isinstance(c, (int, str)) and str(c).lstrip("-").isdigit()]
    exclude = raw.get("exclude_brands")
    if isinstance(exclude, list):
        cfg["exclude_brands"] = [str(b) for b in exclude if isinstance(b, str) and b.strip()]
    for key in ("turnover_days", "margin_pct", "drr_pct", "no_ads_stock_days", "wb_low_days", "top_n"):
        v = raw.get(key)
        if isinstance(v, (int, float)) and 0 <= float(v) <= 10000:
            cfg[key] = float(v) if key in ("margin_pct", "drr_pct") else int(v)
    for key in ("sheet_id", "sheet_id_weekly"):
        v = raw.get(key)
        if isinstance(v, str):
            cfg[key] = v.strip()
    emails = raw.get("share_emails")
    if isinstance(emails, list):
        cfg["share_emails"] = [e.strip() for e in emails if isinstance(e, str) and "@" in e]
    return cfg


async def get_digest_settings(db: AsyncSession, project_id: int) -> dict[str, Any]:
    from backend.services.settings_service import get_setting

    raw = await get_setting(db, project_id, DIGEST_SETTINGS_KEY)
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        data = {}
    return sanitize_settings(data)


async def set_digest_settings(db: AsyncSession, project_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge-обновление настройки (сохраняем только известные ключи)."""
    from backend.services.settings_service import set_setting

    current = await get_digest_settings(db, project_id)
    current.update({k: v for k, v in patch.items() if k in DEFAULT_SETTINGS})
    cfg = sanitize_settings(current)
    await set_setting(db, project_id, DIGEST_SETTINGS_KEY, json.dumps(cfg, ensure_ascii=False))
    return cfg


# ─── классификация ──────────────────────────────────────────────────────────


def enrich_row(r: dict[str, Any], r_cmp: dict[str, Any] | None) -> dict[str, Any]:
    """Строка воронки (sku, extended) → item с производными для классификации.

    r_cmp — та же метрика за сравнительный период («вчера» в ежедневной сводке,
    позапрошлая неделя в недельной); None, если товара там не было.
    """
    stock_qty = _num(r.get("wb_stock_qty")) + _num(r.get("own_stock_qty"))
    wb_qty = _num(r.get("wb_stock_qty"))
    frozen = _num(r.get("wb_stock_cost")) + _num(r.get("own_stock_cost"))
    turnover = r.get("stock_days_left")
    wb_days = r.get("wb_stock_days_left")
    drr_inf = _num(r.get("adv_sum")) > 0 and _num(r.get("orders_sum_rub")) == 0
    return {
        "r": r,
        "cmp": r_cmp or {},
        "stock_qty": stock_qty,
        "wb_qty": wb_qty,
        "frozen": frozen,
        "turnover": None if turnover is None else _num(turnover),
        "wb_days": None if wb_days is None else _num(wb_days),
        "drr_inf": drr_inf,
        "drr": None if r.get("drr") is None else _num(r.get("drr")),
    }


def pick_sections(items: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Товары по секциям, каждая отсортирована от худшего к лучшему."""
    drr = sorted(
        [i for i in items if i["drr_inf"] or (i["drr"] is not None and _num(i["r"].get("adv_sum")) > 0 and i["drr"] > cfg["drr_pct"])],
        key=lambda i: (not i["drr_inf"], -(i["drr"] or 0)),
    )
    margin = sorted(
        [i for i in items if _num(i["r"].get("orders_sum_rub")) > 0 and _num(i["r"].get("margin")) < cfg["margin_pct"]],
        key=lambda i: _num(i["r"].get("margin")),
    )
    turnover = sorted(
        [i for i in items if i["turnover"] is not None and i["stock_qty"] > 0 and i["turnover"] > cfg["turnover_days"]],
        key=lambda i: -i["frozen"],
    )
    no_ads = sorted(
        [
            i for i in items
            if _num(i["r"].get("adv_sum")) == 0 and i["stock_qty"] > 0
            and i["turnover"] is not None and i["turnover"] > cfg["no_ads_stock_days"]
        ],
        key=lambda i: -i["frozen"],
    )
    low_stock = sorted(
        [i for i in items if i["wb_qty"] > 0 and i["wb_days"] is not None and i["wb_days"] < cfg["wb_low_days"]],
        key=lambda i: i["wb_days"],
    )
    out_stock = sorted(
        [i for i in items if i["wb_qty"] == 0],
        key=lambda i: -_num(i["r"].get("orders_sum_rub")),
    )
    return {
        "drr": drr,
        "margin": margin,
        "turnover": turnover,
        "no_ads": no_ads,
        "low_stock": low_stock,
        "out_stock": out_stock,
    }


# ─── сбор данных ────────────────────────────────────────────────────────────


async def _load_tax_info(db: AsyncSession, project: Project) -> dict[str, Any]:
    """Налоги для расчёта маржи (копия фолбэка из routers/funnel — сервису роутер не импортнуть)."""
    from backend.services.bdr_loaders import load_tax_settings

    today = date.today()
    tax_info = await load_tax_settings(db, project.id, today, today)
    if tax_info.get("usn_rate", 0) == 0 and tax_info.get("nds_rate", 0) == 0:
        tax_info = {
            "tax_regime": "usn_income",
            "usn_rate": float(project.tax_rate or 6),
            "nds_rate": 0,
            "cost_as_expense": False,
        }
    return tax_info


async def collect_digest(
    db: AsyncSession,
    project: Project,
    cfg: dict[str, Any],
    date_from: str,
    date_to: str,
    cmp_from: str,
    cmp_to: str,
) -> dict[str, Any]:
    """Собрать данные сводки: секции по брендам + кампании с нехваткой бюджета.

    Основной период [date_from..date_to] — по нему считаются метрики и флаги;
    сравнительный [cmp_from..cmp_to] идёт второй колонкой (вчера / пред. неделя).
    """
    from backend.services import funnel as funnel_service
    from backend.services.funnel.ads_manager import get_budget_gaps
    from backend.services.funnel.bdr_rates import get_bdr_rates

    tax_info = await _load_tax_info(db, project)
    bdr_rates_map = await get_bdr_rates(db, project.id)

    rows_main = await funnel_service.get_funnel_by_sku(
        db, project.id, tax_info, date_from, date_to, None, None,
        bdr_rates_map=bdr_rates_map, limit=FUNNEL_LIMIT,
    )
    stock_map = await funnel_service.get_stock_cost_map(db, project.id)
    funnel_service.merge_stock_costs(rows_main, stock_map, "sku")

    rows_cmp = await funnel_service.get_funnel_by_sku(
        db, project.id, tax_info, cmp_from, cmp_to, None, None,
        bdr_rates_map=bdr_rates_map, limit=FUNNEL_LIMIT,
    )
    cmp_by_nm = {r["nm_id"]: r for r in rows_cmp}

    excluded = set(cfg["exclude_brands"])
    items_by_brand: dict[str, list[dict[str, Any]]] = {}
    for r in rows_main:
        brand = r.get("brand") or "Без бренда"
        if brand in excluded:
            continue
        items_by_brand.setdefault(brand, []).append(enrich_row(r, cmp_by_nm.get(r["nm_id"])))

    sections_by_brand = {b: pick_sections(items, cfg) for b, items in sorted(items_by_brand.items())}

    gap_by_brand: dict[str, list[dict[str, Any]]] = {}
    try:
        gaps = await get_budget_gaps(db, project.id)
    except Exception:
        logger.exception("problem digest: budget gaps failed (project=%s)", project.id)
        gaps = []
    for g in gaps:
        for brand in g.get("brands") or ["Без бренда"]:
            if brand not in excluded:
                gap_by_brand.setdefault(brand, []).append(g)

    return {
        "sections_by_brand": sections_by_brand,
        "gap_by_brand": gap_by_brand,
        "cmp_rows_by_brand": _cmp_totals_by_brand(rows_cmp, excluded),
        "main_rows_by_brand": _cmp_totals_by_brand(rows_main, excluded),
        "revenue_cmp": revenue_by_brand(rows_cmp, excluded),
        "revenue_main": revenue_by_brand(rows_main, excluded),
    }


def revenue_by_brand(rows: list[dict[str, Any]], excluded: set[str]) -> dict[str, dict[str, Any]]:
    """Выручка (сумма заказов ₽) по брендам с разбивкой по категориям — для ТГ-текста."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        brand = r.get("brand") or "Без бренда"
        if brand in excluded:
            continue
        entry = out.setdefault(brand, {"total": 0.0, "subjects": {}})
        s = _num(r.get("orders_sum_rub"))
        entry["total"] += s
        subject = r.get("subject") or "Без категории"
        entry["subjects"][subject] = entry["subjects"].get(subject, 0.0) + s
    return out


def _cmp_totals_by_brand(rows: list[dict[str, Any]], excluded: set[str]) -> dict[str, dict[str, float]]:
    """Суммарные метрики бренда за период — для листа «Динамика» недельной сводки."""
    by_brand: dict[str, dict[str, float]] = {}
    for r in rows:
        brand = r.get("brand") or "Без бренда"
        if brand in excluded:
            continue
        t = by_brand.setdefault(brand, {"orders_sum_rub": 0, "adv_sum": 0, "profit": 0, "revenue": 0})
        t["orders_sum_rub"] += _num(r.get("orders_sum_rub"))
        t["adv_sum"] += _num(r.get("adv_sum"))
        t["profit"] += _num(r.get("profit"))
        t["revenue"] += _num(r.get("revenue"))
    for t in by_brand.values():
        t["drr"] = round(t["adv_sum"] / t["orders_sum_rub"] * 100, 2) if t["orders_sum_rub"] else 0
        t["margin"] = round(t["profit"] / t["revenue"] * 100, 2) if t["revenue"] else 0
    return by_brand


# ─── текст для Telegram ─────────────────────────────────────────────────────


def rub_short(v: float) -> str:
    if v >= 1_000_000:
        return f"{v / 1_000_000:.1f} млн ₽".replace(".", ",")
    if v >= 10_000:
        return f"{round(v / 1000)}к ₽"
    return f"{round(v):,} ₽".replace(",", " ")


def fmt_days(v: float | None) -> str:
    if v is None:
        return "—"
    return "∞" if v >= INF_DAYS else str(round(v))


def build_tg_text(
    title: str,
    sheet_url: str | None = None,
    revenue: dict[str, dict[str, Any]] | None = None,
    revenue_label: str | None = None,
) -> str:
    """Сообщение: общая выручка по всем товарам (итог → бренды → категории).

    Проблемные товары в тексте не перечисляем — они в приложенном xlsx
    и Google-таблице (решение юзера 23.07).
    """
    from html import escape

    revenue = revenue or {}
    total = sum(d["total"] for d in revenue.values())
    lines = [f"📊 <b>{escape(title)}</b>", ""]
    lines.append(f"💰 <b>Выручка {escape(revenue_label or '')} — {rub_short(total)}</b>")
    for brand, data in sorted(revenue.items(), key=lambda kv: -kv[1]["total"]):
        if data["total"] <= 0:
            continue
        # Бренды/категории приходят из WB-карточек — сырые & и < ломают parse_mode=HTML
        lines.append(f"<b>{escape(brand)}</b> — {rub_short(data['total'])}")
        subjects = sorted(((k, v) for k, v in data["subjects"].items() if v > 0), key=lambda kv: -kv[1])
        for subject, s in subjects[:5]:
            lines.append(f"   • {escape(subject)} — {rub_short(s)}")
        rest = subjects[5:]
        if rest:
            lines.append(f"   • прочие ({len(rest)}) — {rub_short(sum(v for _, v in rest))}")
    lines.append("")
    lines.append("⚠️ Проблемные товары — в файле" + (f" и <a href=\"{sheet_url}\">таблице</a>" if sheet_url else ""))
    return "\n".join(lines)


# ─── сборка готовой сводки (текст + файл) ───────────────────────────────────


def _fmt_period(d_from: date, d_to: date) -> str:
    if d_from == d_to:
        return d_from.strftime("%d.%m")
    return f"{d_from.strftime('%d.%m')}–{d_to.strftime('%d.%m')}"


async def build_daily_digest(db: AsyncSession, project: Project, cfg: dict[str, Any], now_msk: datetime) -> dict[str, Any]:
    """Ежедневная сводка: метрики за 7 дней (по вчера), сравнение — вчера."""
    yday = now_msk.date() - timedelta(days=1)
    week_from = yday - timedelta(days=6)
    digest = await collect_digest(
        db, project, cfg,
        week_from.isoformat(), yday.isoformat(),
        yday.isoformat(), yday.isoformat(),
    )
    from backend.services.funnel.problem_digest_xlsx import render_workbook

    title = f"Сводка · {now_msk.strftime('%d.%m.%Y')}"
    meta: dict[str, Any] = {
        "cfg": cfg,
        "title": f"Проблемные товары · {now_msk.strftime('%d.%m.%Y')}",
        "period_label": f"неделя {_fmt_period(week_from, yday)}",
        "cmp_label": f"вчера {yday.strftime('%d.%m')}",
        "cmp_short": "вчера",
        "with_dynamics": False,
    }
    revenue = digest["revenue_cmp"]  # выручка за вчера — свежий день
    revenue_label = f"за {yday.strftime('%d.%m')}"
    sheet = _cfg_sheet_url(cfg, "sheet_id")
    return {
        "text": build_tg_text(title, sheet_url=sheet, revenue=revenue, revenue_label=revenue_label),
        "title": title,
        "kind": "daily",
        "revenue": revenue,
        "revenue_label": revenue_label,
        "filename": f"problem_digest_{now_msk.date().isoformat()}.xlsx",
        "xlsx": render_workbook(digest, meta),
        "digest": digest,
    }


async def build_weekly_digest(db: AsyncSession, project: Project, cfg: dict[str, Any], now_msk: datetime) -> dict[str, Any]:
    """Недельная сводка (понедельник): прошлая неделя пн–вс против позапрошлой."""
    last_sunday = now_msk.date() - timedelta(days=now_msk.weekday() + 1)
    last_monday = last_sunday - timedelta(days=6)
    prev_sunday = last_monday - timedelta(days=1)
    prev_monday = prev_sunday - timedelta(days=6)
    digest = await collect_digest(
        db, project, cfg,
        last_monday.isoformat(), last_sunday.isoformat(),
        prev_monday.isoformat(), prev_sunday.isoformat(),
    )
    from backend.services.funnel.problem_digest_xlsx import render_workbook

    title = f"Итоги недели {_fmt_period(last_monday, last_sunday)}"
    meta: dict[str, Any] = {
        "cfg": cfg,
        "title": f"Проблемные товары · {title.lower()}",
        "period_label": f"неделя {_fmt_period(last_monday, last_sunday)}",
        "cmp_label": f"пред. неделя {_fmt_period(prev_monday, prev_sunday)}",
        "cmp_short": "пред. нед",
        "with_dynamics": True,
    }
    revenue = digest["revenue_main"]  # выручка за отчётную неделю
    revenue_label = f"за неделю {_fmt_period(last_monday, last_sunday)}"
    sheet = _cfg_sheet_url(cfg, "sheet_id_weekly")
    return {
        "text": build_tg_text(title, sheet_url=sheet, revenue=revenue, revenue_label=revenue_label),
        "title": title,
        "kind": "weekly",
        "revenue": revenue,
        "revenue_label": revenue_label,
        "filename": f"problem_digest_week_{last_monday.isoformat()}.xlsx",
        "xlsx": render_workbook(digest, meta),
        "digest": digest,
    }


# ─── «Прислать сейчас» через worker ─────────────────────────────────────────
# Из API-контейнера прода api.telegram.org недоступен (РКН; прокси-путь живёт
# в worker) — send-now ставит маркер, worker-тик подхватывает его в течение минуты.

ASAP_KEY = "ads_problem_digest_asap"


async def request_asap_send(db: AsyncSession, project_id: int, kind: str) -> None:
    from backend.services.settings_service import set_setting

    await set_setting(db, project_id, ASAP_KEY, json.dumps({"kind": kind}))


async def pop_asap_request(db: AsyncSession, project_id: int) -> str | None:
    """Прочитать и снять маркер «прислать сейчас». Возвращает kind или None."""
    from backend.services.settings_service import get_setting, set_setting

    raw = await get_setting(db, project_id, ASAP_KEY)
    if not raw:
        return None
    try:
        kind = json.loads(raw).get("kind")
    except (TypeError, ValueError, AttributeError):
        kind = None
    await set_setting(db, project_id, ASAP_KEY, "")
    return kind if kind in ("daily", "weekly") else None


def _cfg_sheet_url(cfg: dict[str, Any], key: str) -> str | None:
    """Постоянная ссылка на настроенную Google-таблицу (кладём в текст всегда,
    даже если заливка недоступна — сама таблица живёт по стабильному URL)."""
    from backend.services.funnel.problem_digest_gsheet import sheet_url

    sid = str(cfg.get(key) or "")
    return sheet_url(sid) if sid else None


async def attach_sheet_link(db: AsyncSession, project_id: int, cfg: dict[str, Any], payload: dict[str, Any]) -> None:
    """Залить свежий xlsx в Google-таблицу сводки (best-effort).

    Ссылка на таблицу уже в тексте (build_*_digest); здесь только обновляем
    содержимое — без ключа сервисного аккаунта / при ошибке Google тихо скипаем.
    """
    from backend.services.funnel.problem_digest_gsheet import update_digest_sheet

    url = await update_digest_sheet(db, project_id, cfg, payload["kind"], payload["xlsx"])
    if url:
        payload["sheet_url"] = url
