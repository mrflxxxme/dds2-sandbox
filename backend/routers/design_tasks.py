# ruff: noqa: RUF002, RUF003
"""Router: /design-tasks — модуль «Дизайн карточек» (Ф2, контракт в CONTRACT.md).

Thin HTTP-слой над backend/services/design/ (Ф1): роутер только транслирует
ошибки сервиса (PermissionError → 403, ValueError → 400/404) и валидирует вход.
RBAC на бэке (Р11): каждая ручка — require_role(..., page="design-tasks");
тонкая доводка прав (author/assignee/lead) — в сервисе. Кэша нет (Р10).

Порядок объявления: статические пути ДО /{task_id} (FastAPI матчит по порядку).
Единственная ручка без get_current_project — GET /all-projects (скоуп — членство
ProjectMember, §6.1).
"""

import logging
import mimetypes
import os
from collections.abc import Awaitable
from datetime import date
from typing import TypeVar
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models.auth import Project, ProjectMember, User
from backend.models.design import DesignTaskStatus, DesignWorkType
from backend.project_context import get_current_project
from backend.rbac import require_role
from backend.schemas.design import (
    DesignAbTestIn,
    DesignAbTestOut,
    DesignAssign,
    DesignBoardResponse,
    DesignCalendarOut,
    DesignCommentIn,
    DesignCommentOut,
    DesignMaterialIn,
    DesignMaterialOut,
    DesignMoveIn,
    DesignProductSuggestion,
    DesignStatsOut,
    DesignStatusChange,
    DesignTaskCreate,
    DesignTaskDetail,
    DesignTaskListItem,
    DesignTaskUpdate,
    DesignVerdictIn,
    DesignWorkloadRow,
)
from backend.services.design import ab_bridge, board, crud, queries, state, stats, workload
from backend.services.design import files as design_files
from backend.utils.rate_limit import rate_limit_write

logger = logging.getLogger("dds.design")

router = APIRouter(prefix="/design-tasks")

require_viewer = require_role("viewer", page="design-tasks")
require_editor = require_role("editor", page="design-tasks")

# Тексты ValueError сервиса, означающие «ресурса нет в скоупе проекта» → 404;
# остальные ValueError — невалидная операция/гвард → 400 (соглашение пакета Ф1).
_NOT_FOUND_TEXTS = frozenset({"Задача не найдена", "Версия сдачи не найдена"})

_T = TypeVar("_T")


async def _svc(call: Awaitable[_T]) -> _T:
    """Единый маппинг ошибок сервиса в HTTP (HTTPException files.py — сквозной)."""
    try:
        return await call
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        msg = str(e)
        raise HTTPException(404 if msg in _NOT_FOUND_TEXTS else 400, msg) from e


async def get_member_role(
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> str:
    """member_role для матрицы прав сервиса (lead = owner/admin, CHARTER §1)."""
    res = await db.execute(
        select(ProjectMember.role)
        .where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
            ProjectMember.is_deleted == False,  # noqa: E712
        )
        .limit(1)
    )
    role = res.scalar_one_or_none()
    if role is None:
        raise HTTPException(403, "Нет доступа к проекту")
    return role


def _attachment(data: bytes, filename: str, content_type: str) -> Response:
    """Отдача файла строго вложением: attachment + nosniff (решение F1 «вариант Б»)."""
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _precheck_upload_type(filename: str | None, content_type: str | None) -> None:
    """Дешёвые проверки типа ДО чтения тела (донор payment_requests.py:710):
    allowlist mime/расширения → blocklist исполняемых. Magic-bytes — в сервисе."""
    name = (filename or "").lower()
    ext = os.path.splitext(name)[1]
    mime = mimetypes.guess_type(name)[0] or (content_type or "").lower() or None
    if mime is None or mime.lower() in design_files.BLOCKED_MIME_EXACT or not (
        mime.startswith("image/") or mime in design_files.ALLOWED_MIME_EXACT
    ):
        raise HTTPException(400, "Недопустимый тип файла. Разрешены: изображения, PDF, ZIP, RAR")
    if ext in design_files.EXEC_BLOCKLIST:
        raise HTTPException(400, "Исполняемые файлы запрещены")


def _check_file_size(size: int | None) -> None:
    if size is not None and size > design_files.MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"Файл слишком большой. Максимум: {design_files.MAX_FILE_MB} МБ")


async def _detail(
    db: AsyncSession, project_id: int, task_id: int, user: User, member_role: str
) -> DesignTaskDetail:
    """Свежая деталка после мутации — единый формат ответа write-ручек задачи."""
    return await _svc(queries.get_task(db, project_id, task_id, user, member_role))


def _status_values(status: list[DesignTaskStatus] | None) -> list[str] | None:
    """Enum-валидация — на Query (невалидный статус → 422 автоматом, fix-цикл Ф2)."""
    return [s.value for s in status] if status else None


# ─── Статические пути (ДО /{task_id}) ────────────────────────────────────────


@router.get("/board", response_model=DesignBoardResponse)
async def get_board(
    user: User = Depends(require_viewer),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Доска: 6 колонок одним ответом + counts по всем статусам (Р4)."""
    return await board.get_board(db, project.id, user, member_role)


@router.get("/all-projects", response_model=list[DesignTaskListItem])
async def list_all_projects(
    status: list[DesignTaskStatus] | None = Query(None),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Сквозной список по брендам — ТОЛЬКО проекты членства ProjectMember (§6.1).

    Единственная ручка без get_current_project; page-гейт применяется в сервисе:
    включаются лишь проекты, где участнику доступна страница design-tasks
    (owner/admin — всегда; editor/viewer — по ключу в pages, решение fix-цикла Ф2).
    """
    return await queries.list_tasks_all_projects(
        db, user.id, status=_status_values(status), limit=limit, offset=offset
    )


@router.get("/workload", response_model=list[DesignWorkloadRow])
async def get_workload(
    user: User = Depends(require_viewer),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Экран «Загрузка команды»: активные/просроченные/в правках по исполнителям."""
    return await workload.get_workload(db, project.id)


@router.get("/calendar", response_model=DesignCalendarOut)
async def get_calendar(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$", description="YYYY-MM"),
    user: User = Depends(require_viewer),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Задачи месяца по due_date; границы видимой сетки ±6 дней (Р7)."""
    try:
        month_first = date.fromisoformat(f"{month}-01")
    except ValueError as e:
        raise HTTPException(400, "month должен быть в формате YYYY-MM") from e
    date_from, date_to, tasks = await queries.list_calendar(db, project.id, month_first)
    return DesignCalendarOut(month=month, date_from=date_from, date_to=date_to, tasks=tasks)


@router.get("/stats", response_model=DesignStatsOut)
async def get_stats(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    user: User = Depends(require_viewer),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Метрики PRD §10 (панель метрик — потребитель Ф6)."""
    return await stats.get_stats(db, project.id, date_from, date_to)


@router.get("/product-suggest", response_model=list[DesignProductSuggestion])
async def product_suggest(
    q: str = Query(..., min_length=1, max_length=100),
    user: User = Depends(require_editor),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Автоподсказка товара по Nomenclature (Р2): nm_id = article_wb."""
    return await queries.product_suggest(db, project.id, q)


# ─── Список / создание ───────────────────────────────────────────────────────


@router.get("", response_model=list[DesignTaskListItem])
async def list_tasks(
    status: list[DesignTaskStatus] | None = Query(None),
    assignee_user_id: int | None = Query(None),
    author_user_id: int | None = Query(None),
    work_type: DesignWorkType | None = Query(None),
    is_urgent: bool | None = Query(None),
    overdue: bool | None = Query(None),
    q: str | None = Query(None, max_length=100),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_viewer),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Список задач (второй вид) с фильтрами; без фильтра статуса видны и ON_HOLD/CANCELLED.

    Невалидный enum в status/work_type → 422 (валидация Query, fix-цикл Ф2).
    """
    return await queries.list_tasks(
        db,
        project.id,
        status=_status_values(status),
        assignee_user_id=assignee_user_id,
        author_user_id=author_user_id,
        work_type=work_type.value if work_type else None,
        is_urgent=is_urgent,
        overdue=overdue,
        q=q,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=DesignTaskDetail,
    status_code=201,
    dependencies=[Depends(rate_limit_write)],
)
async def create_task(
    payload: DesignTaskCreate,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать заявку (менеджер): статус NEW, в конец колонки, номер DES-N."""
    task = await _svc(crud.create_task(db, project.id, payload, user))
    return await _detail(db, project.id, task.id, user, member_role)


# ─── Задача ──────────────────────────────────────────────────────────────────


@router.get("/{task_id}", response_model=DesignTaskDetail)
async def get_task(
    task_id: int,
    user: User = Depends(require_viewer),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Деталка: шапка + материалы, сдачи, комментарии, журнал + permissions (§6.9)."""
    return await _detail(db, project.id, task_id, user, member_role)


@router.put("/{task_id}", response_model=DesignTaskDetail, dependencies=[Depends(rate_limit_write)])
async def update_task(
    task_id: int,
    payload: DesignTaskUpdate,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """PATCH-семантика (правила прав — в сервисе: lead везде, автор вне REVIEW/терминалов)."""
    await _svc(crud.update_task(db, project.id, task_id, payload, user, member_role))
    return await _detail(db, project.id, task_id, user, member_role)


@router.delete("/{task_id}", status_code=204, dependencies=[Depends(rate_limit_write)])
async def delete_task(
    task_id: int,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Мягкое удаление (soft_delete, автор|lead); номер DES-N не переиспользуется."""
    await _svc(crud.delete_task(db, project.id, task_id, user, member_role))
    return Response(status_code=204)


@router.post(
    "/{task_id}/status", response_model=DesignTaskDetail, dependencies=[Depends(rate_limit_write)]
)
async def change_status(
    task_id: int,
    payload: DesignStatusChange,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Смена статуса из деталки (кнопочный путь): словарь переходов + гварды Ф1."""
    await _svc(
        state.change_status(db, project.id, task_id, payload.to_status, user, member_role, payload.comment)
    )
    return await _detail(db, project.id, task_id, user, member_role)


@router.post(
    "/{task_id}/move", response_model=DesignTaskDetail, dependencies=[Depends(rate_limit_write)]
)
async def move_task(
    task_id: int,
    payload: DesignMoveIn,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Dnd (Р4): статус + позиция; reorder внутри колонки — только lead (Р9)."""
    await _svc(
        board.move_task(
            db,
            project.id,
            task_id,
            payload.to_status,
            payload.after_task_id,
            user,
            member_role,
            comment=payload.comment,
        )
    )
    return await _detail(db, project.id, task_id, user, member_role)


@router.post(
    "/{task_id}/assign", response_model=DesignTaskDetail, dependencies=[Depends(rate_limit_write)]
)
async def assign_task(
    task_id: int,
    payload: DesignAssign,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Назначение/снятие исполнителя (lead-only — проверка в сервисе)."""
    await _svc(crud.assign(db, project.id, task_id, payload.assignee_user_id, user, member_role))
    return await _detail(db, project.id, task_id, user, member_role)


@router.post(
    "/{task_id}/viewed", response_model=DesignTaskDetail, dependencies=[Depends(rate_limit_write)]
)
async def mark_viewed(
    task_id: int,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Отметка просмотра лидом (Р5): идемпотентно снимает красную метку."""
    await _svc(crud.mark_viewed(db, project.id, task_id, user, member_role))
    return await _detail(db, project.id, task_id, user, member_role)


# ─── Материалы (Р12) ─────────────────────────────────────────────────────────


@router.post(
    "/{task_id}/materials",
    response_model=DesignMaterialOut,
    status_code=201,
    dependencies=[Depends(rate_limit_write)],
)
async def add_material(
    task_id: int,
    payload: DesignMaterialIn,
    user: User = Depends(require_editor),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Материал: LINK (url) или NM (ref_nm_id); FILE — отдельной multipart-ручкой."""
    if payload.kind == "LINK":
        material = await _svc(
            design_files.add_material_link(
                db,
                project_id=project.id,
                task_id=task_id,
                url=str(payload.url),
                caption=payload.caption,
                user=user,
            )
        )
    else:  # NM (kind=FILE отсечён валидатором схемы)
        material = await _svc(
            design_files.add_material_nm(
                db,
                project_id=project.id,
                task_id=task_id,
                ref_nm_id=payload.ref_nm_id or 0,
                caption=payload.caption,
                user=user,
            )
        )
    return DesignMaterialOut.model_validate(material)


@router.post(
    "/{task_id}/materials/file",
    response_model=DesignMaterialOut,
    status_code=201,
    dependencies=[Depends(rate_limit_write)],
)
async def upload_material_file(
    task_id: int,
    file: UploadFile = File(...),
    user: User = Depends(require_editor),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Материал файлом (донор pr:710): allowlist → blocklist → read →
    validate_file_content (в сервисе) → размер 413."""
    _precheck_upload_type(file.filename, file.content_type)
    _check_file_size(file.size)
    data = await file.read()
    _check_file_size(len(data))
    material = await _svc(
        design_files.upload_material_file(
            db,
            project_id=project.id,
            task_id=task_id,
            file_data=data,
            filename=file.filename or "file",
            mime_type=file.content_type,
            user=user,
        )
    )
    return DesignMaterialOut.model_validate(material)


@router.get(
    "/{task_id}/materials/{mat_id}/file",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_material(
    task_id: int,
    mat_id: int,
    user: User = Depends(require_viewer),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Скачать материал: attachment + nosniff; 404 вне проекта, 503 без MinIO."""
    data, filename, content_type = await _svc(
        design_files.download_material(
            db, project_id=project.id, task_id=task_id, material_id=mat_id
        )
    )
    return _attachment(data, filename, content_type)


@router.delete("/{task_id}/materials/{mat_id}", status_code=204, dependencies=[Depends(rate_limit_write)])
async def delete_material(
    task_id: int,
    mat_id: int,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Удалить материал (его автор, автор заявки или lead)."""
    await _svc(
        design_files.delete_material(
            db,
            project_id=project.id,
            task_id=task_id,
            material_id=mat_id,
            user=user,
            member_role=member_role,
        )
    )
    return Response(status_code=204)


# ─── Версии сдач ─────────────────────────────────────────────────────────────


@router.post(
    "/{task_id}/submissions",
    response_model=DesignTaskDetail,
    status_code=201,
    dependencies=[Depends(rate_limit_write)],
)
async def create_submission(
    task_id: int,
    files: list[UploadFile] = File(...),
    comment: str | None = Form(None),
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Сдать версию (multipart): создаёт PENDING-версию с файлами и переводит в REVIEW.

    Каповки на входе роутера (сервис дублирует): ≤10 файлов, каждый ≤20 МБ (413),
    суммарно ≤100 МБ (413); порядок проверок каждого файла — донор pr:710.
    """
    if len(files) > design_files.MAX_FILES_PER_SUBMISSION:
        raise HTTPException(
            400, f"Не больше {design_files.MAX_FILES_PER_SUBMISSION} файлов в одной версии"
        )
    max_total = design_files.MAX_SUBMISSION_TOTAL_MB * 1024 * 1024
    prepared: list[tuple[bytes, str, str | None]] = []
    total = 0
    for f in files:
        _precheck_upload_type(f.filename, f.content_type)
        _check_file_size(f.size)
        data = await f.read()
        _check_file_size(len(data))
        total += len(data)
        if total > max_total:
            raise HTTPException(
                413,
                f"Суммарный размер файлов превышает {design_files.MAX_SUBMISSION_TOTAL_MB} МБ",
            )
        prepared.append((data, f.filename or "file", f.content_type))

    # Единая оркестрация сервиса (iron rule 8): версия + перевод в REVIEW — один вызов.
    await _svc(
        design_files.submit_version(
            db,
            project_id=project.id,
            task_id=task_id,
            files=prepared,
            comment=comment,
            user=user,
            member_role=member_role,
        )
    )
    return await _detail(db, project.id, task_id, user, member_role)


@router.get(
    "/{task_id}/submissions/{sub_id}/files/{file_id}",
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_submission_file(
    task_id: int,
    sub_id: int,
    file_id: int,
    user: User = Depends(require_viewer),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Скачать файл версии: attachment + nosniff; 404 вне проекта/версии."""
    data, filename, content_type = await _svc(
        design_files.download_submission_file(
            db,
            project_id=project.id,
            task_id=task_id,
            file_id=file_id,
            submission_id=sub_id,
        )
    )
    return _attachment(data, filename, content_type)


@router.post(
    "/{task_id}/submissions/{sub_id}/verdict",
    response_model=DesignTaskDetail,
    dependencies=[Depends(rate_limit_write)],
)
async def set_verdict(
    task_id: int,
    sub_id: int,
    payload: DesignVerdictIn,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Вердикт по версии (автор/lead, задача в REVIEW): ACCEPTED | REJECTED (→ Правки)."""
    await _svc(
        design_files.set_verdict(
            db,
            project_id=project.id,
            task_id=task_id,
            submission_id=sub_id,
            verdict=payload.verdict,
            verdict_comment=payload.verdict_comment,
            user=user,
            member_role=member_role,
        )
    )
    return await _detail(db, project.id, task_id, user, member_role)


# ─── Комментарии / АБ-мост ───────────────────────────────────────────────────


@router.post(
    "/{task_id}/comments",
    response_model=DesignCommentOut,
    status_code=201,
    dependencies=[Depends(rate_limit_write)],
)
async def add_comment(
    task_id: int,
    payload: DesignCommentIn,
    user: User = Depends(require_editor),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Комментарий к задаче (viewer read-only → 403 page/role-гейтом)."""
    comment = await _svc(crud.add_comment(db, project.id, task_id, payload.body, user))
    author_name = (
        f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username
    )
    return DesignCommentOut(
        id=comment.id,
        author_user_id=comment.author_user_id,
        author_name=author_name,
        body=comment.body,
        original_filename=comment.original_filename,
        created_at=comment.created_at,
    )


@router.post(
    "/{task_id}/ab-test",
    response_model=DesignAbTestOut,
    dependencies=[Depends(rate_limit_write)],
)
async def create_ab_test(
    task_id: int,
    payload: DesignAbTestIn | None = None,
    user: User = Depends(require_editor),
    member_role: str = Depends(get_member_role),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """АБ-мост (Ф6): с campaign_id — создаёт тест по image-файлам последней
    принятой версии (через сервис funnel); без тела/campaign_id — отдаёт prefill
    для редиректа на предзаполненную форму создания теста (Hints F6)."""
    return await _svc(
        ab_bridge.create_ab_test_from_task(
            db, project.id, task_id, user, member_role, payload or DesignAbTestIn()
        )
    )
