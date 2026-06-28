"""
Refs service — CRUD operations for reference data.

Extracted from routers/refs.py to enable reuse and testing.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_project_reports
from backend.models import (
    Account,
    CategoryRef,
    CounterpartyCategory,
    OpeningBalance,
    Override,
)

logger = logging.getLogger(__name__)


# ─── Accounts ────────────────────────────────────────────────────────────────


async def list_accounts(db: AsyncSession, project_id: int) -> list:
    """List all accounts for a project."""
    result = await db.execute(select(Account).where(Account.project_id == project_id, Account.is_deleted == False))
    return result.scalars().all()


async def upsert_account(db: AsyncSession, project_id: int, payload: dict) -> Account:
    """Create or update an account."""
    if payload.get("id"):
        result = await db.execute(
            select(Account).where(
                Account.id == payload["id"],
                Account.project_id == project_id,
                Account.is_deleted == False,
            )
        )
        acc = result.scalar_one_or_none()
        if acc:
            for k, v in payload.items():
                if k != "id":
                    setattr(acc, k, v)
            await db.commit()
            await db.refresh(acc)
            return acc

    acc = Account(**payload, project_id=project_id)
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    await invalidate_project_reports(project_id)
    return acc


async def delete_account(db: AsyncSession, project_id: int, account_id: int) -> bool:
    """Delete an account. Returns True if deleted."""
    result = await db.execute(
        select(Account).where(
            Account.id == account_id,
            Account.project_id == project_id,
            Account.is_deleted == False,
        )
    )
    acc = result.scalar_one_or_none()
    if not acc:
        return False
    acc.soft_delete()
    await db.commit()
    await invalidate_project_reports(project_id)
    return True


# ─── Counterparty Categories ────────────────────────────────────────────────


async def list_cp_categories(db: AsyncSession, project_id: int) -> list:
    """List all counterparty categories for a project."""
    result = await db.execute(
        select(CounterpartyCategory).where(
            CounterpartyCategory.project_id == project_id,
            CounterpartyCategory.is_deleted == False,
        )
    )
    return result.scalars().all()


async def upsert_cp_category(db: AsyncSession, project_id: int, payload: dict) -> CounterpartyCategory:
    """Create or update a counterparty category."""
    if payload.get("id"):
        result = await db.execute(
            select(CounterpartyCategory).where(
                CounterpartyCategory.id == payload["id"],
                CounterpartyCategory.project_id == project_id,
                CounterpartyCategory.is_deleted == False,
            )
        )
        cpc = result.scalar_one_or_none()
        if cpc:
            for k, v in payload.items():
                if k != "id":
                    setattr(cpc, k, v)
            await db.commit()
            await db.refresh(cpc)
            return cpc

    payload["project_id"] = project_id
    cpc = CounterpartyCategory(**payload)
    db.add(cpc)
    await db.commit()
    await db.refresh(cpc)
    await invalidate_project_reports(project_id)
    return cpc


async def delete_cp_category(db: AsyncSession, project_id: int, cpc_id: int) -> bool:
    """Delete a counterparty category. Returns True if deleted."""
    result = await db.execute(
        select(CounterpartyCategory).where(
            CounterpartyCategory.id == cpc_id,
            CounterpartyCategory.project_id == project_id,
            CounterpartyCategory.is_deleted == False,
        )
    )
    cpc = result.scalar_one_or_none()
    if not cpc:
        return False
    cpc.soft_delete()
    await db.commit()
    await invalidate_project_reports(project_id)
    return True


# ─── Overrides ───────────────────────────────────────────────────────────────


async def list_overrides(db: AsyncSession, project_id: int) -> list:
    """List all overrides for a project."""
    result = await db.execute(select(Override).where(Override.project_id == project_id, Override.is_deleted == False))
    return result.scalars().all()


async def delete_override(db: AsyncSession, project_id: int, override_id: int) -> bool:
    """Delete an override. Returns True if deleted."""
    result = await db.execute(
        select(Override).where(
            Override.id == override_id,
            Override.project_id == project_id,
            Override.is_deleted == False,
        )
    )
    ovr = result.scalar_one_or_none()
    if not ovr:
        return False
    ovr.soft_delete()
    await db.commit()
    await invalidate_project_reports(project_id)
    return True


# ─── Opening Balances ────────────────────────────────────────────────────────


async def list_opening_balances(db: AsyncSession, project_id: int) -> list:
    """List all opening balances for a project."""
    result = await db.execute(select(OpeningBalance).where(OpeningBalance.project_id == project_id))
    return result.scalars().all()


async def upsert_opening_balance(db: AsyncSession, project_id: int, payload: dict) -> OpeningBalance:
    """Create or update an opening balance."""
    if payload.get("id"):
        result = await db.execute(
            select(OpeningBalance).where(
                OpeningBalance.id == payload["id"],
                OpeningBalance.project_id == project_id,
            )
        )
        ob = result.scalar_one_or_none()
        if ob:
            for k, v in payload.items():
                if k != "id":
                    setattr(ob, k, v)
            await db.commit()
            await db.refresh(ob)
            return ob

    payload["project_id"] = project_id
    ob = OpeningBalance(**payload)
    db.add(ob)
    await db.commit()
    await db.refresh(ob)
    await invalidate_project_reports(project_id)
    return ob


# ─── Category Reference ─────────────────────────────────────────────────────


async def list_categories(db: AsyncSession, project_id: int) -> list[dict]:
    """List all categories for a project as flat records."""
    result = await db.execute(
        select(CategoryRef)
        .where(
            CategoryRef.project_id == project_id,
            CategoryRef.is_deleted == False,
        )
        .order_by(CategoryRef.direction, CategoryRef.cat_lvl1, CategoryRef.cat_lvl2)
    )
    rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "direction": r.direction,
            "cat_lvl1": r.cat_lvl1,
            "cat_lvl2": r.cat_lvl2 or "",
        }
        for r in rows
    ]


async def add_category(
    db: AsyncSession,
    project_id: int,
    cat_lvl1: str,
    cat_lvl2: str | None = None,
    direction: str | None = None,
) -> CategoryRef:
    """Add a category reference."""
    cat = CategoryRef(
        project_id=project_id,
        direction=direction or "expense",
        cat_lvl1=cat_lvl1,
        cat_lvl2=cat_lvl2 or "",
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


async def delete_category(db: AsyncSession, cat_id: int, project_id: int) -> bool:
    """Delete a category reference. Returns True if deleted."""
    result = await db.execute(
        select(CategoryRef).where(
            CategoryRef.id == cat_id,
            CategoryRef.project_id == project_id,
            CategoryRef.is_deleted == False,
        )
    )
    cat = result.scalar_one_or_none()
    if not cat:
        return False
    cat.soft_delete()
    await db.commit()
    return True


# ─── Product Tags ────────────────────────────────────────────────────────────


async def list_product_tags(db: AsyncSession, project_id: int) -> list:
    """List all product tags for a project."""
    from backend.models.refs import ProductTag

    result = await db.execute(
        select(ProductTag)
        .where(ProductTag.project_id == project_id, ProductTag.is_deleted == False)
        .order_by(ProductTag.name)
    )
    return result.scalars().all()


async def upsert_product_tag(db: AsyncSession, project_id: int, payload: dict):
    """Create or update a product tag."""
    from backend.models.refs import ProductTag

    if payload.get("id"):
        result = await db.execute(
            select(ProductTag).where(
                ProductTag.id == payload["id"],
                ProductTag.project_id == project_id,
                ProductTag.is_deleted == False,
            )
        )
        tag = result.scalar_one_or_none()
        if tag:
            for k, v in payload.items():
                if k != "id":
                    setattr(tag, k, v)
            await db.commit()
            await db.refresh(tag)
            return tag

    tag = ProductTag(
        project_id=project_id,
        name=payload["name"],
        color=payload.get("color", "#3B82F6"),
    )
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return tag


async def delete_product_tag(db: AsyncSession, project_id: int, tag_id: int) -> bool:
    """Soft delete a tag."""
    from sqlalchemy import delete

    from backend.models.refs import ProductTag, ProductTagMap

    result = await db.execute(
        select(ProductTag).where(
            ProductTag.id == tag_id,
            ProductTag.project_id == project_id,
            ProductTag.is_deleted == False,
        )
    )
    tag = result.scalar_one_or_none()
    if not tag:
        return False

    tag.soft_delete()
    # Cascade delete mapping
    await db.execute(delete(ProductTagMap).where(ProductTagMap.tag_id == tag_id))
    await db.commit()
    return True


async def get_product_tag_mapping(db: AsyncSession, project_id: int) -> dict[int, list[int]]:
    """Get {nm_id: [tag_id_1, ...]} mapping."""
    from backend.models.refs import ProductTag, ProductTagMap

    result = await db.execute(
        select(ProductTagMap.nm_id, ProductTagMap.tag_id)
        .join(ProductTag, ProductTag.id == ProductTagMap.tag_id)
        .where(ProductTagMap.project_id == project_id, ProductTag.is_deleted == False)
    )
    mapping = {}
    for nm_id, tag_id in result:
        mapping.setdefault(nm_id, []).append(tag_id)
    return mapping


async def update_product_tag_mapping(
    db: AsyncSession, project_id: int, nm_ids: list[int], add_tags: list[int], remove_tags: list[int]
):
    """Mass assign/remove tags for products."""
    from sqlalchemy import and_, delete

    from backend.models.refs import ProductTagMap

    if not nm_ids:
        return True

    if remove_tags:
        await db.execute(
            delete(ProductTagMap).where(
                and_(
                    ProductTagMap.project_id == project_id,
                    ProductTagMap.nm_id.in_(nm_ids),
                    ProductTagMap.tag_id.in_(remove_tags),
                )
            )
        )

    if add_tags:
        # Prevent duplicates by fetching existing maps
        existing = await db.execute(
            select(ProductTagMap.nm_id, ProductTagMap.tag_id).where(
                and_(
                    ProductTagMap.project_id == project_id,
                    ProductTagMap.nm_id.in_(nm_ids),
                    ProductTagMap.tag_id.in_(add_tags),
                )
            )
        )
        existing_set = {(r.nm_id, r.tag_id) for r in existing}

        new_mappings = []
        for nm_id in nm_ids:
            for tag_id in add_tags:
                if (nm_id, tag_id) not in existing_set:
                    new_mappings.append(ProductTagMap(project_id=project_id, tag_id=tag_id, nm_id=nm_id))

        if new_mappings:
            db.add_all(new_mappings)

    await db.commit()
    return True


# ─── Product Statuses ────────────────────────────────────────────────────────


async def get_product_statuses(db: AsyncSession, project_id: int) -> dict[int, str]:
    """Get {nm_id: status} mapping for all products in project."""
    from backend.models.refs import ProductStatusMap

    result = await db.execute(
        select(ProductStatusMap.nm_id, ProductStatusMap.status).where(
            ProductStatusMap.project_id == project_id,
        )
    )
    return {r.nm_id: r.status for r in result}


async def set_product_status(db: AsyncSession, project_id: int, nm_id: int, status: str) -> bool:
    """Set or update status for a single product (upsert)."""
    from backend.models.refs import ProductStatusMap

    result = await db.execute(
        select(ProductStatusMap).where(
            ProductStatusMap.project_id == project_id,
            ProductStatusMap.nm_id == nm_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.status = status
    else:
        db.add(ProductStatusMap(project_id=project_id, nm_id=nm_id, status=status))
    await db.commit()
    return True


async def bulk_set_product_status(db: AsyncSession, project_id: int, nm_ids: list[int], status: str) -> bool:
    """Bulk set status for multiple products."""
    from backend.models.refs import ProductStatusMap

    if not nm_ids:
        return True

    # Fetch existing
    result = await db.execute(
        select(ProductStatusMap).where(
            ProductStatusMap.project_id == project_id,
            ProductStatusMap.nm_id.in_(nm_ids),
        )
    )
    existing_map = {row.nm_id: row for row in result.scalars().all()}

    for nm_id in nm_ids:
        if nm_id in existing_map:
            existing_map[nm_id].status = status
        else:
            db.add(ProductStatusMap(project_id=project_id, nm_id=nm_id, status=status))

    await db.commit()
    return True


# ─── IMT Aliases ─────────────────────────────────────────────────────────────


async def get_imt_aliases(db: AsyncSession, project_id: int) -> dict[int, str]:
    """Get {imt_id: alias_name} mapping."""
    from backend.models.refs import ImtAlias

    result = await db.execute(select(ImtAlias.imt_id, ImtAlias.name).where(ImtAlias.project_id == project_id))
    return {r.imt_id: r.name for r in result}


async def set_imt_alias(db: AsyncSession, project_id: int, imt_id: int, name: str) -> bool:
    """Set or update alias for an imt_id group (upsert)."""
    from backend.models.refs import ImtAlias

    result = await db.execute(select(ImtAlias).where(ImtAlias.project_id == project_id, ImtAlias.imt_id == imt_id))
    existing = result.scalar_one_or_none()
    if existing:
        existing.name = name
    else:
        db.add(ImtAlias(project_id=project_id, imt_id=imt_id, name=name))
    await db.commit()
    return True


# ─── Size aliases (переименование / объединение размеров) ─────────────────────


async def get_size_aliases(db: AsyncSession, project_id: int) -> dict[str, str]:
    """Get {raw_size: display_name} mapping."""
    from backend.models.refs import SizeAlias

    result = await db.execute(
        select(SizeAlias.raw_size, SizeAlias.display_name).where(SizeAlias.project_id == project_id)
    )
    return {r.raw_size: r.display_name for r in result}


async def set_size_alias(db: AsyncSession, project_id: int, raw_size: str, display_name: str) -> bool:
    """Upsert size alias; пустое имя → снять алиас (вернуть исходное значение)."""
    from sqlalchemy import delete

    from backend.models.refs import SizeAlias

    name = (display_name or "").strip()
    result = await db.execute(
        select(SizeAlias).where(SizeAlias.project_id == project_id, SizeAlias.raw_size == raw_size)
    )
    existing = result.scalar_one_or_none()
    if not name:
        if existing:
            await db.execute(delete(SizeAlias).where(SizeAlias.id == existing.id))
            await db.commit()
        return True
    if existing:
        existing.display_name = name
    else:
        db.add(SizeAlias(project_id=project_id, raw_size=raw_size, display_name=name))
    await db.commit()
    return True


# ─── Size overrides (ручной перенос товара в размер) ──────────────────────────


async def get_size_overrides(db: AsyncSession, project_id: int) -> dict[int, str]:
    """Get {nm_id: size_value} mapping."""
    from backend.models.refs import SizeOverride

    result = await db.execute(
        select(SizeOverride.nm_id, SizeOverride.size_value).where(SizeOverride.project_id == project_id)
    )
    return {r.nm_id: r.size_value for r in result}


async def bulk_set_size_override(db: AsyncSession, project_id: int, nm_ids: list[int], size_value: str) -> bool:
    """Bulk upsert размера для товаров; пустое значение → снять оверрайд (вернуть парсинг)."""
    from sqlalchemy import delete

    from backend.models.refs import SizeOverride

    if not nm_ids:
        return True

    value = (size_value or "").strip()
    if not value:
        await db.execute(
            delete(SizeOverride).where(SizeOverride.project_id == project_id, SizeOverride.nm_id.in_(nm_ids))
        )
        await db.commit()
        return True

    result = await db.execute(
        select(SizeOverride).where(SizeOverride.project_id == project_id, SizeOverride.nm_id.in_(nm_ids))
    )
    existing_map = {row.nm_id: row for row in result.scalars().all()}
    for nm_id in nm_ids:
        if nm_id in existing_map:
            existing_map[nm_id].size_value = value
        else:
            db.add(SizeOverride(project_id=project_id, nm_id=nm_id, size_value=value))
    await db.commit()
    return True


async def get_detected_sizes(db: AsyncSession, project_id: int) -> list[dict]:
    """Список размеров (effective = оверрайд → парсинг → «Без размера») со счётчиками и алиасом."""
    from backend.models.cost import Nomenclature
    from backend.services.funnel.article_parser import parse_size

    overrides = await get_size_overrides(db, project_id)
    aliases = await get_size_aliases(db, project_id)
    result = await db.execute(
        select(Nomenclature.article_wb, Nomenclature.article_seller)
        .where(Nomenclature.project_id == project_id, Nomenclature.article_wb.isnot(None))
        .limit(50000)
    )
    counts: dict[str, int] = {}
    for nm_id, article in result:
        raw = overrides.get(nm_id) or parse_size(article) or "Без размера"
        counts[raw] = counts.get(raw, 0) + 1
    sizes = [{"raw_size": raw, "display_name": aliases.get(raw, raw), "count": cnt} for raw, cnt in counts.items()]
    sizes.sort(key=lambda x: -x["count"])
    return sizes


async def build_size_resolver(db: AsyncSession, project_id: int) -> tuple[dict[int, str], dict[str, str]]:
    """(override_map {nm_id: size}, alias_map {raw_size: display}) для группировки воронки."""
    return await get_size_overrides(db, project_id), await get_size_aliases(db, project_id)


# ─── Product sub-categories (винтаж / обычные — одна на товар) ─────────────────


async def list_subcategories(db: AsyncSession, project_id: int) -> list:
    """List all product sub-categories for a project."""
    from backend.models.refs import ProductSubcategory

    result = await db.execute(
        select(ProductSubcategory)
        .where(ProductSubcategory.project_id == project_id, ProductSubcategory.is_deleted == False)
        .order_by(ProductSubcategory.name)
    )
    return result.scalars().all()


async def upsert_subcategory(db: AsyncSession, project_id: int, payload: dict):
    """Create or update a sub-category."""
    from backend.models.refs import ProductSubcategory

    if payload.get("id"):
        result = await db.execute(
            select(ProductSubcategory).where(
                ProductSubcategory.id == payload["id"],
                ProductSubcategory.project_id == project_id,
                ProductSubcategory.is_deleted == False,
            )
        )
        sub = result.scalar_one_or_none()
        if sub:
            for k, v in payload.items():
                if k != "id":
                    setattr(sub, k, v)
            await db.commit()
            await db.refresh(sub)
            return sub

    sub = ProductSubcategory(
        project_id=project_id,
        name=payload["name"],
        color=payload.get("color", "#8B5CF6"),
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub


async def delete_subcategory(db: AsyncSession, project_id: int, subcategory_id: int) -> bool:
    """Soft delete a sub-category (снимает её со всех товаров)."""
    from sqlalchemy import delete

    from backend.models.refs import ProductSubcategory, ProductSubcategoryMap

    result = await db.execute(
        select(ProductSubcategory).where(
            ProductSubcategory.id == subcategory_id,
            ProductSubcategory.project_id == project_id,
            ProductSubcategory.is_deleted == False,
        )
    )
    sub = result.scalar_one_or_none()
    if not sub:
        return False
    sub.soft_delete()
    await db.execute(delete(ProductSubcategoryMap).where(ProductSubcategoryMap.subcategory_id == subcategory_id))
    await db.commit()
    return True


async def get_subcategory_mapping(db: AsyncSession, project_id: int) -> dict[int, int]:
    """Get {nm_id: subcategory_id} mapping (single per product)."""
    from backend.models.refs import ProductSubcategory, ProductSubcategoryMap

    result = await db.execute(
        select(ProductSubcategoryMap.nm_id, ProductSubcategoryMap.subcategory_id)
        .join(ProductSubcategory, ProductSubcategory.id == ProductSubcategoryMap.subcategory_id)
        .where(ProductSubcategoryMap.project_id == project_id, ProductSubcategory.is_deleted == False)
    )
    return {r.nm_id: r.subcategory_id for r in result}


async def bulk_set_subcategory(
    db: AsyncSession, project_id: int, nm_ids: list[int], subcategory_id: int | None
) -> bool:
    """Назначить ОДНУ под-категорию товарам (single-select); None → снять."""
    from sqlalchemy import delete

    from backend.models.refs import ProductSubcategoryMap

    if not nm_ids:
        return True

    await db.execute(
        delete(ProductSubcategoryMap).where(
            ProductSubcategoryMap.project_id == project_id, ProductSubcategoryMap.nm_id.in_(nm_ids)
        )
    )
    if subcategory_id is not None:
        db.add_all(
            [
                ProductSubcategoryMap(project_id=project_id, subcategory_id=subcategory_id, nm_id=nm_id)
                for nm_id in nm_ids
            ]
        )
    await db.commit()
    return True


async def get_subcategory_names(db: AsyncSession, project_id: int) -> dict[int, str]:
    """Get {nm_id: subcategory_name} для 4-го уровня дерева воронки."""
    from backend.models.refs import ProductSubcategory, ProductSubcategoryMap

    result = await db.execute(
        select(ProductSubcategoryMap.nm_id, ProductSubcategory.name)
        .join(ProductSubcategory, ProductSubcategory.id == ProductSubcategoryMap.subcategory_id)
        .where(ProductSubcategoryMap.project_id == project_id, ProductSubcategory.is_deleted == False)
    )
    return {r.nm_id: r.name for r in result}
