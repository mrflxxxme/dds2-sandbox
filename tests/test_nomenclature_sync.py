"""Regression tests for WB nomenclature sync (integrations_service.sync_wb_nomenclature).

Guards the int32-overflow bug: WB `imtID` crossed the int32 ceiling (2 726 044 315 >
2 147 483 647), the INSERT into `nomenclature` failed with `value out of int32 range`,
poisoned the session, and the endpoint returned 500 ("Внутренняя ошибка сервера").
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.models.cost import Nomenclature
from backend.models.integrations import IntegrationKey
from backend.services import integrations_service

# imtID / nmID beyond int32 max (2 147 483 647) — the values WB now returns.
BIG_IMT_ID = 2_726_044_315
BIG_NM_ID = 2_999_888_777


def _card(barcode: str, imt_id: int, nm_id: int) -> dict:
    return {
        "nmID": nm_id,
        "imtID": imt_id,
        "brand": "Уютопия",
        "subjectName": "Покрывала",
        "vendorCode": "GLAZYR_180х200_шоколад",
        "dimensions": {"length": 30, "width": 22, "height": 12},
        "sizes": [{"skus": [barcode]}],
    }


async def _make_wb_key(db_session, project_id: int) -> IntegrationKey:
    key = IntegrationKey(
        project_id=project_id,
        service="wb",
        encrypted_key="enc",
        is_active=True,
    )
    db_session.add(key)
    # Commit so the key survives the service's rollback-on-error path, mirroring
    # production where the WB key is persisted by an earlier request.
    await db_session.commit()
    return key


@pytest.mark.asyncio
async def test_sync_persists_wb_id_beyond_int32(db_session, project):
    """A card whose imtID/nmID exceed int32 must sync OK (not 500)."""
    await _make_wb_key(db_session, project.id)
    cards = [_card("2052654682642", BIG_IMT_ID, BIG_NM_ID)]

    fake_client = AsyncMock()
    fake_client.get_cards_list = AsyncMock(return_value=cards)

    with (
        patch.object(integrations_service, "_decrypt", return_value="api-key"),
        patch("backend.integrations.wb_api.WBApiClient", return_value=fake_client),
        patch(
            "backend.services.cost.first_sale.backfill_first_sale_dates",
            new=AsyncMock(return_value={}),
        ),
    ):
        log = await integrations_service.sync_wb_nomenclature(db_session, project.id)

    assert log.status == "OK", log.error_msg
    row = (
        await db_session.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project.id,
                Nomenclature.barcode == "2052654682642",
            )
        )
    ).scalar_one()
    assert row.imt_id == BIG_IMT_ID
    assert row.article_wb == BIG_NM_ID


async def _run_sync(db_session, project, cards):
    fake_client = AsyncMock()
    fake_client.get_cards_list = AsyncMock(return_value=cards)
    with (
        patch.object(integrations_service, "_decrypt", return_value="api-key"),
        patch("backend.integrations.wb_api.WBApiClient", return_value=fake_client),
        patch(
            "backend.services.cost.first_sale.backfill_first_sale_dates",
            new=AsyncMock(return_value={}),
        ),
    ):
        return await integrations_service.sync_wb_nomenclature(db_session, project.id)


async def _nom_by_barcode(db_session, project, barcode: str) -> Nomenclature:
    return (
        await db_session.execute(
            select(Nomenclature).where(
                Nomenclature.project_id == project.id,
                Nomenclature.barcode == barcode,
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_sync_fills_chrt_id(db_session, project):
    """chrtID карточки обязан долетать до `Nomenclature.chrt_id`.

    Ключ методов остатков Marketplace API — chrtId. Пока синк карточек его не
    писал, трансляция FBS не отправляла в WB НИ ОДНОЙ позиции (все строки
    блокировались как `no_chrt`), а прогон при этом рапортовал OK.
    """
    await _make_wb_key(db_session, project.id)
    card = _card("2052654682644", 11, 21)
    card["sizes"] = [{"chrtID": 987654, "skus": ["2052654682644"]}]

    log = await _run_sync(db_session, project, [card])

    assert log.status == "OK", log.error_msg
    row = await _nom_by_barcode(db_session, project, "2052654682644")
    assert row.chrt_id == 987654


@pytest.mark.asyncio
async def test_sync_does_not_wipe_known_chrt_id(db_session, project):
    """Карточка без chrtID не должна обнулять уже известный ключ остатков."""
    await _make_wb_key(db_session, project.id)
    db_session.add(
        Nomenclature(project_id=project.id, barcode="2052654682645", chrt_id=112233)
    )
    await db_session.commit()

    log = await _run_sync(db_session, project, [_card("2052654682645", 12, 22)])

    assert log.status == "OK", log.error_msg
    row = await _nom_by_barcode(db_session, project, "2052654682645")
    assert row.chrt_id == 112233


@pytest.mark.asyncio
async def test_sync_returns_error_log_not_500_on_bad_row(db_session, project):
    """If a row still fails to flush, the session is rolled back and an ERROR
    SyncLog is returned — the endpoint must never surface a raw 500."""
    await _make_wb_key(db_session, project.id)
    # subjectName far longer than the String(100) column → DataError on flush.
    bad = _card("2052654682643", 10, 20)
    bad["subjectName"] = "x" * 300

    fake_client = AsyncMock()
    fake_client.get_cards_list = AsyncMock(return_value=[bad])

    with (
        patch.object(integrations_service, "_decrypt", return_value="api-key"),
        patch("backend.integrations.wb_api.WBApiClient", return_value=fake_client),
        patch(
            "backend.services.cost.first_sale.backfill_first_sale_dates",
            new=AsyncMock(return_value={}),
        ),
    ):
        log = await integrations_service.sync_wb_nomenclature(db_session, project.id)

    assert log.status == "ERROR"
    assert log.error_msg
