"""
MinIO (S3-compatible) async storage client for DDS file management.
Stores original uploaded files for audit trail and re-import capability.

Uses miniopy-async for non-blocking I/O (compatible with FastAPI async handlers).
"""

import io
import logging
from datetime import datetime, timezone

from backend.utils.time import utcnow
from typing import Optional

from miniopy_async import Minio
from miniopy_async.error import S3Error

from backend.config import settings

logger = logging.getLogger(__name__)

# Lazy MinIO client
_minio_client: Optional[Minio] = None


async def get_minio() -> Optional[Minio]:
    """Get or create async MinIO client."""
    global _minio_client
    if _minio_client is None:
        try:
            _minio_client = Minio(
                settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE,
            )
            # Ensure bucket exists
            bucket = settings.MINIO_BUCKET
            if not await _minio_client.bucket_exists(bucket):
                await _minio_client.make_bucket(bucket)
                logger.info("Created MinIO bucket: %s", bucket)
            logger.info("MinIO connected: %s", settings.MINIO_ENDPOINT)
        except Exception as e:
            logger.warning("MinIO unavailable (%s), file storage disabled", e)
            _minio_client = None
    return _minio_client


async def upload_file(
    data: bytes,
    filename: str,
    source_type: str = "",
    content_type: str = "application/octet-stream",
) -> Optional[str]:
    """
    Upload a file to MinIO (async).

    Args:
        data: File content as bytes
        filename: Original filename
        source_type: Type of source (e.g. VTB_RUB_MAIN)
        content_type: MIME type

    Returns:
        Object path in MinIO (e.g. "imports/2026/02/VTB_RUB_MAIN/filename.xlsx")
        or None if MinIO is unavailable.
    """
    client = await get_minio()
    if client is None:
        return None

    # Organize files by date and source type
    now = utcnow()
    prefix = f"imports/{now.strftime('%Y/%m')}"
    if source_type:
        prefix = f"{prefix}/{source_type}"
    object_name = f"{prefix}/{now.strftime('%d_%H%M%S')}_{filename}"

    try:
        await client.put_object(
            settings.MINIO_BUCKET,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        logger.info("Uploaded to MinIO: %s (%d bytes)", object_name, len(data))
        return object_name
    except S3Error as e:
        logger.error("MinIO upload error: %s", e)
        return None


async def download_file(object_name: str) -> Optional[bytes]:
    """
    Download a file from MinIO (async).

    Args:
        object_name: Object path in MinIO

    Returns:
        File content as bytes, or None if not found / unavailable.
    """
    client = await get_minio()
    if client is None:
        return None

    try:
        response = await client.get_object(settings.MINIO_BUCKET, object_name)
        data = await response.read()
        response.close()
        response.release_conn()
        return data
    except S3Error as e:
        logger.error("MinIO download error: %s", e)
        return None


async def list_files(prefix: str = "imports/") -> list[dict]:
    """
    List files in MinIO bucket under a prefix (async).

    Returns:
        List of {name, size, last_modified} dicts.
    """
    client = await get_minio()
    if client is None:
        return []

    try:
        objects = await client.list_objects(settings.MINIO_BUCKET, prefix=prefix, recursive=True)
        return [
            {
                "name": obj.object_name,
                "size": obj.size,
                "last_modified": obj.last_modified.isoformat() if obj.last_modified else None,
            }
            for obj in objects
        ]
    except S3Error as e:
        logger.error("MinIO list error: %s", e)
        return []
