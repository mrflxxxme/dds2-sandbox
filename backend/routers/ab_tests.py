"""Router: /ab-tests — АБ-тесты главного фото карточки WB.

Thin HTTP layer — логика в services/funnel/ab_photo_tests.py. Ответы — dict
(как у эндпоинтов ads-manager): живые данные теста, кэш не используется.
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.models import Project
from backend.project_context import get_current_project
from backend.services.funnel import ab_photo_tests as svc
from backend.utils.rate_limit import rate_limit_write

logger = logging.getLogger("dds.ab_tests")

router = APIRouter(prefix="/ab-tests")


class CreateTestRequest(BaseModel):
    nm_id: int
    campaign_id: int
    name: str = ""
    comment: str | None = None
    views_per_round: int = Field(5000, ge=100, le=1_000_000)
    round_minutes: int = Field(45, ge=15, le=24 * 60)
    target_views: int = Field(10000, ge=1000, le=10_000_000)
    max_days: int = Field(7, ge=1, le=30)


@router.get("")
async def list_ab_tests(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Список тестов проекта (для главной страницы раздела)."""
    return {"tests": await svc.list_tests(db, project.id)}


@router.post("", dependencies=[Depends(rate_limit_write)])
async def create_ab_test(
    payload: CreateTestRequest,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Создать черновик: снимок галереи + контрольный вариант из текущего фото."""
    try:
        test = await svc.create_test(
            db,
            project.id,
            nm_id=payload.nm_id,
            campaign_id=payload.campaign_id,
            name=payload.name,
            comment=payload.comment,
            views_per_round=payload.views_per_round,
            round_minutes=payload.round_minutes,
            target_views=payload.target_views,
            max_days=payload.max_days,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": test.id, "status": test.status}


@router.get("/{test_id}")
async def get_ab_test(
    test_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Карточка теста: варианты с агрегатами + история кругов."""
    try:
        return await svc.get_test_results(db, project.id, test_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/{test_id}/photos", dependencies=[Depends(rate_limit_write)])
async def upload_ab_photo(
    test_id: int,
    file: UploadFile = File(...),
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Загрузить вариант фото в черновик (JPEG/PNG/WebP, ≥700×900, ≤10 МБ)."""
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(413, f"Файл слишком большой. Максимум: {settings.MAX_UPLOAD_SIZE_MB} МБ")
    try:
        variant = await svc.add_variant(db, project.id, test_id, content)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": variant.id, "position": variant.position}


@router.get("/{test_id}/photos/{variant_id}")
async def get_ab_photo(
    test_id: int,
    variant_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Байты фото варианта (авторизованно; фронт делает fetch → objectURL)."""
    data = await svc.get_variant_photo(db, project.id, test_id, variant_id)
    if data is None:
        raise HTTPException(404, "Фото не найдено")
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@router.delete("/{test_id}/photos/{variant_id}", dependencies=[Depends(rate_limit_write)])
async def delete_ab_photo(
    test_id: int,
    variant_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Удалить вариант из черновика."""
    try:
        await svc.delete_variant(db, project.id, test_id, variant_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/{test_id}/start", dependencies=[Depends(rate_limit_write)])
async def start_ab_test(
    test_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Запустить тест (первый круг — контроль, фото не меняется)."""
    try:
        test = await svc.start_test(db, project.id, test_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": test.id, "status": test.status}


@router.post("/{test_id}/stop", dependencies=[Depends(rate_limit_write)])
async def stop_ab_test(
    test_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Завершить досрочно: вернуть исходное фото, зафиксировать победителя."""
    try:
        test = await svc.stop_test(db, project.id, test_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": test.id, "status": test.status, "winner_variant_id": test.winner_variant_id}


@router.post("/{test_id}/resume", dependencies=[Depends(rate_limit_write)])
async def resume_ab_test(
    test_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Продолжить тест после паузы (текущая галерея принимается за новую базу)."""
    try:
        test = await svc.resume_test(db, project.id, test_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": test.id, "status": test.status}


@router.post("/{test_id}/variants/{variant_id}/exclude", dependencies=[Depends(rate_limit_write)])
async def exclude_ab_variant(
    test_id: int,
    variant_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Минусик: исключить фото из ротации (данные остаются в таблице)."""
    try:
        await svc.set_variant_excluded(db, project.id, test_id, variant_id, excluded=True)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/{test_id}/variants/{variant_id}/include", dependencies=[Depends(rate_limit_write)])
async def include_ab_variant(
    test_id: int,
    variant_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Плюсик: вернуть исключённое фото в ротацию."""
    try:
        await svc.set_variant_excluded(db, project.id, test_id, variant_id, excluded=False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/{test_id}/apply-winner", dependencies=[Depends(rate_limit_write)])
async def apply_ab_winner(
    test_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
):
    """Поставить фото-победителя на карточку (по кнопке)."""
    try:
        test = await svc.apply_winner(db, project.id, test_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "winner_applied_at": test.winner_applied_at.isoformat() if test.winner_applied_at else None}
