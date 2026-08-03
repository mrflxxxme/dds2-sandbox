# ruff: noqa: RUF002, RUF003
"""
Router: /gazelka — передача заявки логиста перевозчику Газельке (gazelka.space).

HTTP + валидация; логика — в services/gazelka_service. У Газельки нет API:
интеграция ходит как браузер (cookie-сессия + form-POST). `send` — РЕАЛЬНОЕ
создание заявки во внешнем сервисе (необратимо), под rate_limit_write.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession

from backend.auth import get_current_user
from backend.database import get_db
from backend.models import Project, User
from backend.project_context import get_current_project
from backend.rbac import require_page
from backend.schemas.gazelka import (
    GazelkaConfigResponse,
    GazelkaDraftResponse,
    GazelkaEditDraft,
    GazelkaLinkKind,
    GazelkaMatchCandidate,
    GazelkaMatchRequest,
    GazelkaMatchResult,
    GazelkaOrderList,
    GazelkaSendRequest,
    GazelkaSendResult,
)
from backend.services import gazelka_service
from backend.services.gazelka_service import GazelkaServiceError
from backend.utils.rate_limit import rate_limit_write

# `/send` создаёт РЕАЛЬНЫЙ заказ на перевозку в портале Газельки — деньги наружу,
# отменить нельзя. Гейт на роутере: ключ logistics («Оплаты», «Счета ФФ»,
# «Слоты сдачи» в меню), мутации — от editor.
router = APIRouter(
    prefix="/gazelka",
    tags=["Gazelka"],
    dependencies=[Depends(require_page("logistics"))],
)


def _actor(user: User) -> str:
    return getattr(user, "email", None) or f"user:{user.id}"


@router.get("/config", response_model=GazelkaConfigResponse)
async def gazelka_config(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> GazelkaConfigResponse:
    """Настроена ли интеграция и к какому складу (Натали) привязана."""
    return await gazelka_service.get_config(db, project.id)


@router.get("/assembly/{assembly_id}/draft", response_model=GazelkaDraftResponse)
async def gazelka_draft(
    assembly_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> GazelkaDraftResponse:
    """Справочники их формы + предзаполнение из сборки (для диалога логиста)."""
    try:
        return await gazelka_service.build_draft(db, project.id, assembly_id)
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.post(
    "/assembly/{assembly_id}/send",
    response_model=GazelkaSendResult,
    dependencies=[Depends(rate_limit_write)],
)
async def gazelka_send(
    assembly_id: int,
    payload: GazelkaSendRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GazelkaSendResult:
    """РЕАЛЬНАЯ отправка заявки в Газельку (необратимо). Пишет audit-строку."""
    try:
        return await gazelka_service.send_order(db, project.id, assembly_id, payload, actor=_actor(user))
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


# ─── Переезд между нашими складами (StockTransfer) ───────────────────────────
# Зеркало assembly-путей: у Газельки один и тот же кабинет и одна и та же форма,
# отличается только источник данных и гейт (склад-ИСТОЧНИК вместо склада отгрузки).


@router.get("/transfer/{transfer_id}/draft", response_model=GazelkaDraftResponse)
async def gazelka_transfer_draft(
    transfer_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> GazelkaDraftResponse:
    """Справочники их формы + предзаполнение из переезда (для диалога логиста)."""
    try:
        return await gazelka_service.build_transfer_draft(db, project.id, transfer_id)
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.post(
    "/transfer/{transfer_id}/send",
    response_model=GazelkaSendResult,
    dependencies=[Depends(rate_limit_write)],
)
async def gazelka_transfer_send(
    transfer_id: int,
    payload: GazelkaSendRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GazelkaSendResult:
    """РЕАЛЬНАЯ отправка переезда в Газельку (необратимо). Пишет audit-строку."""
    try:
        return await gazelka_service.send_transfer_order(
            db, project.id, transfer_id, payload, actor=_actor(user)
        )
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


# ─── Заявки из портала (списки / ТТН / редактирование) ───────────────────────


@router.get("/planned", response_model=GazelkaOrderList)
async def gazelka_planned(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> GazelkaOrderList:
    """Запланированные заявки из кабинета Газельки (с пометкой связи с нашей сборкой)."""
    try:
        return await gazelka_service.list_planned(db, project.id)
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.get("/active", response_model=GazelkaOrderList)
async def gazelka_active(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> GazelkaOrderList:
    """Активные (в маршруте) заявки: водитель, ТС, перевозчик, статус, дата сдачи."""
    try:
        return await gazelka_service.list_active(db, project.id)
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.get("/completed", response_model=GazelkaOrderList)
async def gazelka_completed(
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> GazelkaOrderList:
    """Завершённые заявки (из наших данных — у портала архива нет): отгруженные сборки
    со снимком водителя/ТС/тарифа/перевозчика на момент отгрузки."""
    try:
        return await gazelka_service.list_completed(db, project.id)
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.get("/order/{plan_id}/ttn")
async def gazelka_ttn(
    plan_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Печатная форма (упаковочный лист/ТТН) заявки — HTML для печати/сохранения."""
    try:
        content, content_type = await gazelka_service.get_ttn(db, project.id, str(plan_id))
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
    return Response(content=content, media_type=content_type, headers={"Content-Disposition": "inline"})


@router.get("/order/{plan_id}/edit", response_model=GazelkaEditDraft)
async def gazelka_edit_draft(
    plan_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> GazelkaEditDraft:
    """Данные для редактирования заявки: их справочники + текущие значения."""
    try:
        return await gazelka_service.build_edit_draft(db, project.id, str(plan_id))
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.post(
    "/order/{plan_id}/edit",
    response_model=GazelkaSendResult,
    dependencies=[Depends(rate_limit_write)],
)
async def gazelka_save_edit(
    plan_id: int,
    payload: GazelkaSendRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GazelkaSendResult:
    """Сохранить правку заявки в Газельке (необратимо). Пишет audit-строку."""
    try:
        return await gazelka_service.save_edit(db, project.id, str(plan_id), payload, actor=_actor(user))
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


# ─── Матчинг существующих заявок портала с нашими сборками ────────────────────


@router.get("/match-candidates", response_model=list[GazelkaMatchCandidate])
async def gazelka_match_candidates(
    search: str | None = None,
    # 🔴 `GazelkaLinkKind` обязан быть ИМПОРТИРОВАН в модуль: под
    # `from __future__ import annotations` FastAPI хранит аннотацию как
    # ForwardRef и разрешает её по глобалям модуля. Без импорта импорт модуля
    # проходит молча, дефолт отдаётся без валидации, а первый же запрос СО
    # значением `?kind=` падает PydanticUserError → 500.
    kind: GazelkaLinkKind = "assembly",
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> list[GazelkaMatchCandidate]:
    """Наши документы — кандидаты на ручное сопоставление с заявкой Газельки.

    `kind=assembly` (дефолт) — сборки, `kind=transfer` — переезды между складами.
    """
    return await gazelka_service.list_match_candidates(db, project.id, search=search, kind=kind)


@router.post(
    "/order/{plan_id}/match",
    response_model=GazelkaMatchResult,
    dependencies=[Depends(rate_limit_write)],
)
async def gazelka_match(
    plan_id: int,
    payload: GazelkaMatchRequest,
    project: Project = Depends(get_current_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GazelkaMatchResult:
    """Связать существующую заявку портала с нашим документом (сборка или переезд)."""
    try:
        # Тело целиком, а не `payload.assembly_id`: сервис сам решает вид документа
        # по тому, какая из ссылок непуста (`transfer_id` / `assembly_id`).
        return await gazelka_service.match_order(db, project.id, str(plan_id), payload, actor=_actor(user))
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e


@router.delete(
    "/order/{plan_id}/match",
    response_model=GazelkaMatchResult,
    dependencies=[Depends(rate_limit_write)],
)
async def gazelka_unmatch(
    plan_id: int,
    project: Project = Depends(get_current_project),
    db: AsyncSession = Depends(get_db),
) -> GazelkaMatchResult:
    """Снять ручную связь заявки портала со сборкой."""
    try:
        return await gazelka_service.unmatch_order(db, project.id, str(plan_id))
    except GazelkaServiceError as e:
        raise HTTPException(status_code=e.status_code, detail=str(e)) from e
