# ruff: noqa: RUF002, RUF003
"""
Router: /payment-requests — заявки на оплату перевозчику (логист → оплата).

HTTP + валидация; вся логика — в services/. Все write-эндпоинты под rate_limit_write.
«create-draft» — РЕАЛЬНАЯ запись черновика платёжки в банк (необратимо), за гейтом confirm.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.config import settings
from backend.database import get_db
from backend.integrations.faktura_api import FakturaApiError
from backend.models import Project, ProjectMember, User
from backend.models.payment_request import PaymentRequest
from backend.project_context import get_current_project
from backend.rbac import require_role
from backend.services.bank_directory import resolve_bank
from backend.schemas.payment_request import (
    ALLOWED_PR_DOC_TYPES,
    ArchiveShipmentsRequest,
    ArchiveTxnsRequest,
    CancelRequest,
    CounterpartyReconciliation,
    CreateDraftRequest,
    CreateDraftsRequest,
    CreateDraftsResponse,
    CreateDraftResult,
    InvoiceParseResult,
    LinkShipmentsRequest,
    PaymentActionRequest,
    PaymentCategoryCreate,
    PaymentCategorySchema,
    PaymentCategoryUpdate,
    PaymentRequestCreate,
    PaymentRequestDetail,
    PaymentRequestDocumentResponse,
    PaymentRequestEventResponse,
    PaymentRequestListResponse,
    PaymentRequestRow,
    PaymentRequestStatusPoll,
    PaymentRequestUpdate,
    ShippableShipmentRow,
    SubmitRequest,
    UnlinkShipmentsRequest,
)
from backend.services import invoice_parser
from backend.services import payment_category_service as pcs
from backend.services import payment_request_documents as docs_service
from backend.services.faktura_payment import PaymentDraftError, create_payment_draft
from backend.services.payment_request_service import (
    PaymentRequestService,
    PaymentRequestValidationError,
)
from backend.utils.file_validation import validate_file_content
from backend.utils.rate_limit import rate_limit_write

router = APIRouter(prefix="/payment-requests", tags=["Payment Requests"])

_PR_DOC_MAX_MB = 20


def _to_detail(pr: PaymentRequest) -> PaymentRequestDetail:
    """Map a loaded PaymentRequest (with documents/events) to the detail schema, dropping deleted docs."""
    detail = PaymentRequestDetail.model_validate(pr)
    active_docs = sorted(
        (d for d in pr.documents if not d.is_deleted), key=lambda d: d.uploaded_at, reverse=True
    )
    detail.documents = [PaymentRequestDocumentResponse.model_validate(d) for d in active_docs]
    detail.events = [
        PaymentRequestEventResponse.model_validate(e)
        for e in sorted(pr.events, key=lambda e: e.changed_at)
    ]
    detail.doc_count = len(active_docs)
    detail.project_name = pr.project.name if pr.project else None  # «—» для общей заявки
    return detail


def _actor(user: User) -> str:
    return getattr(user, "email", None) or f"user:{user.id}"


# ─── Reads ────────────────────────────────────────────────────────────────────


@router.get("/banks/{bic}")
async def get_bank_by_bic(bic: str, _user: User = Depends(get_current_user)) -> dict[str, str]:
    """БИК → {bic, corr_account, name} из справочника — для авто-заполнения реквизитов
    получателя. 404, если банка нет в наборе (тогда к/с вводится вручную)."""
    bank = resolve_bank(bic)
    if bank is None:
        raise HTTPException(status_code=404, detail="Банк по БИК не найден в справочнике")
    return bank


@router.get("", response_model=PaymentRequestListResponse)
async def list_payment_requests(
    status_filter: str | None = Query(None, alias="status"),
    category: str | None = None,
    brand: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    counterparty_id: int | None = None,
    search: str | None = None,
    limit: int = Query(2000, ge=1, le=5000),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentRequestService(db)
    rows, total, doc_counts, project_names = await service.list_requests(
        project.id,
        status=status_filter,
        category=category,
        brand=brand,
        date_from=date_from,
        date_to=date_to,
        counterparty_id=counterparty_id,
        search=search,
        limit=limit,
    )
    items = []
    for pr in rows:
        row = PaymentRequestRow.model_validate(pr)
        row.doc_count = doc_counts.get(pr.id, 0)
        row.project_name = project_names.get(pr.project_id) if pr.project_id is not None else None
        items.append(row)
    return PaymentRequestListResponse(items=items, total=total)


@router.get("/shippable", response_model=list[ShippableShipmentRow])
async def list_shippable(
    search: str | None = None,
    has_request: bool | None = None,
    archived: bool = False,
    counterparty_id: int | None = None,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentRequestService(db)
    return await service.list_shippable(
        project.id,
        search=search,
        has_request=has_request,
        archived=archived,
        counterparty_id=counterparty_id,
    )


@router.get("/reconciliation", response_model=CounterpartyReconciliation)
async def counterparty_reconciliation(
    counterparty_id: int,
    archived: bool = False,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сверка заборов и платежей одного перевозчика (карточка контрагента)."""
    service = PaymentRequestService(db)
    return await service.get_counterparty_reconciliation(
        project.id, counterparty_id, archived=archived
    )


@router.post("/orphan-payments/archive", dependencies=[Depends(rate_limit_write)])
async def archive_orphan_payments(
    body: ArchiveTxnsRequest,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentRequestService(db)
    n = await service.archive_txns(project.id, body.transaction_ids, user.id)
    return {"archived": n}


@router.post("/orphan-payments/unarchive", dependencies=[Depends(rate_limit_write)])
async def unarchive_orphan_payments(
    body: ArchiveTxnsRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentRequestService(db)
    n = await service.unarchive_txns(project.id, body.transaction_ids)
    return {"unarchived": n}


@router.post("/payments/link", dependencies=[Depends(rate_limit_write)])
async def link_shipments_to_payment(
    body: LinkShipmentsRequest,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Привязать N заборов к одному платежу выписки (агрегированная оплата)."""
    service = PaymentRequestService(db)
    try:
        return await service.link_shipments_to_payment(
            project.id, body.transaction_id, body.shipment_ids, user.id
        )
    except PaymentRequestValidationError as e:
        raise HTTPException(status_code=404, detail=e.args[0]) from None


@router.post("/payments/unlink", dependencies=[Depends(rate_limit_write)])
async def unlink_shipments_from_payment(
    body: UnlinkShipmentsRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отвязать заборы от платежа."""
    service = PaymentRequestService(db)
    n = await service.unlink_shipments(project.id, body.shipment_ids)
    return {"unlinked": n}


@router.post("/shippable/archive", dependencies=[Depends(rate_limit_write)])
async def archive_shipments(
    body: ArchiveShipmentsRequest,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentRequestService(db)
    n = await service.archive_shipments(project.id, body.shipment_ids, user.id)
    return {"archived": n}


@router.post("/shippable/unarchive", dependencies=[Depends(rate_limit_write)])
async def unarchive_shipments(
    body: ArchiveShipmentsRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentRequestService(db)
    n = await service.unarchive_shipments(project.id, body.shipment_ids)
    return {"unarchived": n}


# ─── Справочник «Назначение оплаты» (категории) ─────────────────────────────────
# ВАЖНО: объявлены ДО `/{request_id}` — иначе «categories» матчится как request_id.


@router.get("/categories", response_model=list[PaymentCategorySchema])
async def list_payment_categories(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Список категорий для выпадающего «Назначение» + карты лейблов на странице Оплаты."""
    return await pcs.list_categories(db, project.id)


@router.post(
    "/categories",
    response_model=PaymentCategorySchema,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write), Depends(require_role("admin"))],
)
async def create_payment_category(
    body: PaymentCategoryCreate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await pcs.create_category(db, project.id, body.label, body.sort_order)
    except pcs.PaymentCategoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.patch(
    "/categories/{cat_id}",
    response_model=PaymentCategorySchema,
    dependencies=[Depends(rate_limit_write), Depends(require_role("admin"))],
)
async def update_payment_category(
    cat_id: int,
    body: PaymentCategoryUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        return await pcs.update_category(db, project.id, cat_id, body.label, body.sort_order)
    except pcs.PaymentCategoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete(
    "/categories/{cat_id}",
    dependencies=[Depends(rate_limit_write), Depends(require_role("admin"))],
)
async def delete_payment_category(
    cat_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    try:
        await pcs.delete_category(db, project.id, cat_id)
    except pcs.PaymentCategoryError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@router.get("/{request_id}", response_model=PaymentRequestDetail)
async def get_payment_request(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentRequestService(db)
    pr = await service.get_request(project.id, request_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="Заявка на оплату не найдена")
    detail = _to_detail(pr)
    nums = await service.covered_shipment_numbers(project.id, request_id)
    detail.shipment_numbers = nums
    detail.shipment_count = len(nums)
    return detail


@router.get("/{request_id}/status", response_model=PaymentRequestStatusPoll)
async def get_payment_request_status(
    request_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(
        select(PaymentRequest).where(
            PaymentRequest.id == request_id,
            # свой проект ИЛИ общая (без проекта)
            or_(PaymentRequest.project_id == project.id, PaymentRequest.project_id.is_(None)),
            PaymentRequest.is_deleted == False,  # noqa: E712
        )
    )
    pr = res.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Заявка на оплату не найдена")
    return PaymentRequestStatusPoll.model_validate(pr)


# ─── Writes ───────────────────────────────────────────────────────────────────


@router.post(
    "",
    response_model=PaymentRequestDetail,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def create_payment_request(
    body: PaymentRequestCreate,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    # Целевой проект: поле не передано → текущий; null → общая (без проекта);
    # число → этот проект (проверяем членство, чтобы нельзя было «подложить» чужой).
    if "project_id" in body.model_fields_set:
        target_pid = body.project_id
        if target_pid is not None and target_pid != project.id:
            member = (
                await db.execute(
                    select(ProjectMember).where(
                        ProjectMember.project_id == target_pid,
                        ProjectMember.user_id == user.id,
                        ProjectMember.is_deleted == False,  # noqa: E712
                    )
                )
            ).scalar_one_or_none()
            if member is None:
                raise HTTPException(status_code=403, detail="Нет доступа к выбранному проекту")
    else:
        target_pid = project.id

    service = PaymentRequestService(db)
    try:
        pr = await service.create_request(target_pid, body, user.id)
    except PaymentRequestValidationError as e:
        raise HTTPException(status_code=422, detail={"error": "VALIDATION", "missing": e.missing})
    full = await service.get_request(pr.project_id, pr.id)
    return _to_detail(full)  # type: ignore[arg-type]


@router.patch(
    "/{request_id}",
    response_model=PaymentRequestDetail,
    dependencies=[Depends(rate_limit_write)],
)
async def update_payment_request(
    request_id: int,
    body: PaymentRequestUpdate,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentRequestService(db)
    try:
        await service.update_request(project.id, request_id, body)
    except PaymentRequestValidationError as e:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "missing": e.missing})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    full = await service.get_request(project.id, request_id)
    return _to_detail(full)  # type: ignore[arg-type]


@router.post(
    "/{request_id}/submit",
    response_model=PaymentRequestDetail,
    dependencies=[Depends(rate_limit_write)],
)
async def submit_payment_request(
    request_id: int,
    body: SubmitRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    service = PaymentRequestService(db)
    try:
        await service.submit_request(project.id, request_id, comment=body.comment)
    except PaymentRequestValidationError as e:
        raise HTTPException(status_code=422, detail={"error": "VALIDATION", "missing": e.missing})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    full = await service.get_request(project.id, request_id)
    return _to_detail(full)  # type: ignore[arg-type]


@router.post(
    "/{request_id}/cancel",
    response_model=PaymentRequestDetail,
    dependencies=[Depends(rate_limit_write)],
)
async def cancel_payment_request(
    request_id: int,
    body: CancelRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отменить заявку (PENDING_REVIEW/DRAFT → CANCELLED) — её заборы снова свободны."""
    service = PaymentRequestService(db)
    try:
        await service.cancel_request(project.id, request_id, comment=body.comment)
    except PaymentRequestValidationError as e:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "missing": e.missing})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    full = await service.get_request(project.id, request_id)
    return _to_detail(full)  # type: ignore[arg-type]


# ─── Согласование (только админ) ────────────────────────────────────────────────


@router.post(
    "/{request_id}/approve",
    response_model=PaymentRequestDetail,
    dependencies=[Depends(rate_limit_write), Depends(require_role("admin"))],
)
async def approve_payment_request(
    request_id: int,
    body: PaymentActionRequest,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Согласовать заявку (PENDING_REVIEW → APPROVED). Оплату админ проводит вне системы."""
    service = PaymentRequestService(db)
    try:
        await service.approve_request(project.id, request_id, comment=body.comment, changed_by=_actor(user))
    except PaymentRequestValidationError as e:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "missing": e.missing})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    full = await service.get_request(project.id, request_id)
    return _to_detail(full)  # type: ignore[arg-type]


@router.post(
    "/{request_id}/reject",
    response_model=PaymentRequestDetail,
    dependencies=[Depends(rate_limit_write), Depends(require_role("admin"))],
)
async def reject_payment_request(
    request_id: int,
    body: PaymentActionRequest,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отклонить заявку (PENDING_REVIEW/APPROVED → REJECTED)."""
    service = PaymentRequestService(db)
    try:
        await service.reject_request(project.id, request_id, comment=body.comment, changed_by=_actor(user))
    except PaymentRequestValidationError as e:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "missing": e.missing})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    full = await service.get_request(project.id, request_id)
    return _to_detail(full)  # type: ignore[arg-type]


@router.post(
    "/{request_id}/mark-paid",
    response_model=PaymentRequestDetail,
    dependencies=[Depends(rate_limit_write), Depends(require_role("admin"))],
)
async def mark_paid_payment_request(
    request_id: int,
    body: PaymentActionRequest,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отметить согласованную заявку оплаченной вручную (APPROVED → PAID)."""
    service = PaymentRequestService(db)
    try:
        await service.mark_paid_request(project.id, request_id, comment=body.comment, changed_by=_actor(user))
    except PaymentRequestValidationError as e:
        raise HTTPException(status_code=404, detail={"error": "NOT_FOUND", "missing": e.missing})
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    full = await service.get_request(project.id, request_id)
    return _to_detail(full)  # type: ignore[arg-type]


@router.post(
    "/{request_id}/create-draft",
    response_model=PaymentRequestDetail,
    dependencies=[Depends(rate_limit_write), Depends(require_role("admin"))],
)
async def create_payment_draft_endpoint(
    request_id: int,
    body: CreateDraftRequest,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """«Создать оплату» — РЕАЛЬНЫЙ черновик платёжки в банке. Необратимо; нужен confirm=true."""
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Требуется подтверждение (confirm=true): это реальная запись в банк")
    service = PaymentRequestService(db)
    pr = await service.get_request(project.id, request_id)
    if pr is None:
        raise HTTPException(status_code=404, detail="Заявка на оплату не найдена")
    try:
        await create_payment_draft(db, project.id, pr, payer_account_id=body.payer_account_id, changed_by=_actor(user))
    except PaymentDraftError as e:
        raise HTTPException(status_code=422, detail={"bank_errors": e.bank_errors})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FakturaApiError as e:
        raise HTTPException(status_code=502, detail=f"Банк недоступен: {e}")
    full = await service.get_request(project.id, request_id)
    return _to_detail(full)  # type: ignore[arg-type]


@router.post(
    "/create-drafts",
    response_model=CreateDraftsResponse,
    dependencies=[Depends(rate_limit_write), Depends(require_role("admin"))],
)
async def create_payment_drafts_bulk(
    body: CreateDraftsRequest,
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Массовое «Создать оплату» — РЕАЛЬНЫЕ черновики в банке по нескольким заявкам.

    Необратимо; нужен confirm=true. Идёт по заявкам последовательно, ошибка одной не
    останавливает остальные — результат по каждой в results.
    """
    if not body.confirm:
        raise HTTPException(
            status_code=400, detail="Требуется подтверждение (confirm=true): это реальная запись в банк"
        )
    service = PaymentRequestService(db)
    results: list[CreateDraftResult] = []
    for rid in dict.fromkeys(body.ids):  # дедуп, порядок сохраняем
        pr = await service.get_request(project.id, rid)
        if pr is None:
            results.append(CreateDraftResult(id=rid, ok=False, error="Заявка не найдена"))
            continue
        try:
            await create_payment_draft(
                db, project.id, pr, payer_account_id=body.payer_account_id, changed_by=_actor(user)
            )
            full = await service.get_request(project.id, rid)
            results.append(
                CreateDraftResult(id=rid, ok=True, bank_doc_id=(full.bank_doc_id if full else None))
            )
        except PaymentDraftError as e:
            await db.rollback()
            results.append(
                CreateDraftResult(
                    id=rid, ok=False,
                    error="; ".join(e.bank_errors) if e.bank_errors else "Ошибка валидации платёжки",
                )
            )
        except ValueError as e:
            await db.rollback()
            results.append(CreateDraftResult(id=rid, ok=False, error=str(e)))
        except FakturaApiError as e:
            await db.rollback()
            results.append(CreateDraftResult(id=rid, ok=False, error=f"Банк недоступен: {e}"))
    created = sum(1 for r in results if r.ok)
    return CreateDraftsResponse(results=results, created=created, failed=len(results) - created)


# ─── Распознавание реквизитов из счёта ──────────────────────────────────────────


@router.post(
    "/parse-invoice",
    response_model=InvoiceParseResult,
    dependencies=[Depends(rate_limit_write)],
)
async def parse_invoice_file(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
) -> InvoiceParseResult:
    """Распознать реквизиты получателя из файла счёта (PDF/Word/фото) — ПОДСКАЗКА для формы.

    В БД ничего не пишется (без project-скоупа). Поля, не прошедшие проверку
    (контроль-ключ р/с по БИК, БИК в справочнике), остаются None — вводятся вручную.
    PDF/Word разбираются текстовым Claude+regex, фото (jpg/png/webp/heic) — vision-Claude.
    """
    # Счёт: PDF/Word/фото. Валидация как у upload_payment_request_document:
    # allowlist MIME ИЛИ расширение (браузер часто шлёт пустой content_type на HEIC)
    # + блок исполняемых + magic-проверка ниже.
    content_type = (file.content_type or "").lower()
    filename_lower = (file.filename or "").lower()
    allowed_mime = (
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument",
        "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif",
    )
    allowed_ext = (
        ".pdf", ".doc", ".docx", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    )
    bad_ext = (".exe", ".bat", ".cmd", ".dll", ".sh", ".msi", ".ps1", ".com")
    ok_type = any(content_type.startswith(p) for p in allowed_mime) or filename_lower.endswith(allowed_ext)
    if filename_lower.endswith(bad_ext) or not ok_type:
        raise HTTPException(status_code=415, detail="Поддерживаются PDF, Word или фото счёта (JPG/PNG/HEIC)")

    # Размер режем ДО чтения тела в память (Content-Length), затем перестраховка по факту.
    max_bytes = min(_PR_DOC_MAX_MB, settings.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(status_code=413, detail=f"Файл слишком большой. Максимум: {_PR_DOC_MAX_MB} МБ")
    data = await file.read()
    validate_file_content(data, file.filename or "invoice")  # magic-bytes integrity по расширению
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Файл слишком большой. Максимум: {_PR_DOC_MAX_MB} МБ")

    return await invoice_parser.parse_invoice_async(data, file.filename or "invoice")


# ─── Documents ────────────────────────────────────────────────────────────────


@router.post(
    "/{request_id}/documents",
    response_model=PaymentRequestDocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit_write)],
)
async def upload_payment_request_document(
    request_id: int,
    file: UploadFile = File(...),
    doc_type: str = Form("INVOICE"),
    user: User = Depends(get_current_user),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    if doc_type not in ALLOWED_PR_DOC_TYPES:
        raise HTTPException(status_code=400, detail=f"doc_type must be one of: {ALLOWED_PR_DOC_TYPES}")

    # Счёт/акт: PDF, Word или фото (как и документы контрагента). Исполняемые — отсекаем.
    content_type = (file.content_type or "").lower()
    filename_lower = (file.filename or "").lower()
    allowed_mime = (
        "application/pdf",
        "image/",
        "application/msword",
        "application/vnd.openxmlformats-officedocument",
    )
    bad_ext = (".exe", ".bat", ".cmd", ".dll", ".sh", ".msi", ".ps1", ".com")
    if filename_lower.endswith(bad_ext) or not any(content_type.startswith(p) for p in allowed_mime):
        raise HTTPException(status_code=415, detail="Поддерживаются PDF, Word или фото (JPG/PNG)")

    data = await file.read()
    validate_file_content(data, file.filename or "document")  # magic-bytes integrity по расширению
    max_bytes = min(_PR_DOC_MAX_MB, settings.MAX_UPLOAD_SIZE_MB) * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Файл слишком большой. Максимум: {_PR_DOC_MAX_MB} МБ")

    doc = await docs_service.upload_document(
        db,
        project_id=project.id,
        request_id=request_id,
        file_data=data,
        filename=file.filename or "document",
        doc_type=doc_type,
        mime_type=file.content_type,
        uploaded_by_user_id=user.id,
    )
    return PaymentRequestDocumentResponse.model_validate(doc)


@router.get("/{request_id}/documents/{doc_id}/download")
async def download_payment_request_document(
    request_id: int,
    doc_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    data, filename, content_type = await docs_service.download_document(
        db, project_id=project.id, request_id=request_id, doc_id=doc_id
    )
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(content=data, media_type=content_type, headers={"Content-Disposition": disposition})


@router.delete("/{request_id}/documents/{doc_id}", dependencies=[Depends(rate_limit_write)])
async def delete_payment_request_document(
    request_id: int,
    doc_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    ok = await docs_service.delete_document(db, project_id=project.id, request_id=request_id, doc_id=doc_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Документ не найден")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
