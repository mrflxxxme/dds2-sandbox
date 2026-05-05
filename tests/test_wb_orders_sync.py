"""Тесты sync wb_orders (sync_wb_orders / UPSERT idempotency / cutoff date_to).

Покрытие:
- happy path: 3 заказа → INSERT, повторный sync → UPDATE.
- cutoff date_to: заказы «сегодня» отбрасываются (skipped_future).
- isCancel=true пробрасывается на повторном sync.
- multi-tenancy: данные одного проекта не видны другому.
- дедуп по srid (если WB вернул дубль).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest
import pytz
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import WbOrder
from backend.services.wb_orders_sync_service import sync_wb_orders

_MSK = pytz.timezone("Europe/Moscow")


def _mock_order(
    srid: str,
    nm_id: int,
    *,
    days_ago: int = 5,
    warehouse_name: str = "Коледино",
    warehouse_type: str = "Склад WB",
    okrug: str = "Центральный федеральный округ",
    region: str = "Москва",
    country: str = "Россия",
    is_cancel: bool = False,
    cancel_dt: datetime | None = None,
    last_change_dt: datetime | None = None,
    total_price: float = 1500.0,
    finished_price: float = 900.0,
) -> dict[str, Any]:
    """Сконструировать WB API dict для одного заказа."""
    order_dt = datetime.now(_MSK) - timedelta(days=days_ago)
    last_change = last_change_dt or order_dt
    return {
        "srid": srid,
        "gNumber": f"G{srid}",
        "sticker": f"S{srid}",
        "incomeID": 12345,
        "date": order_dt.strftime("%Y-%m-%dT%H:%M:%S"),
        "lastChangeDate": last_change.strftime("%Y-%m-%dT%H:%M:%S"),
        "nmId": nm_id,
        "barcode": f"BC{nm_id}",
        "supplierArticle": f"VC-{nm_id}",
        "subject": "Платье",
        "brand": "TestBrand",
        "category": "Одежда",
        "techSize": "M",
        "warehouseName": warehouse_name,
        "warehouseType": warehouse_type,
        "countryName": country,
        "oblastOkrugName": okrug,
        "regionName": region,
        "totalPrice": total_price,
        "discountPercent": 40,
        "spp": 5,
        "finishedPrice": finished_price,
        "priceWithDisc": finished_price,
        "isCancel": is_cancel,
        "cancelDate": cancel_dt.strftime("%Y-%m-%dT%H:%M:%S") if cancel_dt else None,
        "isSupply": False,
        "isRealization": True,
    }


async def _seed_wb_key(db: AsyncSession, project_id: int) -> None:
    """Создать минимальный IntegrationKey 'wb' чтобы get_wb_key вернул что-то."""
    from backend.utils.crypto import encrypt

    enc = encrypt("test-wb-key-12345")
    await db.execute(
        text(
            "INSERT INTO integration_keys "
            "(project_id, service, encrypted_key, is_active, is_deleted, created_at) "
            "VALUES (:pid, 'wb', :ek, true, false, NOW())"
        ),
        {"pid": project_id, "ek": enc},
    )
    await db.commit()


async def _count_orders(db: AsyncSession, project_id: int) -> int:
    res = await db.execute(select(WbOrder).where(WbOrder.project_id == project_id))
    return len(list(res.scalars()))


# ═══════════════════════════════════════════════════════════════════════════════
# Happy path / UPSERT idempotency
# ═══════════════════════════════════════════════════════════════════════════════


class TestSyncHappyPath:
    @pytest.mark.asyncio
    async def test_first_sync_inserts_all(self, db_session: AsyncSession, project):
        """Первый sync — все заказы INSERT, none UPDATE."""
        await _seed_wb_key(db_session, project.id)
        mock_orders = [
            _mock_order("ORD-001", 100, days_ago=5),
            _mock_order("ORD-002", 101, days_ago=5),
            _mock_order("ORD-003", 102, days_ago=5),
        ]
        with patch(
            "backend.services.wb_orders_sync_service.fetch_supplier_orders",
            return_value=mock_orders,
        ):
            result = await sync_wb_orders(db_session, project.id, days_back=30)
        assert result["total_fetched"] == 3
        assert result["inserted"] == 3
        assert result["updated"] == 0
        assert result["skipped_future"] == 0
        assert result["errors"] == []
        assert await _count_orders(db_session, project.id) == 3

    @pytest.mark.asyncio
    async def test_second_sync_updates_existing(self, db_session: AsyncSession, project):
        """Повторный sync — все updated, no inserts."""
        await _seed_wb_key(db_session, project.id)
        mock_orders = [_mock_order("ORD-100", 200, days_ago=3)]
        with patch(
            "backend.services.wb_orders_sync_service.fetch_supplier_orders",
            return_value=mock_orders,
        ):
            await sync_wb_orders(db_session, project.id, days_back=30)
            result2 = await sync_wb_orders(db_session, project.id, days_back=30)
        assert result2["inserted"] == 0
        assert result2["updated"] == 1

    @pytest.mark.asyncio
    async def test_cancel_flag_propagates_on_resync(self, db_session: AsyncSession, project):
        """Заказ отменили в WB → второй sync обновит is_cancel=true."""
        await _seed_wb_key(db_session, project.id)
        # 1) Первоначальный sync — заказ активный
        first = [_mock_order("CANCEL-1", 300, days_ago=5, is_cancel=False)]
        with patch(
            "backend.services.wb_orders_sync_service.fetch_supplier_orders",
            return_value=first,
        ):
            await sync_wb_orders(db_session, project.id, days_back=30)

        # 2) WB пометил заказ отменённым
        cancel_dt = datetime.now(_MSK) - timedelta(days=1)
        second = [
            _mock_order(
                "CANCEL-1",
                300,
                days_ago=5,
                is_cancel=True,
                cancel_dt=cancel_dt,
            )
        ]
        with patch(
            "backend.services.wb_orders_sync_service.fetch_supplier_orders",
            return_value=second,
        ):
            result = await sync_wb_orders(db_session, project.id, days_back=30)
        assert result["updated"] == 1

        # Проверим в БД
        await db_session.commit()  # ensure session sees committed UPSERT
        res = await db_session.execute(
            select(WbOrder).where(
                WbOrder.project_id == project.id,
                WbOrder.srid == "CANCEL-1",
            )
        )
        order = res.scalar_one()
        assert order.is_cancel is True
        assert order.cancel_date is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Cutoff date_to (yesterday EOD)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCutoffDate:
    @pytest.mark.asyncio
    async def test_today_orders_skipped(self, db_session: AsyncSession, project):
        """Заказы с order_date > вчера 23:59 MSK не сохраняются."""
        await _seed_wb_key(db_session, project.id)
        # days_ago=0 — сегодняшний заказ → должен быть skipped_future
        mock_orders = [
            _mock_order("TODAY-1", 400, days_ago=0),
            _mock_order("YESTERDAY-1", 401, days_ago=2),  # вчера+ → ok
        ]
        with patch(
            "backend.services.wb_orders_sync_service.fetch_supplier_orders",
            return_value=mock_orders,
        ):
            result = await sync_wb_orders(db_session, project.id, days_back=30)
        assert result["total_fetched"] == 2
        assert result["skipped_future"] == 1
        assert result["inserted"] == 1
        assert await _count_orders(db_session, project.id) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-tenancy
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiTenancy:
    @pytest.mark.asyncio
    async def test_project_isolation(self, db_session: AsyncSession, project, other_project):
        """Sync для проекта 1 не должен видеть/трогать заказы проекта 2."""
        await _seed_wb_key(db_session, project.id)
        await _seed_wb_key(db_session, other_project.id)

        # Запишем в проект 2 заказ напрямую (тот же srid что в проекте 1)
        # чтобы проверить (project_id, srid) UPSERT working
        mock_for_other = [_mock_order("DUP-SRID", 500, days_ago=5)]
        with patch(
            "backend.services.wb_orders_sync_service.fetch_supplier_orders",
            return_value=mock_for_other,
        ):
            await sync_wb_orders(db_session, other_project.id, days_back=30)
        assert await _count_orders(db_session, other_project.id) == 1
        assert await _count_orders(db_session, project.id) == 0

        # Теперь sync для проекта 1 с тем же srid — должен сделать INSERT в проекте 1
        mock_for_main = [_mock_order("DUP-SRID", 500, days_ago=5)]
        with patch(
            "backend.services.wb_orders_sync_service.fetch_supplier_orders",
            return_value=mock_for_main,
        ):
            res = await sync_wb_orders(db_session, project.id, days_back=30)
        assert res["inserted"] == 1
        assert await _count_orders(db_session, project.id) == 1
        # Проект 2 остался с 1 строкой
        assert await _count_orders(db_session, other_project.id) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Дедуп по srid (защита от дублей в одном batch)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeduplication:
    @pytest.mark.asyncio
    async def test_dup_srid_in_batch_collapsed(self, db_session: AsyncSession, project):
        """Если WB вернул две строки с одинаковым srid → дедуп до UPSERT
        (защита от ON CONFLICT CardinalityViolation, прецедент wb_stocks).
        """
        await _seed_wb_key(db_session, project.id)
        mock_orders = [
            _mock_order("DUP-1", 600, days_ago=5),
            _mock_order("DUP-1", 600, days_ago=5),  # дубль srid
            _mock_order("UNIQ-1", 601, days_ago=5),
        ]
        with patch(
            "backend.services.wb_orders_sync_service.fetch_supplier_orders",
            return_value=mock_orders,
        ):
            result = await sync_wb_orders(db_session, project.id, days_back=30)
        # 3 fetched, 1 dropped как дубль, 2 inserted
        assert result["total_fetched"] == 3
        assert result["inserted"] == 2
        assert await _count_orders(db_session, project.id) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# No WB key
# ═══════════════════════════════════════════════════════════════════════════════


class TestNoApiKey:
    @pytest.mark.asyncio
    async def test_no_key_returns_empty_stats(self, db_session: AsyncSession, project):
        """Если у проекта нет WB-ключа — sync возвращает empty stats без ошибок."""
        # Не делаем _seed_wb_key
        result = await sync_wb_orders(db_session, project.id, days_back=30)
        assert result["inserted"] == 0
        assert result["updated"] == 0
        assert "no WB API key" in result["errors"][0]
