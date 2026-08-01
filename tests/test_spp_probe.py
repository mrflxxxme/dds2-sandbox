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
    """Меняем скидку, а не базу: покупатель видит ту же зачёркнутую цену."""

    def test_discount_derived_from_base(self):
        assert _price_and_discount(1999.0, 6518.0, 70.0) == (6518, 69)

    def test_discount_clamped(self):
        _, disc = _price_and_discount(1.0, 100000.0, 0.0)
        assert disc == 99

    def test_no_base_falls_back_to_plain_price(self):
        assert _price_and_discount(1999.0, 0.0, 0.0) == (1999, 0)


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
        """Ошибка в середине пробы не должна оставлять чужую цену на витрине."""
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
            "backend.integrations.wb_api.WBApiClient.set_price",
            _recording_set_price(calls, fail_first=True),
        )
        monkeypatch.setattr(
            "backend.services.integrations_service._get_wb_key", _fake_key()
        )

        await run_probe(db_session, probe, hold_sec=1)
        assert probe.status == "ERROR"
        assert probe.reverted is True
        assert calls[-1][1:] == (4000, 50)  # вернули исходную скидку


def _fake_card(buyer: float):
    async def _inner(nm_id: int):
        return buyer
    return _inner


def _fake_key():
    async def _inner(db, project_id):
        return None, "test-key"
    return _inner


def _recording_set_price(calls: list, *, fail_first: bool = False):
    async def _inner(self, nm_id: int, price: int, discount: int | None = None):
        calls.append((nm_id, price, discount))
        if fail_first and len(calls) == 1:
            raise ValueError("WB упал")
        return {}
    return _inner
