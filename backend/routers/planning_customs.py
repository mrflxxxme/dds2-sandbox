"""Router: /planning/customs — customs DT (FTS), topup, alloc."""

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.schemas import CustomsTopupSchema, CustomsAllocSchema, CustomsDTUpdate
from backend.project_context import get_current_project
from backend.services import planning as planning_service

router = APIRouter()


# ─── Customs TOPUP / ALLOC ────────────────────────────────────────────────────

@router.get("/customs/topup", response_model=list[CustomsTopupSchema])
async def get_customs_topup(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    topups, alloc_map = await planning_service.get_customs_topup(db, project.id)
    out = []
    for t in topups:
        allocated = alloc_map.get(t.topup_txn_id, Decimal("0"))
        remaining = t.amount_rub - allocated
        d = CustomsTopupSchema.model_validate(t)
        d.allocated = allocated
        d.remaining = remaining
        out.append(d)
    return out


@router.get("/customs/alloc", response_model=list[CustomsAllocSchema])
async def get_customs_alloc(
    topup_txn_id: Optional[str] = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.get_customs_alloc(db, project.id, topup_txn_id)


@router.post("/customs/alloc", response_model=CustomsAllocSchema)
async def create_alloc(
    payload: CustomsAllocSchema,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await planning_service.create_alloc(
        db, project.id, payload.model_dump(exclude={"id"})
    )


@router.delete("/customs/alloc/{alloc_id}")
async def delete_alloc(
    alloc_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_alloc(db, project.id, alloc_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


# ─── Customs DT (FTS report) ─────────────────────────────────────────────────

@router.post("/customs_dt/upload_fts")
async def upload_fts_report(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload FTS PDF report and parse DT declarations."""
    content = await file.read()
    from backend.utils.file_validation import validate_file_content
    validate_file_content(content, file.filename or "report.pdf")
    parsed = planning_service.parse_fts_pdf(content)
    if not parsed:
        raise HTTPException(400, "Не удалось найти ДТ строки в PDF")
    created, skipped = await planning_service.upload_fts_and_create_dts(db, project.id, parsed)
    return {"ok": True, "created": created, "skipped": skipped, "parsed": parsed}


@router.get("/customs_dt")
async def get_customs_dt_list(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    dts = await planning_service.get_customs_dt_list(db, project.id)
    return [
        {"id": d.id, "dt_number": d.dt_number, "dt_date": d.dt_date.isoformat(),
         "amount_rub": float(d.amount_rub), "order_no": d.order_no, "note": d.note}
        for d in dts
    ]


@router.put("/customs_dt/{dt_id}")
async def update_customs_dt(
    dt_id: int,
    payload: CustomsDTUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.update_customs_dt(db, project.id, dt_id, payload.model_dump(exclude_unset=True))
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.delete("/customs_dt/{dt_id}")
async def delete_customs_dt(
    dt_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_customs_dt(db, project.id, dt_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}
