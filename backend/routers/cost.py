"""
Router: /cost — nomenclature, duty rules, cost orders & items, cost calculation.
Thin HTTP layer — all business logic is in services/cost_service.py.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.services import cost_service

router = APIRouter(prefix="/cost")


# ─── Nomenclature ─────────────────────────────────────────────────────────────

@router.get("/nomenclature")
async def get_nomenclature(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    items = await cost_service.get_nomenclature(db, project.id)
    return [
        {
            "id": n.id, "barcode": n.barcode, "brand": n.brand,
            "subject": n.subject, "article_seller": n.article_seller,
            "article_wb": n.article_wb, "volume_l": float(n.volume_l or 0),
        }
        for n in items
    ]


@router.post("/nomenclature/upload")
async def upload_nomenclature(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    data = await file.read()

    from backend.utils.file_validation import validate_file_content
    validate_file_content(data, file.filename or "upload.xlsx")

    inserted, updated = await cost_service.upload_nomenclature(db, project.id, data)
    return {"inserted": inserted, "updated": updated}


# ─── Duty Rules ───────────────────────────────────────────────────────────────

@router.get("/duty_rules")
async def get_duty_rules(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    rules = await cost_service.get_duty_rules(db, project.id)
    return [
        {
            "id": r.id, "subject": r.subject, "basis": r.basis,
            "rate": float(r.rate), "util_collect_rub": float(r.util_collect_rub),
            "note": r.note,
        }
        for r in rules
    ]


@router.post("/duty_rules")
async def upsert_duty_rule(
    payload: dict,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result, error = await cost_service.upsert_duty_rule(db, project.id, payload)
    if error:
        raise HTTPException(400, error)
    return {"ok": True}


@router.delete("/duty_rules/{rule_id}")
async def delete_duty_rule(
    rule_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await cost_service.delete_duty_rule(db, project.id, rule_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ─── Cost Orders ──────────────────────────────────────────────────────────────

@router.get("/orders")
async def get_cost_orders(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await cost_service.get_cost_orders(db, project.id)


@router.post("/orders")
async def create_cost_order(
    payload: dict,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result, error = await cost_service.create_cost_order(db, project.id, payload)
    if error:
        raise HTTPException(400, error)
    return result


@router.put("/orders/{order_no}")
async def update_cost_order(
    order_no: str,
    payload: dict,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Edit an existing cost order."""
    result, error = await cost_service.update_cost_order(db, project.id, order_no, payload)
    if error:
        status = 404 if error == "Not found" else 400
        raise HTTPException(status, error)
    return result


@router.delete("/orders/{order_no}")
async def delete_cost_order(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await cost_service.delete_cost_order(db, project.id, order_no)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ─── Generate Payment Plan ───────────────────────────────────────────────────

@router.post("/orders/{order_no}/generate_plan")
async def generate_plan(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Generate planned payments from CostOrder data."""
    result = await cost_service.generate_payment_plan(db, project.id, order_no, project.tax_rate)
    if "error" in result:
        raise HTTPException(result.get("status", 400), result["error"])
    return result


# ─── Cost Order Items ─────────────────────────────────────────────────────────

@router.get("/orders/{order_no}/items")
async def get_cost_order_items(
    order_no: str,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    items, nom_map = await cost_service.get_cost_order_items(db, project.id, order_no)
    return [
        {
            "id": i.id, "barcode": i.barcode, "subject": i.subject,
            "article_seller": i.article_seller, "qty": i.qty,
            "article_wb": nom_map.get(i.barcode),
            "price_cny": cost_service.safe_float(i.price_cny),
            "weight_kg": cost_service.safe_float(i.weight_kg),
            "area_m2": cost_service.safe_float(i.area_m2),
            "volume_m3": cost_service.safe_float(i.volume_m3),
            "cost_rub": cost_service.safe_float(i.cost_rub),
            "delivery_rub": cost_service.safe_float(i.delivery_rub),
            "duty_rub": cost_service.safe_float(i.duty_rub),
            "vat_rub": cost_service.safe_float(i.vat_rub),
            "util_rub": cost_service.safe_float(i.util_rub),
            "total_rub": cost_service.safe_float(i.total_rub),
            "total_cny": cost_service.safe_float(i.total_cny),
            "unrecognized": i.unrecognized,
        }
        for i in items
    ]


@router.post("/orders/{order_no}/upload")
async def upload_order_items(
    order_no: str,
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload Excel file with order items, calculate cost per item."""
    data = await file.read()

    from backend.utils.file_validation import validate_file_content
    validate_file_content(data, file.filename or "upload.xlsx")

    from decimal import Decimal
    tax_rate = Decimal(str(project.tax_rate)) if project.tax_rate else None
    inserted, unrecognized, error = await cost_service.upload_order_items(
        db, project.id, order_no, data, tax_rate
    )
    if error:
        status = 404 if "не найден" in error else 400
        raise HTTPException(status, error)
    return {"inserted": inserted, "unrecognized": unrecognized}
