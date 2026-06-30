# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты справочника БИК ЦБ (ED807): парсинг, синк, резолв к/с по БИК.

resolve_bank_db — авторитетный резолвер для платёжки: таблица cbr_bic (полный ЦБ) с
фолбэком на зашитый набор крупных банков. Закрывает баг «Неверно указан к/с» (пустой
к/с у заявок на банки вне зашитого набора — региональные Сбербанки и т.п.)."""

import pytest
from sqlalchemy import text

from backend.services import cbr_bic_loader
from backend.services.bank_directory import resolve_bank_db
from backend.services.cbr_bic_loader import parse_ed807

# Синтетический ED807 (как у ЦБ: namespace + windows-1251). Точка + Уральский Сбер (валидные),
# один банк без счёта (пропустить), один с к/с не-301 (пропустить).
_ED807 = (
    '<?xml version="1.0" encoding="windows-1251"?>'
    '<ED807 xmlns="urn:cbr-ru:ed:v2.0" EDNo="1" EDDate="2026-06-30">'
    '<BICDirectoryEntry BIC="044525104">'
    '<ParticipantInfo NameP="ООО &quot;Банк Точка&quot;" ParticipantStatus="PSAC"/>'
    '<Accounts Account="30101810745374525104" RegulationAccountType="CRSA" AccountStatus="ACAC"/>'
    "</BICDirectoryEntry>"
    '<BICDirectoryEntry BIC="046577674">'
    '<ParticipantInfo NameP="Уральский банк ПАО Сбербанк"/>'
    '<Accounts Account="30101810500000000674" RegulationAccountType="CRSA" AccountStatus="ACAC"/>'
    "</BICDirectoryEntry>"
    '<BICDirectoryEntry BIC="049999111">'  # без активного корр-счёта → пропуск
    '<ParticipantInfo NameP="Банк без счёта"/>'
    "</BICDirectoryEntry>"
    '<BICDirectoryEntry BIC="044525222">'  # к/с не-301 → пропуск
    '<ParticipantInfo NameP="Левый счёт"/>'
    '<Accounts Account="40702810500000000222" RegulationAccountType="CRSA" AccountStatus="ACAC"/>'
    "</BICDirectoryEntry>"
    "</ED807>"
)


@pytest.fixture(autouse=True)
async def _clean_cbr_bic(db_session):
    """cbr_bic — глобальная таблица (commit persist'ит между тестами) → чистим после каждого."""
    yield
    await db_session.execute(text("DELETE FROM cbr_bic"))
    await db_session.commit()


def test_parse_ed807_extracts_active_corr_accounts():
    rows = parse_ed807(_ED807.encode("cp1251"))
    by_bic = {bic: (corr, name) for bic, corr, name in rows}
    assert by_bic["044525104"][0] == "30101810745374525104"
    assert "Точка" in by_bic["044525104"][1]  # cp1251-имя декодировано
    assert by_bic["046577674"][0] == "30101810500000000674"
    assert "049999111" not in by_bic  # нет активного корр-счёта
    assert "044525222" not in by_bic  # к/с не начинается с 301


async def test_sync_cbr_bic_upserts_and_resolves(db_session, monkeypatch):
    async def fake_fetch(url=None):
        return _ED807.encode("cp1251")

    monkeypatch.setattr(cbr_bic_loader, "fetch_ed807_xml", fake_fetch)
    n = await cbr_bic_loader.sync_cbr_bic(db_session)
    assert n == 2  # только валидные банки
    # региональный Сбер (НЕ в зашитом наборе) теперь резолвится из таблицы — корень бага закрыт
    bank = await resolve_bank_db(db_session, "046577674")
    assert bank is not None and bank["corr_account"] == "30101810500000000674"
    # повторный синк идемпотентен (UPSERT, без дублей/падения)
    assert await cbr_bic_loader.sync_cbr_bic(db_session) == 2


async def test_resolve_bank_db_falls_back_to_hardcoded(db_session):
    """Таблица пуста (банка нет) → фолбэк на зашитый набор крупных банков (платежи не ломаются)."""
    bank = await resolve_bank_db(db_session, "044525225")  # Сбер — в зашитом наборе
    assert bank is not None and bank["corr_account"] == "30101810400000000225"


async def test_resolve_bank_db_unknown_returns_none(db_session):
    assert await resolve_bank_db(db_session, "049999999") is None  # нигде нет
    assert await resolve_bank_db(db_session, "12345") is None       # не 9 цифр
    assert await resolve_bank_db(db_session, None) is None
