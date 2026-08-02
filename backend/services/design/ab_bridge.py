# ruff: noqa: RUF002, RUF003
"""АБ-мост (Ф6): из принятой задачи дизайна — АБ-тест главного фото карточки WB.

Строго поверх существующего сервиса funnel (`ab_photo_tests.create_test` +
`add_variant`): PIL-валидация (≥700×900, ≤10 МБ, лимит 10), проверка «нет
активного теста по nm_id» и снимок галереи НЕ дублируются — ошибки донора
транслируются как есть (ValueError → 400 в роутере).

campaign_id обязателен у донора, а у задачи дизайна кампании нет (Out of scope
F6): без campaign_id в payload ручка НЕ создаёт тест, а отдаёт prefill-данные
для редиректа на предзаполненную форму `/ab-tests/create?nm_id=...&
from_design_task=...` (вариант «редирект» из Hints F6); с campaign_id — полный
мост: файлы ПОСЛЕДНЕЙ принятой версии (только image/*) становятся вариантами.

Транзакционность: create_test/add_variant донора коммитят каждый сам — отката
одной транзакцией нет. Компенсация: сбой любого add_variant → уже созданный
тест soft_delete + жёсткое удаление его вариантов (AbPhotoVariant без
SoftDeleteMixin), исходная ошибка пробрасывается; сироты MinIO — warning в лог
(канон files.py). Тест не остаётся «наполовину» (AC-3).
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.ab_tests import AbPhotoTest, AbPhotoVariant
from backend.models.design import (
    DesignSubmission,
    DesignSubmissionFile,
    DesignTaskStatus,
    DesignVerdict,
)
from backend.schemas.design import DesignAbTestIn, DesignAbTestOut, DesignAbTestPrefill
from backend.services.design import files as design_files
from backend.services.design.common import get_task_row
from backend.services.design.permissions import compute_permissions
from backend.services.funnel import ab_photo_tests

logger = logging.getLogger("dds.design")

# Тексты гвардов — контракт AC-2 спека F6 (роутер отдаёт их в 400 как есть).
ERR_NOT_ACCEPTED = "Тест создаётся только по принятой задаче"
ERR_NO_NM_ID = "Привяжите товар к задаче — тест сравнивает карточку конкретного артикула"
ERR_NO_IMAGES = "В принятой версии нет изображений"


async def _last_accepted_submission_images(
    db: AsyncSession, project_id: int, task_id: int
) -> tuple[int, list[DesignSubmissionFile]]:
    """(submission_id, image-файлы) ПОСЛЕДНЕЙ версии с verdict=ACCEPTED.

    Для задачи в ACCEPTED версия гарантирована стейт-машиной (гвард «Нет версии
    на проверке» + закрытие PENDING); отсутствие — дефензивно тот же 400.
    """
    sub_res = await db.execute(
        select(DesignSubmission)
        .where(
            DesignSubmission.project_id == project_id,
            DesignSubmission.task_id == task_id,
            DesignSubmission.verdict == DesignVerdict.ACCEPTED.value,
        )
        .order_by(DesignSubmission.version_no.desc())
        .limit(1)
    )
    sub = sub_res.scalar_one_or_none()
    if sub is None:
        raise ValueError(ERR_NO_IMAGES)
    files_res = await db.execute(
        select(DesignSubmissionFile)
        .where(
            DesignSubmissionFile.project_id == project_id,
            DesignSubmissionFile.submission_id == sub.id,
            DesignSubmissionFile.mime_type.like("image/%"),
        )
        .order_by(DesignSubmissionFile.id)
        .limit(design_files.MAX_FILES_PER_SUBMISSION)
    )
    image_files = list(files_res.scalars().all())
    if not image_files:
        raise ValueError(ERR_NO_IMAGES)
    return int(sub.id), image_files


async def _compensate_half_test(db: AsyncSession, project_id: int, test_id: int) -> None:
    """Убрать наполовину созданный тест: донор уже закоммитил create_test и часть
    add_variant. Варианты — жёсткий DELETE (модель без SoftDelete), тест —
    soft_delete (iron rule 3). Best-effort: исходная ошибка важнее компенсации.
    """
    try:
        await db.rollback()  # сбросить незакоммиченное состояние упавшего add_variant
        test_res = await db.execute(
            select(AbPhotoTest).where(
                AbPhotoTest.id == test_id,
                AbPhotoTest.project_id == project_id,
                AbPhotoTest.is_deleted == False,  # noqa: E712
            )
        )
        test = test_res.scalar_one_or_none()
        if test is None:  # create_test не докоммитил — rollback уже всё убрал
            return
        variants_res = await db.execute(
            select(AbPhotoVariant)
            .where(
                AbPhotoVariant.project_id == project_id,
                AbPhotoVariant.test_id == test_id,
            )
            .limit(ab_photo_tests.MAX_VARIANTS + 1)
        )
        for variant in variants_res.scalars().all():
            logger.warning(
                "design ab_bridge: сирота MinIO после компенсации теста %d: %s",
                test_id,
                variant.image_key,
            )
            await db.delete(variant)  # no-soft-delete-check: AbPhotoVariant без SoftDeleteMixin
        test.soft_delete()
        await db.commit()
        logger.warning("design ab_bridge: тест %d удалён компенсацией (сбой add_variant)", test_id)
    except Exception as e:  # noqa: BLE001 — компенсация не должна затмить исходную ошибку
        logger.error("design ab_bridge: компенсация теста %d не удалась: %s", test_id, e)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001, S110 — сессию дочистит teardown get_db
            pass


async def create_ab_test_from_task(
    db: AsyncSession,
    project_id: int,
    task_id: int,
    user: Any,
    member_role: str,
    payload: DesignAbTestIn,
) -> DesignAbTestOut:
    """АБ-тест из принятой задачи (спек F6). Гварды → право → файлы → донор.

    Порядок ошибок: 404 «Задача не найдена» (изоляция) → 400 статус → 400 nm_id
    → 403 право (permissions.can_create_ab_test) → 400 «нет изображений» →
    ошибки донора как есть (в т.ч. «уже есть незавершённый тест»).
    """
    task = await get_task_row(db, project_id, task_id)
    if task.status != DesignTaskStatus.ACCEPTED.value:
        raise ValueError(ERR_NOT_ACCEPTED)
    if task.nm_id is None:
        raise ValueError(ERR_NO_NM_ID)
    if not compute_permissions(task, user, member_role)["can_create_ab_test"]:
        raise PermissionError("АБ-тест создаёт автор заявки или ведущий")

    # Скаляры — в локальные ДО commit'ов (expire по commit не дёрнет lazy-load).
    nm_id = int(task.nm_id)
    number = str(task.number)
    title = str(task.title)
    name = f"{number} · {title}"
    comment = f"Из задачи дизайна {number} «{title}»"  # обратная ссылка DES-N

    submission_id, image_files = await _last_accepted_submission_images(db, project_id, task_id)
    file_ids = [int(f.id) for f in image_files]

    if payload.campaign_id is None:
        # Донор требует campaign_id, у задачи его нет: не изобретаем выбор
        # кампании — фронт редиректит на предзаполненную форму (Hints F6).
        return DesignAbTestOut(
            prefill=DesignAbTestPrefill(
                nm_id=nm_id, from_design_task=number, name=name, comment=comment
            )
        )

    # Файлы версии — через files-хелпер Ф1 (project-scoped, 503 MinIO сквозной).
    contents: list[bytes] = []
    for file_id in file_ids:
        data, _, _ = await design_files.download_submission_file(
            db,
            project_id=project_id,
            task_id=task_id,
            file_id=file_id,
            submission_id=submission_id,
        )
        contents.append(data)
    # Не держать БД-транзакцию через внешние HTTP донора (канон learnings).
    await db.commit()

    test = await ab_photo_tests.create_test(
        db,
        project_id,
        nm_id=nm_id,
        campaign_id=int(payload.campaign_id),
        name=name,
        comment=comment,
    )
    test_id = int(test.id)
    try:
        for content in contents:
            await ab_photo_tests.add_variant(db, project_id, test_id, content)
    except BaseException:  # и ValueError донора, и обрыв — тест не остаётся наполовину
        await _compensate_half_test(db, project_id, test_id)
        raise
    return DesignAbTestOut(ab_test_id=test_id)
