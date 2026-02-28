"""
MinIO (S3-compatible) storage client for DDS file management.
Stores original uploaded files for audit trail and re-import capability.
"""

import io
import logging
from datetime import datetime
from typing import Optional

from minio import Minio
from minio.error import S3Error

from backend.config import settings

logger = logging.getLogger(__name__)

# Lazy MinIO client
_minio_client: Optional[Minio] = None


def get_minio() -> Optional[Minio]:
    """Get or create MinIO client."""
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
            if not _minio_client.bucket_exists(bucket):
                _minio_client.make_bucket(bucket)
                logger.info("Created MinIO bucket: %s", bucket)
            logger.info("MinIO connected: %s", settings.MINIO_ENDPOINT)
        except Exception as e:
            logger.warning("MinIO unavailable (%s), file storage disabled", e)
            _minio_client = None
    return _minio_client


def upload_file(
    data: bytes,
    filename: str,
    source_type: str = "",
    content_type: str = "application/octet-stream",
) -> Optional[str]:
    """
    Upload a file to MinIO.

    Args:
        data: File content as bytes
        filename: Original filename
        source_type: Type of source (e.g. VTB_RUB_MAIN)
        content_type: MIME type

    Returns:
        Object path in MinIO (e.g. "imports/2026/02/VTB_RUB_MAIN/filename.xlsx")
        or None if MinIO is unavailable.
    """
    client = get_minio()
    if client is None:
        return None

    # Organize files by date and source type
    now = datetime.utcnow()
    prefix = f"imports/{now.strftime('%Y/%m')}"
    if source_type:
        prefix = f"{prefix}/{source_type}"
    object_name = f"{prefix}/{now.strftime('%d_%H%M%S')}_{filename}"

    try:
        client.put_object(
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


def download_file(object_name: str) -> Optional[bytes]:
    """
    Download a file from MinIO.

    Args:
        object_name: Object path in MinIO

    Returns:
        File content as bytes, or None if not found / unavailable.
    """
    client = get_minio()
    if client is None:
        return None

    try:
        response = client.get_object(settings.MINIO_BUCKET, object_name)
        data = response.read()
        response.close()
        response.release_conn()
        return data
    except S3Error as e:
        logger.error("MinIO download error: %s", e)
        return None


def list_files(prefix: str = "imports/") -> list[dict]:
    """
    List files in MinIO bucket under a prefix.

    Returns:
        List of {name, size, last_modified} dicts.
    """
    client = get_minio()
    if client is None:
        return []

    try:
        objects = client.list_objects(settings.MINIO_BUCKET, prefix=prefix, recursive=True)
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
