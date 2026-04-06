"""
Supply Chain — Vehicle document management (upload, list, download, delete).
Files stored in MinIO, metadata in DB (VehicleDocument).
"""

import io
import logging
import mimetypes

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.models.cost import CostOrder
from backend.models.supply_chain import VehicleDocument
from backend.storage import get_minio
from backend.utils.time import utcnow

logger = logging.getLogger(__name__)


async def upload_document(
    db: AsyncSession,
    project_id: int,
    order_no: str,
    file_data: bytes,
    filename: str,
    doc_type: str,
    note: str | None = None,
) -> VehicleDocument:
    """Upload document to MinIO and save metadata in DB."""
    # Verify vehicle exists and belongs to project
    result = await db.execute(
        select(CostOrder).where(
            CostOrder.order_no == order_no,
            CostOrder.project_id == project_id,
            CostOrder.is_deleted == False,  # noqa: E712
        )
    )
    vehicle = result.scalar_one_or_none()
    if not vehicle:
        raise ValueError(f"Vehicle {order_no} not found")

    # Upload to MinIO with custom path
    now = utcnow()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    object_name = f"vehicles/{order_no}/docs/{timestamp}_{filename}"

    client = await get_minio()
    if client is None:
        raise ValueError("File storage (MinIO) is unavailable")

    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    await client.put_object(
        settings.MINIO_BUCKET,
        object_name,
        io.BytesIO(file_data),
        length=len(file_data),
        content_type=content_type,
    )
    logger.info("Uploaded vehicle doc to MinIO: %s (%d bytes)", object_name, len(file_data))

    # Create DB record
    doc = VehicleDocument(
        project_id=project_id,
        order_no=order_no,
        doc_type=doc_type,
        filename=filename,
        file_url=object_name,
        file_size=len(file_data),
        note=note,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def list_documents(
    db: AsyncSession,
    project_id: int,
    order_no: str,
) -> list[VehicleDocument]:
    """List all non-deleted documents for a vehicle."""
    result = await db.execute(
        select(VehicleDocument)
        .where(
            VehicleDocument.project_id == project_id,
            VehicleDocument.order_no == order_no,
            VehicleDocument.is_deleted == False,  # noqa: E712
        )
        .order_by(VehicleDocument.created_at.desc())
        .limit(200)
    )
    return list(result.scalars().all())


async def delete_document(
    db: AsyncSession,
    project_id: int,
    doc_id: int,
) -> bool:
    """Soft-delete a document."""
    result = await db.execute(
        select(VehicleDocument).where(
            VehicleDocument.id == doc_id,
            VehicleDocument.project_id == project_id,
            VehicleDocument.is_deleted == False,  # noqa: E712
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        return False
    doc.soft_delete()
    await db.commit()
    return True


async def download_document(
    db: AsyncSession,
    project_id: int,
    doc_id: int,
) -> tuple[bytes, str, str]:
    """Download file from MinIO. Return (bytes, filename, content_type)."""
    result = await db.execute(
        select(VehicleDocument).where(
            VehicleDocument.id == doc_id,
            VehicleDocument.project_id == project_id,
            VehicleDocument.is_deleted == False,  # noqa: E712
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise ValueError("Document not found")

    client = await get_minio()
    if client is None:
        raise ValueError("File storage (MinIO) is unavailable")

    response = await client.get_object(settings.MINIO_BUCKET, doc.file_url)
    data = await response.read()
    response.close()
    response.release_conn()

    content_type = mimetypes.guess_type(doc.filename)[0] or "application/octet-stream"
    return data, doc.filename, content_type
