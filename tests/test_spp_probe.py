# ruff: noqa: RUF001, RUF002, RUF003
"""Проба цены — единственное место, где DDS2 пишет цену в ВБ.

Тесты держат защиты: снимок «до» записан ДО первой записи в ВБ, цена всегда
возвращается (в том числе после ошибки), шаг ограничен, ниже пола не идём,
две пробы по одному товару одновременно не запускаются.
"""

from decimal import Decimal

import pytest

from backend.models import WbPrice, WbSppProbe
from backend.services.pricing.spp_probe import (
    MAX_STEP_PCT,
    ProbeRefused,
    _price_and_discount,
    _spp,
    run_probe,
    start_probe,
)


class TestPriceAndDiscount:
    """ВБ принимает целую базу и целую скидку — пару подбираем под точную цену.

    При фиксированной базе произвольную цену с копейками не поставить: у базы
    6518 ₽ соседние достижимые значения 1955.40 (70 %) и 2020.58 (69 %), а
    «1999,14» между ними нет — из-за этого первая проба и не могла встать на
    нужный уровень.
    """

    @pytest.mark.parametrize("target", [1999.14, 1999.20, 1955.40, 4999.20])
    def test_pair_hits_target_exactly(self, target):
        base, disc = _price_and_discount(target, 6518.0, 70.0)
        assert round(base * (1 - disc / 100), 2) == target

    def test_keeps_current_base_when_it_already_fits(self):
        """1955.40 достижима нынешней базой — не трогаем зачёркнутую цену зря."""
        assert _price_and_discount(1955.40, 6518.0, 70.0) == (6518, 70)

    def test_discount_within_wb_limits(self):
        _, disc = _price_and_discount(1.0, 100000.0, 0.0)
        assert 0 <= disc <= 90

    def test_no_base_still_works(self):
        base, disc = _price_and_discount(1999.0, 0.0, 0.0)
        assert round(base * (1 - disc / 100), 2) == 1999.0


class TestSpp:
    def test_basic(self):
        assert _spp(2000, 1500) == 25.0

    def test_no_buyer_price(self):
        assert _spp(2000, None) is None


@pytest.mark.asyncio
class TestGuards:
    async def _price(self, db, project, nm_id=770001, price="2000.00"):
        db.add(
            WbPrice(
                project_id=project.id, nm_id=nm_id, base_price=Decimal("4000.00"),
                price=Decimal(price), discount=Decimal("50.00"), currency="RUB",
            )
        )
        await db.commit()

    async def test_refuses_step_over_limit(self, db_session, project, monkeypatch):
        await self._price(db_session, project)
        monkeypatch.setattr(
            "backend.services.pricing.spp_probe._card_price", _fake_card(1500.0)
        )
        with pytest.raises(ProbeRefused, match="Шаг"):
            await start_probe(db_session, project.id, 770001, 2000 * (1 + MAX_STEP_PCT) + 100)

    async def test_refuses_below_floor(self, db_session, project, monkeypatch):
        await self._price(db_session, project, nm_id=770002)
        monkeypatch.setattr("backend.services.pricing.spp_probe._card_price", _fake_card(1500.0))
        with pytest.raises(ProbeRefused, match="ниже пола"):
            await start_probe(db_session, project.id, 770002, 1800.0, floor_price=1900.0)

    async def test_refuses_without_synced_price(self, db_session, project, monkeypatch):
        monkeypatch.setattr("backend.services.pricing.spp_probe._card_price", _fake_card(1500.0))
        with pytest.raises(ProbeRefused, match="нет синканой цены"):
            await start_probe(db_session, project.id, 770099, 1999.0)

    async def test_refuses_second_probe_on_same_nm(self, db_session, project, monkeypatch):
        await self._price(db_session, project, nm_id=770003)
        monkeypatch.setattr("backend.services.pricing.spp_probe._card_price", _fake_card(1500.0))
        await start_probe(db_session, project.id, 770003, 1900.0)
        with pytest.raises(ProbeRefused, match="уже идёт проба"):
            await start_probe(db_session, project.id, 770003, 1850.0)

    async def test_snapshot_written_before_any_write(self, db_session, project, monkeypatch):
        """Строка журнала есть ДО похода в ВБ — откатывать всегда есть чем."""
        await self._price(db_session, project, nm_id=770004)
        monkeypatch.setattr("backend.services.pricing.spp_probe._card_price", _fake_card(1500.0))
        probe = await start_probe(db_session, project.id, 770004, 1900.0)
        assert probe.id is not None
        assert float(probe.seller_price_before) == 2000.0
        assert float(probe.buyer_price_before) == 1500.0
        assert float(probe.spp_before) == 25.0
        assert probe.reverted is False


@pytest.mark.asyncio
class TestRun:
    async def test_price_returned_even_after_error(self, db_session, project, monkeypatch):
        """Цена уже поставлена, дальше всё падает — откат обязан сработать сам."""
        db_session.add(
            WbPrice(
                project_id=project.id, nm_id=770010, base_price=Decimal("4000.00"),
                price=Decimal("2000.00"), discount=Decimal("50.00"), currency="RUB",
            )
        )
        await db_session.commit()
        monkeypatch.setattr("backend.services.pricing.spp_probe._card_price", _fake_card(1500.0))
        probe = await start_probe(db_session, project.id, 770010, 1900.0)

        calls: list[tuple] = []
        monkeypatch.setattr(
            "backend.integrations.wb_api.WBApiClient.set_price", _recording_set_price(calls)
        )
        monkeypatch.setattr("backend.services.integrations_service._get_wb_key", _fake_key())
        # цена уже на витрине, а опрос падает — ровно тот случай, когда откат обязан
        # сработать сам
        monkeypatch.setattr(
            "backend.services.pricing.spp_probe._card_price", _boom
        )

        await run_probe(db_session, probe, hold_sec=1, poll_sec=0)
        assert probe.status == "ERROR"
        assert probe.reverted is True
        back_base, back_disc = calls[-1][1], calls[-1][2]
        assert round(back_base * (1 - back_disc / 100), 2) == 2000.0  # вернули ту же цену


def _fake_card(buyer: float):
    async def _inner(nm_id: int):
        return buyer
    return _inner


def _fake_key():
    async def _inner(db, project_id):
        return None, "test-key"
    return _inner


def _recording_set_price(calls: list):
    async def _inner(self, nm_id: int, price: int, discount: int | None = None):
        calls.append((nm_id, price, discount))
        return {}
    return _inner


async def _boom(nm_id: int):
    raise ValueError("card-API упал")


@pytest.mark.asyncio
class TestNotApplied:
    async def test_failed_first_write_is_not_a_lost_price(self, db_session, project, monkeypatch):
        """401 на первой же записи: цена не менялась — тревожить «не возвращена» не о чем.

        Живой случай 2026-08-01: локальный ключ ВБ без scope «Цены и скидки»
        отдал 401, и журнал пугал строкой «ЦЕНА НЕ ВОЗВРАЩЕНА», хотя на витрине
        ничего не трогали.
        """
        db_session.add(
            WbPrice(
                project_id=project.id, nm_id=770020, base_price=Decimal("4000.00"),
                price=Decimal("2000.00"), discount=Decimal("50.00"), currency="RUB",
            )
        )
        await db_session.commit()
        monkeypatch.setattr("backend.services.pricing.spp_probe._card_price", _fake_card(1500.0))
        probe = await start_probe(db_session, project.id, 770020, 1900.0)

        calls: list[tuple] = []
        monkeypatch.setattr(
            "backend.integrations.wb_api.WBApiClient.set_price", _always_401(calls)
        )
        monkeypatch.setattr("backend.services.integrations_service._get_wb_key", _fake_key())

        await run_probe(db_session, probe, hold_sec=1, poll_sec=0)
        assert probe.status == "ERROR"
        assert probe.reverted is True  # возвращать было нечего
        assert "НЕ ВОЗВРАЩЕНА" not in (probe.error or "")
        assert len(calls) == 1  # второй записи не было


def _always_401(calls: list):
    async def _inner(self, nm_id: int, price: int, discount: int | None = None):
        calls.append((nm_id, price, discount))
        raise ValueError("WB API: неверный API-ключ (401) — нужен scope «Цены и скидки»")
    return _inner
