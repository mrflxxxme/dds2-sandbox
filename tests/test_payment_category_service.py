# ruff: noqa: RUF002, RUF003
"""Tests: справочник «Назначение оплаты» (payment_category_service) — CRUD + защиты."""

from decimal import Decimal

import pytest

from backend.models.payment_request import PaymentRequest
from backend.services import payment_category_service as pcs

# Категории глобальные (project_id NULL) и коммитятся → persist в общей dev/CI БД между
# тестами. Сбрасываем к сид-состоянию перед каждым тестом, иначе кастомные строки/переименования
# протекают (дубли лейблов, изменённые системные лейблы).
_SEED_LABELS = {
    "PHOTO_CONTENT": "Фотоконтент", "DESIGN": "Дизайн", "FULFILLMENT": "Фулфилмент",
    "CUSTOMS": "Таможенное оформление", "LOGISTICS": "Логистика", "HOUSEHOLD": "Хозрасходы", "OTHER": "Другое",
}


@pytest.fixture(autouse=True)
async def _reset_payment_categories(db_session):
    from sqlalchemy import text

    await db_session.execute(text("DELETE FROM payment_categories WHERE is_system = false"))
    for code, label in _SEED_LABELS.items():
        await db_session.execute(
            text("UPDATE payment_categories SET label = :l, is_deleted = false WHERE code = :c AND is_system = true"),
            {"l": label, "c": code},
        )
    await db_session.commit()
    yield


async def test_seeded_system_categories_listed(db_session, project):
    cats = await pcs.list_categories(db_session, project.id)
    codes = {c.code for c in cats}
    assert {"LOGISTICS", "PHOTO_CONTENT", "OTHER"} <= codes
    assert all(c.is_system for c in cats if c.code in pcs.SYSTEM_CODES)


async def test_create_custom_category(db_session, project):
    cat = await pcs.create_category(db_session, project.id, "Маркетинг", None)
    assert cat.is_system is False
    assert cat.code  # код сгенерён (кириллица → C_<uuid>)
    assert cat.label == "Маркетинг"
    # появилась в списке и валидна для заявки
    assert cat.code in await pcs.active_codes(db_session, project.id)
    assert await pcs.is_valid_code(db_session, project.id, cat.code) is True


async def test_latin_label_gets_readable_code(db_session, project):
    cat = await pcs.create_category(db_session, project.id, "Marketing PPC", None)
    assert cat.code == "MARKETING_PPC"


async def test_create_duplicate_label_rejected(db_session, project):
    await pcs.create_category(db_session, project.id, "Бухгалтерия", None)
    with pytest.raises(pcs.PaymentCategoryError):
        await pcs.create_category(db_session, project.id, "  бухгалтерия ", None)  # тот же без учёта регистра


async def test_rename_keeps_code_stable(db_session, project):
    cat = await pcs.create_category(db_session, project.id, "Реклама", None)
    code = cat.code
    updated = await pcs.update_category(db_session, project.id, cat.id, "Продвижение", None)
    assert updated.code == code  # код не меняется
    assert updated.label == "Продвижение"


async def test_system_category_can_be_renamed(db_session, project):
    cats = await pcs.list_categories(db_session, project.id)
    other = next(c for c in cats if c.code == "OTHER")
    updated = await pcs.update_category(db_session, project.id, other.id, "Прочее", None)
    assert updated.code == "OTHER"
    assert updated.label == "Прочее"


async def test_system_category_cannot_be_deleted(db_session, project):
    cats = await pcs.list_categories(db_session, project.id)
    logistics = next(c for c in cats if c.code == "LOGISTICS")
    with pytest.raises(pcs.PaymentCategoryError):
        await pcs.delete_category(db_session, project.id, logistics.id)


async def test_delete_unused_custom_category(db_session, project):
    cat = await pcs.create_category(db_session, project.id, "Временная", None)
    await pcs.delete_category(db_session, project.id, cat.id)
    assert cat.code not in await pcs.active_codes(db_session, project.id)


async def test_delete_in_use_category_rejected(db_session, project):
    cat = await pcs.create_category(db_session, project.id, "Софт", None)
    db_session.add(
        PaymentRequest(
            project_id=project.id, number="ОПЛ-T1", status="PENDING_REVIEW",
            source="MANUAL", amount=Decimal("100.00"), currency="RUB", category=cat.code,
        )
    )
    await db_session.flush()
    with pytest.raises(pcs.PaymentCategoryError):
        await pcs.delete_category(db_session, project.id, cat.id)


async def test_unknown_code_is_invalid(db_session, project):
    assert await pcs.is_valid_code(db_session, project.id, "NOPE_NOT_A_CODE") is False
    assert await pcs.is_valid_code(db_session, project.id, "OTHER") is True  # системный
