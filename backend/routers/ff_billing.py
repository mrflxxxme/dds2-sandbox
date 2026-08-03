# ruff: noqa: RUF002, RUF003
"""
Router: FF billing — тарифы услуг ФФ, посуточное хранение, ожидаемая стоимость
услуг по заявке сборки и по переезду, счета ФФ (upload/reconcile/платежи).

Пути и схемы — строго из контракта backend/schemas/ff_billing.py (докстринг).
"""

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import settings
from backend.database import get_db
from backend.models import Project, User
from backend.project_context import get_current_project
from backend.schemas.ff_billing import (
    FfAssemblyExpectedCost,
    FfCandidatePayment,
    FfCustomCostPayload,
    FfInvoiceCreatePaymentRequestResponse,
    FfInvoiceCreatePayload,
    FfInvoiceDetail,
    FfInvoiceKindLiteral,
    FfInvoiceListResponse,
    FfInvoicePaymentLinkPayload,
    FfInvoicePaymentUnlinkPayload,
    FfInvoiceRow,
    FfInvoiceStatusLiteral,
    FfInvoiceUpdatePayload,
    FfInvoiceUploadResponse,
    FfStorageDailyRow,
    FfStorageRecomputePayload,
    FfTariffPayload,
    FfTariffRow,
    FfTariffUpdatePayload,
    FfTransferExpectedCost,
)
from backend.services import ff_billing
from backend.utils.file_validation import validate_file_content, validate_upload_type
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(tags=["FF Billing"])


# ─── Тарифы ──────────────────────────────────────────────────────────────────


@router.get("/warehouse/{warehouse_id}/ff-tariffs", response_model=list[FfTariffRow])
async def list_ff_tariffs(
    warehouse_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.list_tariffs(db, project.id, warehouse_id)


@router.post(
    "/warehouse/{warehouse_id}/ff-tariffs",
    response_model=FfTariffRow,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def create_ff_tariff(
    warehouse_id: int,
    payload: FfTariffPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.create_tariff(db, project.id, warehouse_id, payload)


@router.patch(
    "/ff-tariffs/{tariff_id}",
    response_model=FfTariffRow,
    dependencies=[Depends(rate_limit_write)],
)
async def update_ff_tariff(
    tariff_id: int,
    payload: FfTariffUpdatePayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.update_tariff(db, project.id, tariff_id, payload)


@router.delete("/ff-tariffs/{tariff_id}", dependencies=[Depends(rate_limit_write)])
async def delete_ff_tariff(
    tariff_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    await ff_billing.delete_tariff(db, project.id, tariff_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ─── Хранение ────────────────────────────────────────────────────────────────


@router.get("/warehouse/{warehouse_id}/ff-storage-daily", response_model=list[FfStorageDailyRow])
async def list_ff_storage_daily(
    warehouse_id: int,
    date_from: date = Query(...),
    date_to: date = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    if date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from > date_to")
    return await ff_billing.list_storage_daily(db, project.id, warehouse_id, date_from, date_to)


@router.post(
    "/warehouse/{warehouse_id}/ff-storage-daily/recompute",
    dependencies=[Depends(rate_limit_write)],
)
async def recompute_ff_storage(
    warehouse_id: int,
    payload: FfStorageRecomputePayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    updated = await ff_billing.recompute_storage_costs(
        db, project.id, warehouse_id, payload.date_from, payload.date_to
    )
    return {"updated": updated}


# ─── Ожидаемая стоимость услуг ФФ по заявке ─────────────────────────────────


@router.get("/warehouse/assembly/{request_id}/ff-expected-cost", response_model=FfAssemblyExpectedCost)
async def get_assembly_ff_expected_cost(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.compute_assembly_expected_cost(db, project.id, request_id)


@router.patch(
    "/warehouse/assembly/{request_id}/ff-custom-cost",
    response_model=FfAssemblyExpectedCost,
    dependencies=[Depends(rate_limit_write)],
)
async def set_assembly_ff_custom_cost(
    request_id: int,
    payload: FfCustomCostPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.set_assembly_custom_cost(db, project.id, request_id, payload)


# ─── Ожидаемая стоимость услуг ФФ по переезду ───────────────────────────────
# Тариф склада-ИСТОЧНИКА: сборку переезда делает он. Зеркало ручек заявки.


@router.get(
    "/warehouse/transfers/{transfer_id}/ff-expected-cost", response_model=FfTransferExpectedCost
)
async def get_transfer_ff_expected_cost(
    transfer_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.compute_transfer_expected_cost(db, project.id, transfer_id)


@router.patch(
    "/warehouse/transfers/{transfer_id}/ff-custom-cost",
    response_model=FfTransferExpectedCost,
    dependencies=[Depends(rate_limit_write)],
)
async def set_transfer_ff_custom_cost(
    transfer_id: int,
    payload: FfCustomCostPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.set_transfer_custom_cost(db, project.id, transfer_id, payload)


# ─── Счета ───────────────────────────────────────────────────────────────────
# Статические пути (/upload, /candidate-payments, /payments/*) — ДО /{invoice_id}.

_INVOICE_ALLOWED_MIME = (
    "application/pdf",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument",
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
)
_INVOICE_ALLOWED_EXT = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
)


@router.get("/ff-invoices", response_model=FfInvoiceListResponse)
async def list_ff_invoices(
    warehouse_id: int | None = Query(None),
    status_filter: FfInvoiceStatusLiteral | None = Query(None, alias="status"),
    kind: FfInvoiceKindLiteral | None = Query(None),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.list_invoices(
        db, project.id, warehouse_id=warehouse_id, status=status_filter, kind=kind
    )


@router.post(
    "/ff-invoices",
    response_model=FfInvoiceDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def create_ff_invoice(
    payload: FfInvoiceCreatePayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.create_invoice(db, project.id, payload)


@router.post(
    "/ff-invoices/upload",
    response_model=FfInvoiceUploadResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def upload_ff_invoice(
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Файл счёта ФФ → распознавание реквизитов → черновик счёта (NEW/MIXED)."""
    # Исполняемые и активное содержимое (svg/html/xml — stored XSS) режет хелпер;
    # тип — по расширению приоритетно (клиентскому Content-Type нельзя доверять).
    mime = validate_upload_type(file.filename, file.content_type)
    filename_lower = (file.filename or "").lower()
    ok_type = (
        mime is not None and any(mime.startswith(p) for p in _INVOICE_ALLOWED_MIME)
    ) or filename_lower.endswith(_INVOICE_ALLOWED_EXT)
    if not ok_type:
        raise HTTPException(
            status_code=415, detail="Поддерживаются PDF, Word, Excel или фото счёта (JPG/PNG/HEIC)"
        )

    # Размер режем ДО чтения тела (Content-Length), затем перестраховка по факту.
    max_mb = settings.MAX_UPLOAD_SIZE_MB
    max_bytes = max_mb * 1024 * 1024
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(status_code=413, detail=f"Файл слишком большой. Максимум: {max_mb} МБ")
    data = await file.read()
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Файл слишком большой. Максимум: {max_mb} МБ")
    validate_file_content(data, file.filename or "invoice")  # magic-bytes по расширению

    return await ff_billing.upload_invoice(
        db, project.id, file_data=data, filename=file.filename or "invoice",
        mime_type=mime,
    )


@router.get("/ff-invoices/candidate-payments", response_model=list[FfCandidatePayment])
async def list_ff_invoice_candidate_payments(
    invoice_id: int = Query(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.list_candidate_payments(db, project.id, invoice_id)


@router.post(
    "/ff-invoices/payments/link",
    response_model=list[FfInvoiceRow],
    dependencies=[Depends(rate_limit_write)],
)
async def link_ff_invoice_payments(
    payload: FfInvoicePaymentLinkPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.link_invoice_payments(db, project.id, payload)


@router.post(
    "/ff-invoices/payments/unlink",
    response_model=list[FfInvoiceRow],
    dependencies=[Depends(rate_limit_write)],
)
async def unlink_ff_invoice_payments(
    payload: FfInvoicePaymentUnlinkPayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.unlink_invoice_payments(db, project.id, payload)


@router.get("/ff-invoices/{invoice_id}", response_model=FfInvoiceDetail)
async def get_ff_invoice(
    invoice_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.get_invoice_detail(db, project.id, invoice_id)


@router.patch(
    "/ff-invoices/{invoice_id}",
    response_model=FfInvoiceDetail,
    dependencies=[Depends(rate_limit_write)],
)
async def update_ff_invoice(
    invoice_id: int,
    payload: FfInvoiceUpdatePayload,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.update_invoice(db, project.id, invoice_id, payload)


@router.delete("/ff-invoices/{invoice_id}", dependencies=[Depends(rate_limit_write)])
async def delete_ff_invoice(
    invoice_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    await ff_billing.delete_invoice(db, project.id, invoice_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/ff-invoices/{invoice_id}/document/download")
async def download_ff_invoice_document(
    invoice_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    data, filename, content_type = await ff_billing.download_invoice_document(
        db, project.id, invoice_id
    )
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(
        content=data,
        media_type=content_type,
        headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff"},
    )


@router.post(
    "/ff-invoices/{invoice_id}/reconcile",
    response_model=FfInvoiceDetail,
    dependencies=[Depends(rate_limit_write)],
)
async def reconcile_ff_invoice(
    invoice_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    return await ff_billing.reconcile_invoice(db, project.id, invoice_id)


@router.post(
    "/ff-invoices/{invoice_id}/create-payment-request",
    response_model=FfInvoiceCreatePaymentRequestResponse,
    dependencies=[Depends(rate_limit_write)],
)
async def create_ff_invoice_payment_request(
    invoice_id: int,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    pr_id = await ff_billing.create_payment_request_for_invoice(db, project.id, invoice_id, user.id)
    return FfInvoiceCreatePaymentRequestResponse(payment_request_id=pr_id)
