"""Router: /planning/wb_payouts — WB payouts upload, list, reconcile."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import Project
from backend.schemas import WbPayoutSchema, WbReconcileRequest
from backend.project_context import get_current_project
from backend.services import planning as planning_service

router = APIRouter()


@router.post("/wb_payouts/upload")
async def upload_wb_payouts(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Upload WB payout Excel from seller cabinet. Upserts by request_id."""
    from backend.etl.parsers import parse_wb_payout_cabinet
    data = await file.read()
    from backend.utils.file_validation import validate_file_content
    validate_file_content(data, file.filename or "payouts.xlsx")
    parsed = parse_wb_payout_cabinet(data)
    if not parsed:
        raise HTTPException(400, "Не удалось распознать записи в файле")
    created, updated, skipped = await planning_service.upload_wb_payouts(db, project.id, parsed)
    await planning_service.reconcile_wb_payouts(db)
    return {"ok": True, "created": created, "updated": updated, "skipped": skipped,
            "total_parsed": len(parsed)}


@router.get("/wb_payouts")
async def get_wb_payouts(
    status: Optional[str] = Query(None),
    limit: int = Query(500),
    offset: int = Query(0),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    payouts = await planning_service.get_wb_payouts(db, project.id, status, limit, offset)
    return [WbPayoutSchema.model_validate(p).model_dump() for p in payouts]


@router.delete("/wb_payouts/{payout_id}")
async def delete_wb_payout(
    payout_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result = await planning_service.delete_wb_payout(db, project.id, payout_id)
    if not result:
        raise HTTPException(404, "Not found")
    return {"ok": True}


@router.post("/wb_payouts/{payout_id}/reconcile")
async def manual_reconcile_wb(
    payout_id: int,
    payload: WbReconcileRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    result, error = await planning_service.manual_reconcile_wb(db, project.id, payout_id, payload.txn_id)
    if error:
        raise HTTPException(404, error)
    return {"ok": True}
