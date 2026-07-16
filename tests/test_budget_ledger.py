"""
Tests for get_budget_ledger (backend/services/funnel/ads_manager.py).

Единая лента движения бюджета из данных WB: пополнения кампании (рост бюджета выше порога),
пополнения счёта кабинета (только в режиме «все кампании») и списания (со знаком минус).
"""

from datetime import datetime, timedelta

from backend.models.integrations import WbAdCampaignEvent, WbAdPayment, WbAdUpd
from backend.services.funnel.ads_manager import get_budget_ledger

CID = 4242
BASE = datetime(2026, 7, 15, 10, 0, 0)


async def _seed(db, project_id):
    db.add_all([
        # пополнение кампании выше порога — учитывается
        WbAdCampaignEvent(project_id=project_id, campaign_id=CID, event_type="budget_change",
                          old_value="0.0", new_value="1000.0", created_at=BASE),
        # рост ниже порога (джиттер между синками) — НЕ пополнение
        WbAdCampaignEvent(project_id=project_id, campaign_id=CID, event_type="budget_change",
                          old_value="100.0", new_value="110.0", created_at=BASE - timedelta(hours=1)),
        # уменьшение бюджета (трата) — НЕ пополнение
        WbAdCampaignEvent(project_id=project_id, campaign_id=CID, event_type="budget_change",
                          old_value="500.0", new_value="100.0", created_at=BASE - timedelta(hours=2)),
        # событие другого типа — игнор
        WbAdCampaignEvent(project_id=project_id, campaign_id=CID, event_type="status_change",
                          old_value="9", new_value="11", created_at=BASE),
        # списание
        WbAdUpd(project_id=project_id, advert_id=CID, upd_time=BASE - timedelta(minutes=30),
                upd_sum=500, upd_num=777, payment_type="Баланс"),
        # пополнение счёта кабинета (уровень аккаунта, без кампании)
        WbAdPayment(project_id=project_id, wb_id=999, paid_at=BASE - timedelta(minutes=10),
                    amount=3000, payment_type=1, status_id=1),
    ])
    await db.commit()


async def test_ledger_campaign_scope(db_session, project):
    """По кампании: только пополнение выше порога (+1000) и списание (−500); счёт кабинета исключён."""
    await _seed(db_session, project.id)
    led = await get_budget_ledger(db_session, project.id, CID)
    topups = [e["amount"] for e in led if e["kind"] == "campaign_topup"]
    assert topups == [1000.0]  # джиттер (+10), уменьшение и status_change отсеяны
    charges = [e["amount"] for e in led if e["kind"] == "charge"]
    assert charges == [-500.0]  # списание со знаком минус
    assert all(e["kind"] != "account_topup" for e in led)  # счёт — только в «все кампании»
    # лента отсортирована по времени, новые первыми
    assert [e["ts"] for e in led] == sorted((e["ts"] for e in led), reverse=True)


async def test_ledger_all_scope_includes_account(db_session, project):
    """Режим «все кампании» добавляет пополнения счёта кабинета к пополнениям кампаний и списаниям."""
    await _seed(db_session, project.id)
    led = await get_budget_ledger(db_session, project.id, None)
    assert any(e["kind"] == "account_topup" and e["amount"] == 3000.0 for e in led)
    assert any(e["kind"] == "campaign_topup" and e["amount"] == 1000.0 for e in led)
    assert any(e["kind"] == "charge" and e["amount"] == -500.0 for e in led)


async def test_ledger_kind_split(db_session, project):
    """kind='topup' — только пополнения (+), kind='charge' — только списания (−); вкладки не смешаны."""
    await _seed(db_session, project.id)
    topups = await get_budget_ledger(db_session, project.id, None, "topup")
    charges = await get_budget_ledger(db_session, project.id, None, "charge")
    assert topups and all(e["amount"] > 0 and e["kind"] != "charge" for e in topups)
    assert charges and all(e["amount"] < 0 and e["kind"] == "charge" for e in charges)
