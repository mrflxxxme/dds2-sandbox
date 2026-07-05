# ruff: noqa: RUF002, RUF003
"""Settings service — project-level key-value settings."""

import json
import logging
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.refs import ProjectSetting
from backend.services.warehouse_geo import WAREHOUSE_COORDS

logger = logging.getLogger("dds.settings")


async def get_setting(db: AsyncSession, project_id: int, key: str) -> str | None:
    """Get a raw setting value by key."""
    result = await db.execute(
        select(ProjectSetting).where(ProjectSetting.project_id == project_id, ProjectSetting.key == key)
    )
    row = result.scalar_one_or_none()
    return row.value if row else None


async def set_setting(db: AsyncSession, project_id: int, key: str, value: str) -> None:
    """Upsert a setting value."""
    result = await db.execute(
        select(ProjectSetting).where(ProjectSetting.project_id == project_id, ProjectSetting.key == key)
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(ProjectSetting(project_id=project_id, key=key, value=value))
    await db.commit()


async def get_excluded_warehouses(db: AsyncSession, project_id: int) -> list[str]:
    """Get list of excluded warehouse names for a project."""
    raw = await get_setting(db, project_id, "excluded_warehouses")
    if not raw:
        return []
    try:
        parsed: list[str] = json.loads(raw)
        return parsed
    except (json.JSONDecodeError, TypeError):
        return []


async def set_excluded_warehouses(db: AsyncSession, project_id: int, warehouses: list[str]) -> list[str]:
    """Set excluded warehouses. Normalizes names (strips parenthesized suffix)."""
    import re

    from backend.cache import invalidate_cache

    parens_re = re.compile(r"\s*\([^)]*\)\s*$")
    valid = sorted({parens_re.sub("", w).strip() for w in warehouses if w and w.strip()})
    await set_setting(db, project_id, "excluded_warehouses", json.dumps(valid, ensure_ascii=False))
    # Iron rule #7: после мутации настроек, влияющих на отчёт, инвалидируем кэш.
    # Без этого 5-минутный TTL отдавал бы старый снимок с уже исключёнными
    # складами (прецедент: пользователь убрал Обухово в excluded, но оно
    # оставалось в матрице потребности до истечения кэша).
    try:
        await invalidate_cache("reports:warehouse_need")
    except Exception as e:
        logger.warning("invalidate reports:warehouse_need failed: %s", e)
    logger.info("Set excluded warehouses for project %s: %s", project_id, valid)
    return valid


async def get_preorder_allowed_warehouses(db: AsyncSession, project_id: int) -> list[str]:
    """Warehouses where a preorder (предзаявка) is allowed even without a WB
    acceptance limit. Whitelist: складам ВНЕ списка предзаявка по «нет лимита»
    запрещена → они вырезаются из расчёта (см. `get_acceptance_blocked_warehouses`)."""
    raw = await get_setting(db, project_id, "preorder_allowed_warehouses")
    if not raw:
        return []
    try:
        parsed: list[str] = json.loads(raw)
        return parsed
    except (json.JSONDecodeError, TypeError):
        return []


async def set_preorder_allowed_warehouses(db: AsyncSession, project_id: int, warehouses: list[str]) -> list[str]:
    """Set preorder-allowed warehouses. Normalizes names (strips parenthesized suffix)."""
    import re

    from backend.cache import invalidate_cache

    parens_re = re.compile(r"\s*\([^)]*\)\s*$")
    valid = sorted({parens_re.sub("", w).strip() for w in warehouses if w and w.strip()})
    await set_setting(db, project_id, "preorder_allowed_warehouses", json.dumps(valid, ensure_ascii=False))
    # Iron rule #7: список влияет на отсев складов в матрице потребности.
    try:
        await invalidate_cache("reports:warehouse_need")
    except Exception as e:
        logger.warning("invalidate reports:warehouse_need failed: %s", e)
    logger.info("Set preorder-allowed warehouses for project %s: %s", project_id, valid)
    return valid


_BOX_SIZE_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _canon_box_size(s: str) -> str:
    """Канон ключа размера коробки по ЧИСЛАМ (первые 3) — устойчив к разделителям
    и опечаткам: «60×40×50», «60x40x50», «60*40*40», «60x40xc40» (лишняя буква) →
    «60x40x40»; дробное «9.0» → «9». Зеркало фронтового canonBoxSize."""
    nums = _BOX_SIZE_NUM_RE.findall(s or "")
    if len(nums) >= 3:
        def _n(x: str) -> str:
            f = float(x)
            return str(int(f)) if f == int(f) else str(f)

        return "x".join(_n(n) for n in nums[:3])
    return (s or "").strip().lower().replace(" ", "")


async def get_pallet_boxes_by_size(db: AsyncSession, project_id: int) -> dict[str, int]:
    """Ручной override «коробок на паллету» по размеру коробки (canonical → int).
    Пусто = используется геометрия (boxesPerPallet)."""
    raw = await get_setting(db, project_id, "pallet_boxes_by_size")
    if not raw:
        return {}
    try:
        parsed: dict = json.loads(raw)
        return {str(k): int(v) for k, v in parsed.items() if int(v) > 0}
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return {}


async def set_pallet_boxes_by_size(db: AsyncSession, project_id: int, mapping: dict[str, int]) -> dict[str, int]:
    """Сохранить override «коробок на паллету» по размеру. Ключи канонизируются,
    значения ≤0 / нечисловые отбрасываются."""
    from backend.cache import invalidate_cache

    clean: dict[str, int] = {}
    for k, v in mapping.items():
        key = _canon_box_size(str(k))
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if key and n > 0:
            clean[key] = n
    await set_setting(db, project_id, "pallet_boxes_by_size", json.dumps(clean, ensure_ascii=False))
    # Влияет на паллетную консолидацию в матрице потребности.
    try:
        await invalidate_cache("reports:warehouse_need")
    except Exception as e:
        logger.warning("invalidate reports:warehouse_need failed: %s", e)
    logger.info("Set pallet_boxes_by_size for project %s: %d sizes", project_id, len(clean))
    return clean


async def get_all_warehouses(db: AsyncSession | None = None, project_id: int | None = None) -> list[dict]:
    """Return all known WB warehouses: union of WAREHOUSE_COORDS keys and any
    warehouse names that appear in wb_warehouse_stocks for this project.

    Names are normalized (parenthesized suffix stripped). For warehouses without
    coordinates, lat/lng are 0 (UI may show them but not on the map).
    """
    import re

    parens_re = re.compile(r"\s*\([^)]*\)\s*$")

    def norm(name: str) -> str:
        return parens_re.sub("", name or "").strip()

    by_name: dict[str, dict] = {}
    for raw_name, (lat, lng) in WAREHOUSE_COORDS.items():
        n = norm(raw_name)
        if n and n not in by_name:
            by_name[n] = {"name": n, "lat": lat, "lng": lng, "is_sorting_center": n.startswith("СЦ ")}

    if db is not None and project_id is not None:
        from backend.models import WbWarehouseStock

        result = await db.execute(
            select(WbWarehouseStock.warehouse_name).where(WbWarehouseStock.project_id == project_id).distinct()
        )
        for (raw,) in result:
            n = norm(raw)
            if not n or n in by_name:
                continue
            by_name[n] = {"name": n, "lat": 0.0, "lng": 0.0, "is_sorting_center": n.startswith("СЦ ")}

    return sorted(by_name.values(), key=lambda x: x["name"])


# ─── Stock forecast: default RF→WB lead time (used when WarehouseDeliveryTime is empty)


_FORECAST_RF_DEFAULT_DAYS_KEY = "forecast_rf_default_days"
_FORECAST_RF_DEFAULT_DAYS_FALLBACK = 8  # 3 (assembly) + 3 (delivery) + 2 (acceptance)


async def get_forecast_rf_default_days(db: AsyncSession, project_id: int) -> int:
    raw = await get_setting(db, project_id, _FORECAST_RF_DEFAULT_DAYS_KEY)
    if raw is None:
        return _FORECAST_RF_DEFAULT_DAYS_FALLBACK
    try:
        v = int(raw)
        return v if v >= 0 else _FORECAST_RF_DEFAULT_DAYS_FALLBACK
    except (TypeError, ValueError):
        return _FORECAST_RF_DEFAULT_DAYS_FALLBACK


async def set_forecast_rf_default_days(db: AsyncSession, project_id: int, days: int) -> int:
    days = max(0, min(int(days), 365))
    await set_setting(db, project_id, _FORECAST_RF_DEFAULT_DAYS_KEY, str(days))
    logger.info("Set forecast_rf_default_days for project %s: %d", project_id, days)
    return days


# ─── Cost valuation method (lifetime_avg | fifo | moving_avg) ────────────────


_VALUATION_METHOD_KEY = "valuation_method"
_VALUATION_METHODS = ("lifetime_avg", "fifo", "moving_avg")
_VALUATION_METHOD_DEFAULT = "lifetime_avg"


async def get_valuation_method(db: AsyncSession, project_id: int) -> str:
    """Project COGS valuation method. Defaults to lifetime_avg (current behaviour)."""
    raw = await get_setting(db, project_id, _VALUATION_METHOD_KEY)
    return raw if raw in _VALUATION_METHODS else _VALUATION_METHOD_DEFAULT


async def set_valuation_method(db: AsyncSession, project_id: int, method: str) -> str:
    if method not in _VALUATION_METHODS:
        raise ValueError(f"invalid valuation method: {method}")
    await set_setting(db, project_id, _VALUATION_METHOD_KEY, method)
    logger.info("Set valuation_method for project %s: %s", project_id, method)
    return method


# ─── Cost valuation data-start cutoff (incomplete early-period guard) ─────────


_VALUATION_START_KEY = "valuation_start_date"


async def get_valuation_start_date(db: AsyncSession, project_id: int) -> date | None:
    """Sales before this date are ignored by the valuation engine (incomplete
    early-period data, e.g. Dec 2025). None = use all history."""
    raw = await get_setting(db, project_id, _VALUATION_START_KEY)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


async def set_valuation_start_date(db: AsyncSession, project_id: int, d: date | None) -> None:
    await set_setting(db, project_id, _VALUATION_START_KEY, d.isoformat() if d else "")
    logger.info("Set valuation_start_date for project %s: %s", project_id, d)


# ─── Вес пустой коробки (тара картона) — для расчётного веса отгрузки сборки ──
#
# Одно число на проект. Итоговый вес заявки = нетто товаров (Σ шт × вес/шт) +
# `box_weight_kg` × число коробов. Вес паллеты (деревянной тары) НЕ учитываем —
# решение пользователя. None/пусто = коробочную тару не прибавляем.


_BOX_WEIGHT_KG_KEY = "box_weight_kg"


async def get_box_weight_kg(db: AsyncSession, project_id: int) -> Decimal | None:
    """Вес пустой коробки (кг) для проекта. None — не задан (тара не прибавляется)."""
    raw = await get_setting(db, project_id, _BOX_WEIGHT_KG_KEY)
    if not raw:
        return None
    try:
        v = Decimal(raw)
    except (InvalidOperation, TypeError):
        return None
    return v if v >= 0 else None


async def set_box_weight_kg(db: AsyncSession, project_id: int, weight_kg: Decimal | float | str) -> Decimal:
    """Сохранить вес пустой коробки. Отрицательное → 0; точность до грамма."""
    from backend.cache import invalidate_cache

    try:
        w = Decimal(str(weight_kg))
    except (InvalidOperation, TypeError):
        w = Decimal("0")
    if w < 0:
        w = Decimal("0")
    w = w.quantize(Decimal("0.001"))
    await set_setting(db, project_id, _BOX_WEIGHT_KG_KEY, str(w))
    # Вес коробки входит в расчётный вес отгрузки (аналитика логиста по весу). Iron rule 7.
    try:
        await invalidate_cache("reports:logistics_analytics_v2")
    except Exception as e:
        logger.warning("invalidate reports:logistics_analytics_v2 failed: %s", e)
    logger.info("Set box_weight_kg for project %s: %s", project_id, w)
    return w
