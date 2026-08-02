# ruff: noqa: RUF002, RUF003
"""Общие хелперы тестов модуля «Дизайн карточек» (Ф1).

Не тест-файл (без префикса test_). Роли PRD → member_role (CHARTER §1):
lead = owner/admin; author/designer = editor, различаются полями задачи.
"""

import itertools
import uuid
from types import SimpleNamespace

from backend.models.auth import ProjectMember, User
from backend.models.design import (
    DesignSubmission,
    DesignSubmissionFile,
    DesignTask,
    DesignTaskStatus,
    DesignVerdict,
)

_num_counter = itertools.count(500)


def actor(user) -> SimpleNamespace:
    """Мини-объект пользователя для сервисных вызовов (нужны .id и .username)."""
    return SimpleNamespace(id=user.id, username=user.username)


async def make_user(db, prefix: str) -> User:
    user = User(
        username=f"{prefix}_{uuid.uuid4().hex[:8]}",
        password_hash="test-nohash",
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


async def make_member(db, project_id: int, user_id: int, role: str = "editor") -> ProjectMember:
    member = ProjectMember(
        project_id=project_id,
        user_id=user_id,
        role=role,
        pages='["design-tasks"]',
    )
    db.add(member)
    await db.flush()
    return member


async def make_team(db, project) -> SimpleNamespace:
    """lead (admin) + author (editor, менеджер) + designer/designer2 (editor)."""
    lead = await make_user(db, "lead")
    author = await make_user(db, "author")
    designer = await make_user(db, "designer")
    designer2 = await make_user(db, "designer2")
    await make_member(db, project.id, lead.id, role="admin")
    await make_member(db, project.id, author.id, role="editor")
    await make_member(db, project.id, designer.id, role="editor")
    await make_member(db, project.id, designer2.id, role="editor")
    await db.commit()
    return SimpleNamespace(lead=lead, author=author, designer=designer, designer2=designer2)


async def make_task(
    db,
    project_id: int,
    author_id: int,
    *,
    status: DesignTaskStatus = DesignTaskStatus.NEW,
    assignee_id: int | None = None,
    number: str | None = None,
    sort_order: int = 0,
    commit: bool = True,
    **kwargs,
) -> DesignTask:
    task = DesignTask(
        project_id=project_id,
        number=number or f"DES-{next(_num_counter)}",
        title=kwargs.pop("title", "Инфографика ковёр 80x150"),
        description=kwargs.pop("description", "Главное фото + карусель, акцент на состав."),
        sheet_url=kwargs.pop("sheet_url", "https://docs.google.com/spreadsheets/d/tz-1"),
        status=status.value,
        assignee_user_id=assignee_id,
        author_user_id=author_id,
        sort_order=sort_order,
        **kwargs,
    )
    db.add(task)
    if commit:
        await db.commit()
    else:
        await db.flush()
    return task


async def add_submission(
    db,
    task: DesignTask,
    by_user_id: int,
    *,
    verdict: DesignVerdict = DesignVerdict.PENDING,
    version_no: int = 1,
    with_file: bool = False,
    commit: bool = True,
) -> DesignSubmission:
    """with_file=True — версия с файлом: гвард →REVIEW требует PENDING С файлами."""
    sub = DesignSubmission(
        project_id=task.project_id,
        task_id=task.id,
        version_no=version_no,
        submitted_by_user_id=by_user_id,
        verdict=verdict.value,
    )
    db.add(sub)
    await db.flush()
    if with_file:
        db.add(
            DesignSubmissionFile(
                project_id=task.project_id,
                submission_id=sub.id,
                minio_path=f"design/{task.project_id}/{task.id}/v{version_no}/test.png",
                original_filename="test.png",
                mime_type="image/png",
                file_size=8,
            )
        )
    if commit:
        await db.commit()
    else:
        await db.flush()
    return sub
