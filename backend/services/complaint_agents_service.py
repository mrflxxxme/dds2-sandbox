# ruff: noqa: RUF002 — русские комментарии и docstring
"""
Service: ИИ-агенты подготовки жалоб (`wb_complaint_agents`).

CRUD агентов + прогон: агент по фильтрам (предмет/бренд/артикул/звёзды) берёт отзывы
без жалобы, LLM (сменный провайдер) по ПРАВИЛАМ и контексту НАШЕГО товара решает
основание и готовит текст → создаёт жалобы (status pending) в `wb_feedback_complaints`.

Подготовка ≠ отправка. Прогон ограничен `_RUN_LIMIT` за раз (LLM небесплатен/небыстр).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Nomenclature, WBComplaintAgent, WBFeedback, WBFeedbackComplaint
from backend.services.ai import complaint_llm
from backend.services.complaints_service import create_complaint
from backend.utils.time import utcnow

logger = logging.getLogger("dds.reviews.agents")

_RUN_LIMIT = 25  # сколько отзывов агент обрабатывает LLM за один прогон


def _parse_ints(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for p in raw.replace(";", ",").split(","):
        p = p.strip()
        if p.isdigit():
            out.append(int(p))
    return out


def _to_dict(a: WBComplaintAgent) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "enabled": a.enabled,
        "subject": a.subject,
        "brand": a.brand,
        "nm_ids": a.nm_ids,
        "star_levels": a.star_levels,
        "rules": a.rules,
        "examples": a.examples,
        "llm_provider": a.llm_provider,
        "llm_model": a.llm_model,
        "llm_base_url": a.llm_base_url,
        "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
    }


async def list_agents(db: AsyncSession, project_id: int) -> list[dict]:
    rows = (
        await db.execute(
            select(WBComplaintAgent)
            .where(WBComplaintAgent.project_id == project_id)
            .order_by(WBComplaintAgent.created_at.desc())
            .limit(200)
        )
    ).scalars().all()
    return [_to_dict(a) for a in rows]


async def _get(db: AsyncSession, project_id: int, agent_id: int) -> WBComplaintAgent | None:
    return (
        await db.execute(
            select(WBComplaintAgent).where(
                WBComplaintAgent.project_id == project_id, WBComplaintAgent.id == agent_id
            )
        )
    ).scalar_one_or_none()


async def create_agent(db: AsyncSession, project_id: int, data: dict) -> dict:
    a = WBComplaintAgent(
        project_id=project_id,
        name=(data.get("name") or "").strip() or "Агент жалоб",
        enabled=bool(data.get("enabled", True)),
        subject=data.get("subject") or None,
        brand=data.get("brand") or None,
        nm_ids=data.get("nm_ids") or None,
        star_levels=data.get("star_levels") or "1,2,3",
        rules=(data.get("rules") or "").strip(),
        examples=data.get("examples") or None,
        llm_provider=data.get("llm_provider") or "openai_compatible",
        llm_model=data.get("llm_model") or "deepseek-chat",
        llm_base_url=data.get("llm_base_url") or None,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return _to_dict(a)


async def update_agent(db: AsyncSession, project_id: int, agent_id: int, data: dict) -> dict | None:
    a = await _get(db, project_id, agent_id)
    if a is None:
        return None
    for field in ("name", "subject", "brand", "nm_ids", "star_levels", "rules", "examples",
                  "llm_provider", "llm_model", "llm_base_url"):
        if field in data:
            setattr(a, field, data[field] or (None if field != "rules" else ""))
    if "enabled" in data:
        a.enabled = bool(data["enabled"])
    await db.commit()
    await db.refresh(a)
    return _to_dict(a)


async def delete_agent(db: AsyncSession, project_id: int, agent_id: int) -> bool:
    a = await _get(db, project_id, agent_id)
    if a is None:
        return False
    await db.delete(a)
    await db.commit()
    return True


async def run_agent(db: AsyncSession, project_id: int, agent_id: int) -> dict:
    """Прогнать агента: отобрать отзывы, оценить LLM, создать жалобы на подходящие."""
    a = await _get(db, project_id, agent_id)
    if a is None:
        raise ValueError("Агент не найден")
    if not (a.rules or "").strip():
        raise ValueError("У агента не заданы «Правила для жалобы»")

    stars = _parse_ints(a.star_levels) or [1, 2, 3]
    nm_filter = _parse_ints(a.nm_ids)

    # Кандидаты: отзывы среза без уже поданной жалобы (контекст товара — из зеркала/номенклатуры)
    nom = (
        select(
            Nomenclature.article_wb.label("nm_id"),
            Nomenclature.subject.label("subject"),
            Nomenclature.brand.label("brand"),
        )
        .where(Nomenclature.project_id == project_id, Nomenclature.article_wb.isnot(None))
        .subquery()
    )
    already = select(WBFeedbackComplaint.wb_feedback_id).where(WBFeedbackComplaint.project_id == project_id)

    stmt = (
        select(WBFeedback, nom.c.subject, nom.c.brand)
        .outerjoin(nom, nom.c.nm_id == WBFeedback.nm_id)
        .where(
            WBFeedback.project_id == project_id,
            WBFeedback.rating.in_(stars),
            WBFeedback.has_text,  # без текста жаловаться не на что
            WBFeedback.wb_id.notin_(already),
        )
    )
    if a.subject:
        stmt = stmt.where(nom.c.subject == a.subject)
    if a.brand:
        from sqlalchemy import func
        stmt = stmt.where(func.coalesce(nom.c.brand, WBFeedback.brand) == a.brand)
    if nm_filter:
        stmt = stmt.where(WBFeedback.nm_id.in_(nm_filter))
    stmt = stmt.order_by(WBFeedback.rating.asc(), WBFeedback.created_date.desc().nullslast()).limit(_RUN_LIMIT)

    rows = (await db.execute(stmt)).all()
    # Закрываем read-транзакцию ДО походов в LLM (не держать БД через внешний HTTP — learnings)
    await db.commit()

    checked = qualified = created = errors = 0
    for fb, nom_subject, nom_brand in rows:
        checked += 1
        product = {
            "name": fb.product_name or (f"nmID {fb.nm_id}" if fb.nm_id else ""),
            "subject": nom_subject or "",
            "brand": nom_brand or fb.brand or "",
        }
        review = {"rating": fb.rating, "text": fb.text, "pros": fb.pros, "cons": fb.cons}
        try:
            verdict = await complaint_llm.evaluate_review(
                a.llm_provider, a.llm_model, a.llm_base_url, a.rules, a.examples, product, review
            )
        except Exception as e:  # noqa: BLE001 — сбой LLM на одном отзыве не валит прогон
            errors += 1
            logger.warning("agent %d: LLM error on feedback %s: %s", agent_id, fb.wb_id, e)
            continue
        if not verdict["qualifies"]:
            continue
        qualified += 1
        text = verdict["complaint_text"].strip() or f"Просим удалить отзыв: {verdict['reason']}"
        try:
            await create_complaint(db, project_id, fb.wb_id, "not_related", text)
            created += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            logger.warning("agent %d: create complaint failed for %s: %s", agent_id, fb.wb_id, e)

    a2 = await _get(db, project_id, agent_id)
    if a2 is not None:
        a2.last_run_at = utcnow()
        await db.commit()

    return {"checked": checked, "qualified": qualified, "created": created, "errors": errors, "limit": _RUN_LIMIT}
