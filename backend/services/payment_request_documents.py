# ruff: noqa: RUF002, RUF003
"""
Payment-request documents — счёт/акт в MinIO (зеркало counterparty_service docs).

Скачивание стримим ЧЕРЕЗ бэк (download_file → bytes), без presigned-URL — чтобы
доступ был строго project-scoped (никакой утечки прямой ссылки на объект).
"""

import io
import logging
import mimetypes

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.payment_request import PaymentRequest, PaymentRequestDocument
from backend.storage import download_file, get_minio
from backend.utils.time import utcnow

logger = logging.getLogger("dds.payment_request")


async def _get_request(db: AsyncSession, project_id: int, request_id: int) -> PaymentRequest:
    res = await db.execute(
        select(PaymentRequest).where(
            PaymentRequest.id == request_id,
            # свой проект ИЛИ общая (project_id IS NULL) — доступ к заявке авторизует и её документы.
            or_(PaymentRequest.project_id == project_id, PaymentRequest.project_id.is_(None)),
            PaymentRequest.is_deleted == False,  # noqa: E712
        )
    )
    pr = res.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=404, detail="Заявка на оплату не найдена")
    return pr


async def upload_document(
    db: AsyncSession,
    *,
    project_id: int,
    request_id: int,
    file_data: bytes,
    filename: str,
    doc_type: str,
    mime_type: str | None = None,
    uploaded_by_user_id: int | None = None,
) -> PaymentRequestDocument:
    """Upload a счёт/акт to MinIO and create the metadata row (request must belong to project)."""
    pr = await _get_request(db, project_id, request_id)

    now = utcnow()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    object_name = f"payment_requests/{request_id}/docs/{timestamp}_{filename}"
    content_type = mime_type or (mimetypes.guess_type(filename)[0] or "application/octet-stream")

    client = await get_minio()
    if client is None:
        raise HTTPException(status_code=503, detail="File storage (MinIO) unavailable")

    await client.put_object(
        settings.MINIO_BUCKET,
        object_name,
        io.BytesIO(file_data),
        length=len(file_data),
        content_type=content_type,
    )
    logger.info("Uploaded payment-request doc to MinIO: %s (%d bytes)", object_name, len(file_data))

    doc = PaymentRequestDocument(
        project_id=pr.project_id,  # зеркалит проект заявки (NULL у общей)
        payment_request_id=request_id,
        minio_path=object_name,
        doc_type=doc_type,
        original_filename=filename,
        file_size=len(file_data),
        mime_type=content_type,
        uploaded_by_user_id=uploaded_by_user_id,
        uploaded_at=now,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def download_document(
    db: AsyncSession, *, project_id: int, request_id: int, doc_id: int
) -> tuple[bytes, str, str]:
    """Return (bytes, filename, content_type). Project-scoped; raises 404 if not found, 503 if MinIO down."""
    await _get_request(db, project_id, request_id)  # авторизация доступа к заявке (свой проект / общая)
    res = await db.execute(
        select(PaymentRequestDocument).where(
            PaymentRequestDocument.id == doc_id,
            PaymentRequestDocument.payment_request_id == request_id,
            PaymentRequestDocument.is_deleted == False,  # noqa: E712
        )
    )
    doc = res.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="Документ не найден")

    data = await download_file(doc.minio_path)
    if data is None:
        raise HTTPException(status_code=503, detail="Файл недоступен (MinIO)")
    return data, doc.original_filename or "document", doc.mime_type or "application/octet-stream"


async def delete_document(db: AsyncSession, *, project_id: int, request_id: int, doc_id: int) -> bool:
    """Soft-delete a doc; best-effort remove from MinIO."""
    await _get_request(db, project_id, request_id)  # авторизация доступа к заявке (свой проект / общая)
    res = await db.execute(
        select(PaymentRequestDocument).where(
            PaymentRequestDocument.id == doc_id,
            PaymentRequestDocument.payment_request_id == request_id,
            PaymentRequestDocument.is_deleted == False,  # noqa: E712
        )
    )
    doc = res.scalar_one_or_none()
    if doc is None:
        return False
    doc.soft_delete()
    await db.commit()

    try:
        client = await get_minio()
        if client is not None:
            await client.remove_object(settings.MINIO_BUCKET, doc.minio_path)
    except Exception as e:
        logger.warning("MinIO remove_object failed: %s", e)
    return True
