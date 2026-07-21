# ruff: noqa: RUF001, RUF002, RUF003
"""
Почасовая история наполнения черновиков сборки по категориям.

Черновик хранит только ТЕКУЩЕЕ состояние distribution — по нему нельзя понять,
разбирают ли менеджеры предложенное («Срочно к отправке» превращается в заявки)
или товар неделями висит в черновике. Эта служба копит динамику вперёд:
раз в час срез Σ по всем не-удалённым черновикам проекта (основной +
категорийные) × категория: позиции, штуки (rows и prebook раздельно), короба,
паллетный футпринт.

Категория SKU = CategoryOverride ?? Nomenclature.subject ?? «Без категории»
(зеркало categoryOf на фронте). Кратность и габарит короба — из «машины
кратности» (_resolve_machine_box_qty), коробов-на-паллету — по геометрии с
override'ами настроек; лимит высоты — дефолтный (180 см), без привязки к
целевому складу: это аналитическая оценка, а не отгрузочный документ.

handed_units в срез НЕ входят: переданное на ФФ — уже движение, а не «висит».
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import AssemblyDraft, AssemblyDraftCategoryHourly
from backend.models.cost import Nomenclature
from backend.models.refs import CategoryOverride
from backend.services.assembly.pallet_geo import (
    DEFAULT_MAX_PALLET_HEIGHT_CM,
    effective_boxes_per_pallet,
)
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)

FALLBACK_CATEGORY = "Без категории"
# = максимум окна просмотра (роутер: days ≤ 60) — храним ровно то, что достижимо.
RETENTION_DAYS = 60
# 60 дней × 24 часа × ~40 категорий — с запасом выше любого реального объёма.
_HISTORY_ROWS_CAP = 60_000


@dataclass
class _CatAcc:
    nm_ids: set[int] = field(default_factory=set)
    units_rows: int = 0
    units_prebook: int = 0
    boxes: int = 0
    mono_pallets: int = 0
    box_footprint: Decimal = Decimal("0")

    @property
    def pallets(self) -> Decimal:
        # MONOPALLET — целые паллеты (SKU не смешиваются), BOX — дробный след
        # общей паллеты. Дробь сохраняем: тренд «разбирают/висит» важнее округления.
        return (Decimal(self.mono_pallets) + self.box_footprint).quantize(Decimal("0.1"))


def _iter_qty(src: object) -> list[tuple[int, int]]:
    """(ff_warehouse_id, qty>0) из сырого src-словаря JSONB (мусор пропускаем)."""
    out: list[tuple[int, int]] = []
    if not isinstance(src, dict):
        return out
    for wid, qty in src.items():
        if isinstance(qty, bool) or not isinstance(qty, (int, float, str)):
            continue
        try:
            w, q = int(wid), int(qty)
        except (TypeError, ValueError):
            continue
        if q > 0:
            out.append((w, q))
    return out


def compute_category_stats(
    distributions: list[dict],
    category_by_nm: dict[int, str],
    machine: dict[tuple[str, int], dict],
    pallet_overrides: dict[str, int] | None,
) -> dict[str, _CatAcc]:
    """Агрегация rows+prebook черновиков в срез по категориям (чистая функция).

    machine: (barcode, ff_warehouse_id) → {box_qty, box_size} — пары без резолва
    отсутствуют: их штуки считаются, но короба/паллеты по ним не оцениваются.
    """
    stats: dict[str, _CatAcc] = {}
    for dist in distributions:
        if not isinstance(dist, dict):
            continue
        for bucket in ("rows", "prebook"):
            for row in dist.get(bucket) or []:
                if not isinstance(row, dict):
                    continue
                try:
                    nm_id = int(row.get("nm_id") or 0)
                except (TypeError, ValueError):
                    continue
                barcode = str(row.get("barcode") or "")
                pkg = str(row.get("package_type") or "BOX")
                pairs = _iter_qty(row.get("src"))
                qty_total = sum(q for _, q in pairs)
                if qty_total <= 0:
                    continue
                cat = category_by_nm.get(nm_id) or FALLBACK_CATEGORY
                acc = stats.setdefault(cat, _CatAcc())
                if nm_id:
                    acc.nm_ids.add(nm_id)
                if bucket == "rows":
                    acc.units_rows += qty_total
                else:
                    acc.units_prebook += qty_total

                row_mono_frac = Decimal("0")
                for wid, q in pairs:
                    entry = machine.get((barcode, wid))
                    bq = (entry or {}).get("box_qty")
                    if not bq or bq <= 0:
                        continue
                    acc.boxes += math.ceil(q / bq)
                    bpp = effective_boxes_per_pallet(
                        (entry or {}).get("box_size"), DEFAULT_MAX_PALLET_HEIGHT_CM, pallet_overrides
                    )
                    if not bpp or bpp <= 0:
                        continue
                    frac = Decimal(q) / Decimal(bpp * bq)
                    if pkg == "MONOPALLET":
                        row_mono_frac += frac
                    else:
                        acc.box_footprint += frac
                if row_mono_frac > 0:
                    acc.mono_pallets += int(math.ceil(row_mono_frac))
    return stats


async def _category_by_nm(db: AsyncSession, project_id: int, nm_ids: set[int]) -> dict[int, str]:
    """Эффективная категория per nm: CategoryOverride ?? Nomenclature.subject."""
    if not nm_ids:
        return {}
    result: dict[int, str] = {}
    noms = await db.execute(
        select(Nomenclature.article_wb, Nomenclature.subject)
        .where(
            Nomenclature.project_id == project_id,
            Nomenclature.article_wb.in_(nm_ids),
        )
        .limit(len(nm_ids) * 4)  # дубли barcode-строк одного nm
    )
    for nm, subject in noms.all():
        if nm and subject and nm not in result:
            result[int(nm)] = str(subject)
    overrides = await db.execute(
        select(CategoryOverride.nm_id, CategoryOverride.category_value)
        .where(
            CategoryOverride.project_id == project_id,
            CategoryOverride.nm_id.in_(nm_ids),
        )
        .limit(len(nm_ids))
    )
    for nm, value in overrides.all():
        if nm and value:
            result[int(nm)] = str(value)  # override перебивает subject
    return result


async def snapshot_project(
    db: AsyncSession,
    project_id: int,
    taken_at: datetime | None = None,
) -> int:
    """Срез черновиков проекта на начало часа (idempotent: час перезаписывается).

    Возвращает число записанных строк-категорий. Коммитит сам.
    """
    from backend.services.box_multiplicity_service import _resolve_machine_box_qty
    from backend.services.settings_service import get_pallet_boxes_by_size

    hour = (taken_at or utcnow()).replace(minute=0, second=0, microsecond=0)

    drafts = await db.execute(
        select(AssemblyDraft.distribution)
        .where(
            AssemblyDraft.project_id == project_id,
            AssemblyDraft.is_deleted == False,  # noqa: E712 — SQLAlchemy expression
        )
        .limit(500)
    )
    distributions = [d for (d,) in drafts.all() if isinstance(d, dict)]

    nm_ids: set[int] = set()
    barcodes: set[str] = set()
    for dist in distributions:
        for bucket in ("rows", "prebook"):
            for row in dist.get(bucket) or []:
                if not isinstance(row, dict):
                    continue
                try:
                    nm = int(row.get("nm_id") or 0)
                except (TypeError, ValueError):
                    nm = 0
                if nm:
                    nm_ids.add(nm)
                bc = str(row.get("barcode") or "")
                if bc:
                    barcodes.add(bc)

    category_by_nm = await _category_by_nm(db, project_id, nm_ids)
    machine = await _resolve_machine_box_qty(db, project_id, sorted(barcodes)) if barcodes else {}
    pallet_overrides = await get_pallet_boxes_by_size(db, project_id)

    stats = compute_category_stats(distributions, category_by_nm, machine, pallet_overrides)

    # Idempotent-перезапись часа БЕЗ delete+insert: почасовая джоба и ручной
    # «Снять срез сейчас» могут наложиться — две транзакции delete+insert падали
    # бы на uq_asm_draft_cat_hourly (ревью MEDIUM). Upsert по уникальному ключу
    # конкуренцию переживает; категории, исчезнувшие с прошлого прогона часа,
    # подчищаются отдельным DELETE (текущий срез = состояние, не сумма прогонов).
    cat_names = [cat[:100] for cat in stats]
    stale = delete(AssemblyDraftCategoryHourly).where(
        AssemblyDraftCategoryHourly.project_id == project_id,
        AssemblyDraftCategoryHourly.taken_at == hour,
    )
    if cat_names:
        stale = stale.where(AssemblyDraftCategoryHourly.category.not_in(cat_names))
    await db.execute(stale)
    for cat, acc in stats.items():
        values = {
            "project_id": project_id,
            "taken_at": hour,
            "category": cat[:100],
            "positions": len(acc.nm_ids),
            "units_rows": acc.units_rows,
            "units_prebook": acc.units_prebook,
            "boxes": acc.boxes,
            "pallets": acc.pallets,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        }
        await db.execute(
            pg_insert(AssemblyDraftCategoryHourly)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_asm_draft_cat_hourly",
                set_={k: values[k] for k in ("positions", "units_rows", "units_prebook", "boxes", "pallets", "updated_at")},
            )
        )
    await db.commit()
    return len(stats)


async def purge_old_snapshots(db: AsyncSession, retention_days: int = RETENTION_DAYS) -> None:
    """Ретенция: срезы старше retention_days удаляются (все проекты — служебный
    вызов из scheduler-джобы, таблица чисто аналитическая)."""
    cutoff = utcnow() - timedelta(days=retention_days)
    await db.execute(
        delete(AssemblyDraftCategoryHourly).where(AssemblyDraftCategoryHourly.taken_at < cutoff)
    )
    await db.commit()


async def get_history(
    db: AsyncSession,
    project_id: int,
    days: int,
) -> list[AssemblyDraftCategoryHourly]:
    """Точки истории проекта за N дней, старые → новые (категории внутри часа по имени)."""
    since = utcnow() - timedelta(days=days)
    result = await db.execute(
        select(AssemblyDraftCategoryHourly)
        .where(
            AssemblyDraftCategoryHourly.project_id == project_id,
            AssemblyDraftCategoryHourly.taken_at >= since,
        )
        .order_by(AssemblyDraftCategoryHourly.taken_at.asc(), AssemblyDraftCategoryHourly.category.asc())
        .limit(_HISTORY_ROWS_CAP)
    )
    return list(result.scalars().all())
