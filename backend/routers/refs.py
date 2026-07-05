"""
Router: /refs — accounts, cp_categories, overrides, opening_balances, category_ref

Delegates CRUD logic to services/refs_service.py.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.schemas import (
    AccountSchema,
    CategoryRefCreate,
    OpeningBalanceSchema,
)
from backend.schemas.refs import (
    CategoryOverrideBulkPayload,
    BoxWeightPayload,
    CategoryRefUpdate,
    ExcludedWarehousesPayload,
    ForecastRfDefaultDaysPayload,
    ImtAliasPayload,
    PalletBoxesBySizePayload,
    PreorderAllowedWarehousesPayload,
    ProductStatusBulkPayload,
    ProductStatusPayload,
    ProductTagMappingPayload,
    ProductTagSchema,
    SizeAliasPayload,
    SizeOverrideBulkPayload,
    SubcategoryBulkPayload,
    SubcategorySchema,
)
from backend.services import refs_service
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/refs")


# ─── Accounts ─────────────────────────────────────────────────────────────────


@router.get("/accounts")
async def get_accounts(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await refs_service.list_accounts(db, project.id)


@router.post("/accounts")
async def upsert_account(
    payload: AccountSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    return await refs_service.upsert_account(db, project.id, payload.model_dump(exclude_unset=True))


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    deleted = await refs_service.delete_account(db, project.id, account_id)
    if not deleted:
        raise HTTPException(404, "Account not found")
    return {"ok": True}


# Категории контрагентов ведутся на странице «Контрагенты» (карточка/массово,
# /counterparties/{id}/category и /counterparties/bulk_category) — отдельный
# плоский CRUD `cp_categories` удалён. Маппинг cp_key→категория по-прежнему живёт
# в таблице counterparty_categories и применяется в импорте (etl/service.py).


# ─── Overrides ────────────────────────────────────────────────────────────────


@router.get("/overrides")
async def get_overrides(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await refs_service.list_overrides(db, project.id)


@router.delete("/overrides/{override_id}")
async def delete_override(
    override_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    deleted = await refs_service.delete_override(db, project.id, override_id)
    if not deleted:
        raise HTTPException(404, "Override not found")
    return {"ok": True}


# ─── Opening Balances ─────────────────────────────────────────────────────────


@router.get("/opening_balances")
async def get_opening_balances(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await refs_service.list_opening_balances(db, project.id)


@router.post("/opening_balances")
async def upsert_opening_balance(
    payload: OpeningBalanceSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    return await refs_service.upsert_opening_balance(db, project.id, payload.model_dump(exclude_unset=True))


# ─── Category Reference ──────────────────────────────────────────────────────


@router.get("/categories")
async def get_categories(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await refs_service.list_categories(db, project.id)


@router.post("/categories")
async def add_category(
    payload: CategoryRefCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    if not payload.cat_lvl1.strip():
        raise HTTPException(400, "cat_lvl1 is required")

    cat = await refs_service.add_category(
        db,
        project.id,
        payload.cat_lvl1.strip(),
        (payload.cat_lvl2 or "").strip() or None,
        direction=payload.direction.strip(),
        is_cogs=payload.is_cogs,
    )
    return {"ok": True, "id": cat.id}


@router.patch("/categories/{cat_id}")
async def update_category(
    cat_id: int,
    payload: CategoryRefUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    """Toggle the «себестоимость» (COGS) flag on an expense category."""
    updated = await refs_service.update_category(db, cat_id, project.id, is_cogs=payload.is_cogs)
    if not updated:
        raise HTTPException(404, "Category not found")
    return {"ok": True}


@router.delete("/categories/{cat_id}")
async def delete_category(
    cat_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    deleted = await refs_service.delete_category(db, cat_id, project.id)
    if not deleted:
        raise HTTPException(404, "Category not found")
    return {"ok": True}


# ─── Warehouse Settings ──────────────────────────────────────────────────────


@router.get("/warehouses")
async def get_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """List all available WB warehouses (WAREHOUSE_COORDS + any seen in this project's stocks)."""
    from backend.services import settings_service

    return await settings_service.get_all_warehouses(db, project.id)


@router.get("/excluded-warehouses")
async def get_excluded_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get excluded warehouse names for current project."""
    from backend.services import settings_service

    return await settings_service.get_excluded_warehouses(db, project.id)


@router.put("/excluded-warehouses")
async def set_excluded_warehouses(
    payload: ExcludedWarehousesPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    """Set excluded warehouses. Body: {"warehouses": ["Новосибирск", ...]}"""
    from backend.services import settings_service

    result = await settings_service.set_excluded_warehouses(db, project.id, payload.warehouses)
    return {"ok": True, "excluded": result}


@router.get("/preorder-allowed-warehouses")
async def get_preorder_allowed_warehouses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Warehouses where a preorder (предзаявка) is allowed without a WB acceptance
    limit. Складам ВНЕ списка предзаявка по «нет лимита» запрещена."""
    from backend.services import settings_service

    return await settings_service.get_preorder_allowed_warehouses(db, project.id)


@router.put("/preorder-allowed-warehouses")
async def set_preorder_allowed_warehouses(
    payload: PreorderAllowedWarehousesPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    """Set preorder-allowed warehouses. Body: {"warehouses": ["Электросталь", ...]}"""
    from backend.services import settings_service

    result = await settings_service.set_preorder_allowed_warehouses(db, project.id, payload.warehouses)
    return {"ok": True, "preorder_allowed": result}


@router.get("/pallet-boxes-by-size")
async def get_pallet_boxes_by_size(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Ручной override «коробок на паллету» по размеру коробки (canonical → int)."""
    from backend.services import settings_service

    return await settings_service.get_pallet_boxes_by_size(db, project.id)


@router.put("/pallet-boxes-by-size")
async def set_pallet_boxes_by_size(
    payload: PalletBoxesBySizePayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    """Set pallet boxes-per-size override. Body: {"sizes": {"60x40x50": 16, ...}}"""
    from backend.services import settings_service

    result = await settings_service.set_pallet_boxes_by_size(db, project.id, payload.sizes)
    return {"ok": True, "sizes": result}


@router.get("/box-weight")
async def get_box_weight(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Вес пустой коробки (кг) для расчётного веса отгрузки сборки. None — не задан."""
    from backend.services import settings_service

    weight = await settings_service.get_box_weight_kg(db, project.id)
    return {"weight_kg": weight}


@router.put("/box-weight")
async def set_box_weight(
    payload: BoxWeightPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    """Задать вес пустой коробки. Body: {"weight_kg": 0.35}. Отрицательное → 0."""
    from backend.services import settings_service

    result = await settings_service.set_box_weight_kg(db, project.id, payload.weight_kg)
    return {"ok": True, "weight_kg": result}


@router.get("/forecast-rf-default-days")
async def get_forecast_rf_default_days(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Get default RF→WB lead time (days) used as a fallback when
    `WarehouseDeliveryTime` is empty for a warehouse."""
    from backend.services import settings_service

    days = await settings_service.get_forecast_rf_default_days(db, project.id)
    return {"days": days}


@router.put("/forecast-rf-default-days")
async def set_forecast_rf_default_days(
    payload: ForecastRfDefaultDaysPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    """Set default RF→WB lead time (days). Range 0..365."""
    from backend.cache import invalidate_cache
    from backend.services import settings_service

    days = await settings_service.set_forecast_rf_default_days(db, project.id, payload.days)
    # Forecast/matrix is recomputed on every request (no cache prefix), but report-level
    # caches that include forecast snapshots may need invalidation.
    await invalidate_cache(f"reports:stock_analytics:project_id={project.id}")
    return {"ok": True, "days": days}


# ─── Product Tags ────────────────────────────────────────────────────────────


@router.get("/tags", response_model=list[ProductTagSchema])
async def get_product_tags(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await refs_service.list_product_tags(db, project.id)


@router.post("/tags", response_model=ProductTagSchema)
async def upsert_product_tag(
    payload: ProductTagSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    return await refs_service.upsert_product_tag(db, project.id, payload.model_dump(exclude_unset=True))


@router.delete("/tags/{tag_id}")
async def delete_product_tag(
    tag_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    deleted = await refs_service.delete_product_tag(db, project.id, tag_id)
    if not deleted:
        raise HTTPException(404, "Tag not found")
    return {"ok": True}


@router.get("/tags/mapping")
async def get_product_tag_mapping(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Returns {nm_id: [tag_id_1, ...]}"""
    return await refs_service.get_product_tag_mapping(db, project.id)


@router.post("/tags/mapping")
async def update_product_tag_mapping(
    payload: ProductTagMappingPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    await refs_service.update_product_tag_mapping(db, project.id, payload.nm_ids, payload.add_tags, payload.remove_tags)
    return {"ok": True}


# ─── Product Statuses ────────────────────────────────────────────────────────


@router.get("/product-statuses")
async def get_product_statuses(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Returns {nm_id: status} mapping."""
    return await refs_service.get_product_statuses(db, project.id)


@router.patch("/product-statuses")
async def set_product_status(
    payload: ProductStatusPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    await refs_service.set_product_status(db, project.id, payload.nm_id, payload.status)
    return {"ok": True}


@router.post("/product-statuses/bulk")
async def bulk_set_product_status(
    payload: ProductStatusBulkPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    await refs_service.bulk_set_product_status(db, project.id, payload.nm_ids, payload.status)
    return {"ok": True}


# ─── IMT Aliases ─────────────────────────────────────────────────────────────


@router.get("/imt-aliases")
async def get_imt_aliases(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Returns {imt_id: alias_name} mapping."""
    return await refs_service.get_imt_aliases(db, project.id)


@router.patch("/imt-aliases")
async def set_imt_alias(
    payload: ImtAliasPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    await refs_service.set_imt_alias(db, project.id, payload.imt_id, payload.name)
    return {"ok": True}


# ─── Sizes (overrides + aliases) ──────────────────────────────────────────────


@router.get("/sizes")
async def get_sizes(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Список размеров со счётчиками и текущим отображаемым именем (алиасом)."""
    return await refs_service.get_detected_sizes(db, project.id)


@router.get("/size-overrides")
async def get_size_overrides(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Returns {nm_id: size_value} mapping."""
    return await refs_service.get_size_overrides(db, project.id)


@router.post("/size-overrides")
async def bulk_set_size_override(
    payload: SizeOverrideBulkPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    await refs_service.bulk_set_size_override(db, project.id, payload.nm_ids, payload.size_value)
    return {"ok": True}


@router.get("/size-aliases")
async def get_size_aliases(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Returns {raw_size: display_name} mapping."""
    return await refs_service.get_size_aliases(db, project.id)


@router.patch("/size-aliases")
async def set_size_alias(
    payload: SizeAliasPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    await refs_service.set_size_alias(db, project.id, payload.raw_size, payload.display_name)
    return {"ok": True}


# ─── Category overrides (ручной перенос товара в категорию) ───────────────────


@router.get("/category-overrides")
async def get_category_overrides(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Returns {nm_id: category_value} mapping."""
    return await refs_service.get_category_overrides(db, project.id)


@router.post("/category-overrides")
async def bulk_set_category_override(
    payload: CategoryOverrideBulkPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    await refs_service.bulk_set_category_override(db, project.id, payload.nm_ids, payload.category_value)
    return {"ok": True}


@router.get("/barcode-map")
async def get_barcode_map(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Returns {barcode: nm_id} — для массовой привязки по баркодам (вставка из Excel)."""
    return await refs_service.get_barcode_nm_map(db, project.id)


# ─── Product sub-categories (винтаж / обычные) ────────────────────────────────


@router.get("/subcategories", response_model=list[SubcategorySchema])
async def get_subcategories(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await refs_service.list_subcategories(db, project.id)


@router.post("/subcategories", response_model=SubcategorySchema)
async def upsert_subcategory(
    payload: SubcategorySchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    return await refs_service.upsert_subcategory(db, project.id, payload.model_dump(exclude_unset=True))


@router.delete("/subcategories/{subcategory_id}")
async def delete_subcategory(
    subcategory_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    deleted = await refs_service.delete_subcategory(db, project.id, subcategory_id)
    if not deleted:
        raise HTTPException(404, "Sub-category not found")
    return {"ok": True}


@router.get("/subcategories/mapping")
async def get_subcategory_mapping(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Returns {nm_id: subcategory_id} (одна на товар)."""
    return await refs_service.get_subcategory_mapping(db, project.id)


@router.post("/subcategories/mapping")
async def bulk_set_subcategory(
    payload: SubcategoryBulkPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(rate_limit_write),
):
    await refs_service.bulk_set_subcategory(db, project.id, payload.nm_ids, payload.subcategory_id)
    return {"ok": True}
