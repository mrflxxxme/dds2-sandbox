# ruff: noqa: RUF002, RUF003
"""Файлы модуля дизайна: материалы, версии сдач, вердикты, скачивание.

MinIO-пути: design/{project_id}/{task_id}/materials/... и .../v{n}/... —
отдача только через бэк с проверкой project_id (§6.7), без presigned-URL
(донор payment_request_documents). Лимит 20 МБ, allowlist MIME, blocklist
исполняемых. HTTPException здесь — намеренно (валидация файлов и 503 MinIO,
как у донора); бизнес-ошибки — ValueError/PermissionError.
"""

import io
import logging
import mimetypes
import os
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.config import settings
from backend.models.design import (
    DesignMaterial,
    DesignMaterialKind,
    DesignSubmission,
    DesignSubmissionFile,
    DesignTask,
    DesignTaskStatus,
    DesignVerdict,
)
from backend.services.design.common import get_task_row, is_lead
from backend.services.design.state import _commit_and_notify, apply_transition_locked
from backend.storage import download_file, get_minio
from backend.utils.time import utcnow

logger = logging.getLogger("dds.design")

_S = DesignTaskStatus

MAX_FILE_MB = min(20, settings.MAX_UPLOAD_SIZE_MB)
MAX_FILES_PER_SUBMISSION = 10
MAX_SUBMISSION_TOTAL_MB = 100

_ALLOWED_MIME_EXACT = {
    "application/pdf",
    "application/zip",
    "application/x-zip-compressed",
    "application/x-rar-compressed",
    "application/vnd.rar",
}
_EXEC_BLOCKLIST = {
    ".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1",
    ".sh", ".js", ".vbs", ".jar", ".apk", ".app", ".py",
    # Активное содержимое при отдаче в браузер (XSS/XXE): svg/html/xml-семейство.
    ".svg", ".svgz", ".html", ".htm", ".xhtml", ".xml", ".mjs", ".php",
}

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def _sanitize_filename(filename: str) -> str:
    """Только имя, без путей и управляющих символов — защита от traversal и
    header-injection (iron rule безопасности)."""
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    name = _CONTROL_CHARS_RE.sub("", name)
    if not name or name in (".", ".."):
        raise HTTPException(400, "Некорректное имя файла")
    return name


def _validate_upload(file_data: bytes, filename: str, mime_type: str | None) -> str:
    """Размер → blocklist исполняемых → allowlist MIME. Возвращает итоговый MIME.

    Приоритет типа — расширение файла (mimetypes), клиентский MIME лишь фолбэк:
    клиентскому заголовку нельзя доверять (решение lead, вариант Б — только design).
    """
    if len(file_data) > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(400, f"Файл слишком большой (максимум {MAX_FILE_MB} МБ)")
    ext = os.path.splitext(filename)[1].lower()
    if ext in _EXEC_BLOCKLIST:
        raise HTTPException(400, "Исполняемые файлы запрещены")
    mime = mimetypes.guess_type(filename)[0] or mime_type
    if mime is None or not (mime.startswith("image/") or mime in _ALLOWED_MIME_EXACT):
        raise HTTPException(
            400, "Недопустимый тип файла. Разрешены: изображения, PDF, ZIP, RAR"
        )
    return mime


async def _put_object(object_name: str, data: bytes, content_type: str) -> None:
    client = await get_minio()
    if client is None:
        raise HTTPException(503, "Файловое хранилище (MinIO) недоступно")
    await client.put_object(
        settings.MINIO_BUCKET,
        object_name,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    logger.info("Design upload to MinIO: %s (%d bytes)", object_name, len(data))


# ─── Материалы (Р12) ─────────────────────────────────────────────────────────


async def upload_material_file(
    db: AsyncSession,
    *,
    project_id: int,
    task_id: int,
    file_data: bytes,
    filename: str,
    mime_type: str | None = None,
    user: Any,
) -> DesignMaterial:
    """validate_file_content → MinIO design/{project}/{task}/materials/{ts}_{name}.

    Валидация задачи коммитится ДО заливки: БД-транзакция не живёт через внешний
    HTTP (канон learnings «idle in transaction», решение lead — вариант А).
    """
    from backend.utils.file_validation import validate_file_content

    await get_task_row(db, project_id, task_id)
    name = _sanitize_filename(filename)
    mime = _validate_upload(file_data, name, mime_type)
    validate_file_content(file_data, name)
    await db.commit()  # закрыть autobegin-транзакцию валидации до похода в MinIO

    ts = utcnow().strftime("%Y%m%d_%H%M%S")
    object_name = f"design/{project_id}/{task_id}/materials/{ts}_{name}"
    await _put_object(object_name, file_data, mime)

    material = DesignMaterial(
        project_id=project_id,
        task_id=task_id,
        kind=DesignMaterialKind.FILE.value,
        minio_path=object_name,
        original_filename=name,
        mime_type=mime,
        file_size=len(file_data),
        created_by_user_id=user.id,
    )
    db.add(material)
    await db.commit()
    await db.refresh(material)
    return material


async def add_material_link(
    db: AsyncSession,
    *,
    project_id: int,
    task_id: int,
    url: str,
    caption: str | None = None,
    user: Any,
) -> DesignMaterial:
    await get_task_row(db, project_id, task_id)
    material = DesignMaterial(
        project_id=project_id,
        task_id=task_id,
        kind=DesignMaterialKind.LINK.value,
        url=str(url),
        caption=caption,
        created_by_user_id=user.id,
    )
    db.add(material)
    await db.commit()
    await db.refresh(material)
    return material


async def add_material_nm(
    db: AsyncSession,
    *,
    project_id: int,
    task_id: int,
    ref_nm_id: int,
    caption: str | None = None,
    user: Any,
) -> DesignMaterial:
    await get_task_row(db, project_id, task_id)
    material = DesignMaterial(
        project_id=project_id,
        task_id=task_id,
        kind=DesignMaterialKind.NM.value,
        ref_nm_id=ref_nm_id,
        caption=caption,
        created_by_user_id=user.id,
    )
    db.add(material)
    await db.commit()
    await db.refresh(material)
    return material


async def upload_comment_attachment(
    *,
    project_id: int,
    task_id: int,
    file_data: bytes,
    filename: str,
    mime_type: str | None = None,
) -> tuple[str, str]:
    """Вложение комментария → (minio_path, filename). Задачу валидирует вызывающий."""
    from backend.utils.file_validation import validate_file_content

    name = _sanitize_filename(filename)
    mime = _validate_upload(file_data, name, mime_type)
    validate_file_content(file_data, name)
    ts = utcnow().strftime("%Y%m%d_%H%M%S")
    object_name = f"design/{project_id}/{task_id}/comments/{ts}_{name}"
    await _put_object(object_name, file_data, mime)
    return object_name, name


# ─── Версии сдач ─────────────────────────────────────────────────────────────


async def create_submission(
    db: AsyncSession,
    *,
    project_id: int,
    task_id: int,
    files: list[tuple[bytes, str, str | None]],
    comment: str | None = None,
    user: Any,
    member_role: str,
) -> DesignSubmission:
    """Версия сдачи: version_no = max+1 под row-lock задачи (гонки не дублируют).

    Короткие транзакции (канон learnings, решение lead — вариант А): строка
    DesignSubmission резервирует version_no и КОММИТИТСЯ до заливки MinIO;
    файлы льются вне транзакции/лока; строки DesignSubmissionFile — второй
    короткой транзакцией. Провал заливки оставляет строку версии без файлов
    (гвард →REVIEW такую не пропустит), сироты MinIO — warning в лог.

    Вторая PENDING-версия запрещена — вердикт всегда однозначен.
    Файлы → design/{project}/{task}/v{n}/. Переход в REVIEW делает вызывающий
    отдельным change_status (гвард увидит созданную PENDING-версию с файлами).
    """
    from backend.utils.file_validation import validate_file_content

    task = await get_task_row(db, project_id, task_id, for_update=True)
    if not (is_lead(member_role) or task.assignee_user_id == user.id):
        raise PermissionError("Сдать версию может исполнитель задачи или ведущий")
    if task.status not in (_S.IN_PROGRESS.value, _S.REVISION.value):
        raise ValueError("Сдать версию можно только из статусов «В работе» или «Правки»")
    if not files:
        raise ValueError("Приложите хотя бы один файл")
    if len(files) > MAX_FILES_PER_SUBMISSION:
        raise HTTPException(400, f"Не больше {MAX_FILES_PER_SUBMISSION} файлов в одной версии")
    if sum(len(data) for data, _, _ in files) > MAX_SUBMISSION_TOTAL_MB * 1024 * 1024:
        raise HTTPException(
            400, f"Суммарный размер файлов превышает {MAX_SUBMISSION_TOTAL_MB} МБ"
        )

    prepared: list[tuple[bytes, str, str]] = []
    for data, filename, mime_type in files:  # валидация ВСЕХ файлов до заливки
        name = _sanitize_filename(filename)
        mime = _validate_upload(data, name, mime_type)
        validate_file_content(data, name)
        prepared.append((data, name, mime))

    pending_res = await db.execute(
        select(DesignSubmission.version_no)
        .where(
            DesignSubmission.project_id == project_id,
            DesignSubmission.task_id == task_id,
            DesignSubmission.verdict == DesignVerdict.PENDING.value,
        )
        .order_by(DesignSubmission.version_no.desc())
        .limit(1)
    )
    pending_version = pending_res.scalar_one_or_none()
    if pending_version is not None:
        raise ValueError(
            f"По задаче уже есть несданная версия {pending_version} — дождитесь вердикта"
        )

    version_res = await db.execute(
        select(func.coalesce(func.max(DesignSubmission.version_no), 0)).where(
            DesignSubmission.project_id == project_id,
            DesignSubmission.task_id == task_id,
        )
    )
    version_no = int(version_res.scalar() or 0) + 1

    submission = DesignSubmission(
        project_id=project_id,
        task_id=task_id,
        version_no=version_no,
        submitted_by_user_id=user.id,
        comment=comment,
        verdict=DesignVerdict.PENDING.value,
    )
    db.add(submission)
    await db.flush()
    submission_id = submission.id
    await db.commit()  # транзакция №1: строка версии; row-lock задачи отпущен

    # Заливка ВНЕ транзакции/лока — БД-коннект не висит через внешний HTTP.
    ts = utcnow().strftime("%Y%m%d_%H%M%S")
    uploaded: list[tuple[str, str, str, int]] = []  # (object_name, name, mime, size)
    try:
        for idx, (data, name, mime) in enumerate(prepared):
            object_name = f"design/{project_id}/{task_id}/v{version_no}/{ts}_{idx}_{name}"
            await _put_object(object_name, data, mime)
            uploaded.append((object_name, name, mime, len(data)))
    except Exception as e:
        logger.warning(
            "design create_submission: заливка версии %d задачи %d сорвалась "
            "(%d/%d файлов), сироты MinIO: %s — %s",
            version_no, task_id, len(uploaded), len(prepared),
            [u[0] for u in uploaded], e,
        )
        raise ValueError("Версия создана, повторите загрузку файлов") from e

    for object_name, name, mime, size in uploaded:  # транзакция №2: строки файлов
        db.add(
            DesignSubmissionFile(
                project_id=project_id,
                submission_id=submission_id,
                minio_path=object_name,
                original_filename=name,
                mime_type=mime,
                file_size=size,
            )
        )
    await db.commit()
    res = await db.execute(
        select(DesignSubmission)
        .options(selectinload(DesignSubmission.files))
        .where(
            DesignSubmission.id == submission_id,
            DesignSubmission.project_id == project_id,
        )
    )
    return res.scalar_one()


async def set_verdict(
    db: AsyncSession,
    *,
    project_id: int,
    task_id: int,
    submission_id: int,
    verdict: str,
    verdict_comment: str | None = None,
    user: Any,
    member_role: str,
) -> DesignSubmission:
    """Вердикт по PENDING-версии (только автор/lead, задача в REVIEW).

    Вердикт применяется К ПЕРЕДАННОЙ версии: submission_id прокидывается в
    apply_transition_locked, где живут ОБА эффекта (REVISION → REJECTED c
    verdict_comment, ACCEPTED → закрытие) — единственное место, без задвоения.
    Фолбэк «последняя PENDING» — только для прямого change_status без версии.
    """
    task = await get_task_row(db, project_id, task_id, for_update=True)
    if not (is_lead(member_role) or task.author_user_id == user.id):
        raise PermissionError("Вердикт выносит автор заявки или ведущий")
    if task.status != _S.REVIEW.value:
        raise ValueError("Вердикт выносится только по задаче на проверке")

    sub_res = await db.execute(
        select(DesignSubmission).where(
            DesignSubmission.id == submission_id,
            DesignSubmission.project_id == project_id,
            DesignSubmission.task_id == task_id,
        )
    )
    submission = sub_res.scalar_one_or_none()
    if submission is None:
        raise ValueError("Версия сдачи не найдена")
    if submission.verdict != DesignVerdict.PENDING.value:
        raise ValueError("Вердикт уже вынесен — версия не в статусе PENDING")

    if verdict == DesignVerdict.REJECTED.value:
        if not verdict_comment or not verdict_comment.strip():
            raise ValueError("Напишите, что исправить")
        await apply_transition_locked(
            db, task, _S.REVISION, user, member_role,
            comment=verdict_comment, submission_id=submission.id,
        )
    elif verdict == DesignVerdict.ACCEPTED.value:
        await apply_transition_locked(
            db, task, _S.ACCEPTED, user, member_role,
            comment=verdict_comment, submission_id=submission.id,
        )
    else:
        raise ValueError("Вердикт должен быть ACCEPTED или REJECTED")

    await _commit_and_notify(db)
    await db.refresh(submission)
    return submission


# ─── Скачивание (строго project-scoped, §6.7) ────────────────────────────────


async def download_material(
    db: AsyncSession, *, project_id: int, task_id: int, material_id: int
) -> tuple[bytes, str, str]:
    """(bytes, filename, content_type); 404 вне проекта, 503 при недоступном MinIO.

    Join на живую DesignTask: вложения удалённой задачи недоступны (решение lead).
    """
    res = await db.execute(
        select(DesignMaterial)
        .join(DesignTask, DesignTask.id == DesignMaterial.task_id)
        .where(
            DesignMaterial.id == material_id,
            DesignMaterial.project_id == project_id,
            DesignMaterial.task_id == task_id,
            DesignMaterial.kind == DesignMaterialKind.FILE.value,
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
        )
    )
    material = res.scalar_one_or_none()
    if material is None or not material.minio_path:
        raise HTTPException(404, "Материал не найден")
    data = await download_file(material.minio_path)
    if data is None:
        raise HTTPException(503, "Файл недоступен (MinIO)")
    return (
        data,
        material.original_filename or "file",
        material.mime_type or "application/octet-stream",
    )


async def download_submission_file(
    db: AsyncSession, *, project_id: int, task_id: int, file_id: int
) -> tuple[bytes, str, str]:
    """Файл версии сдачи — с проверкой принадлежности задаче и проекту.

    Join на живую DesignTask: вложения удалённой задачи недоступны (решение lead).
    """
    res = await db.execute(
        select(DesignSubmissionFile)
        .join(DesignSubmission, DesignSubmission.id == DesignSubmissionFile.submission_id)
        .join(DesignTask, DesignTask.id == DesignSubmission.task_id)
        .where(
            DesignSubmissionFile.id == file_id,
            DesignSubmissionFile.project_id == project_id,
            DesignSubmission.project_id == project_id,
            DesignSubmission.task_id == task_id,
            DesignTask.project_id == project_id,
            DesignTask.is_deleted == False,  # noqa: E712
        )
    )
    file_row = res.scalar_one_or_none()
    if file_row is None:
        raise HTTPException(404, "Файл не найден")
    data = await download_file(file_row.minio_path)
    if data is None:
        raise HTTPException(503, "Файл недоступен (MinIO)")
    return (
        data,
        file_row.original_filename or "file",
        file_row.mime_type or "application/octet-stream",
    )
