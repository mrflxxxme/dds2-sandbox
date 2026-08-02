# ruff: noqa: RUF002, RUF003 — русские строки в тест-данных
"""Тесты слежения за поступлением товара (wb_stock_watches): классификатор, скан, тик."""

import pytest

from backend.models import WBFeedbackReply, WBQuestion, WBStockWatch
from backend.services import reply_service, stock_watch_service
from backend.services.ai import reply_llm
from backend.utils.time import utcnow


# ─── is_stock_question ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Когда появится в наличии?",
        "Когда появится 46 размер?",
        "Здравствуйте, когда будет в наличии размер S?",
        "Когда будет поступление товара?",
        "Скажите, когда завезёте эту модель?",
        "Когда завезут?",
        "Появится ли в продаже?",
        "Есть ли в наличии?",
        "Нет в наличии нужного цвета, когда привезут?",
        "Когда ожидается поступление?",
        "Подскажите, скоро будет размер XXL?",
        "Когда привезут товар?",
        "Do you have restock planned?",
    ],
)
def test_is_stock_question_positive(text):
    assert stock_watch_service.is_stock_question(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Какой размер выбрать на обхват 90?",
        "Какой состав ткани?",
        "Когда будет доставка в ПВЗ?",  # «когда будет» + доставка — НЕ наличие
        "Когда будет отправка заказа?",
        "Есть ли гарантия на товар?",
        "Цвет соответствует фото?",
        "Что входит в комплект?",
        "Спасибо за быструю доставку!",
        "",
        None,
    ],
)
def test_is_stock_question_negative(text):
    assert stock_watch_service.is_stock_question(text) is False


# ─── helpers ─────────────────────────────────────────────────────────────────


async def _add_question(db, project_id: int, wb_id: str, nm_id: int = 111,
                        text: str = "Когда появится в наличии?", *, answered: bool = False):
    db.add(
        WBQuestion(
            project_id=project_id,
            wb_id=wb_id,
            nm_id=nm_id,
            text=text,
            is_answered=answered,
            created_date=utcnow(),
            product_name="Товар",
            brand="Бренд",
        )
    )


async def _add_watch(db, project_id: int, question_wb_id: str, nm_id: int = 111,
                     status: str = "watching") -> WBStockWatch:
    w = WBStockWatch(project_id=project_id, nm_id=nm_id, question_wb_id=question_wb_id, status=status)
    db.add(w)
    await db.commit()
    await db.refresh(w)
    return w


# ─── scan_stock_questions (backfill) ─────────────────────────────────────────


async def test_scan_creates_watches_for_stock_questions(db_session, project):
    await _add_question(db_session, project.id, "q1", text="Когда появится в наличии?")
    await _add_question(db_session, project.id, "q2", text="Какой состав?")  # не про наличие
    await _add_question(db_session, project.id, "q3", text="Когда завезут?", answered=True)  # отвечен
    await db_session.commit()

    res = await stock_watch_service.scan_stock_questions(db_session, project.id)
    assert res["created"] == 1
    assert res["scanned"] == 1

    lst = await stock_watch_service.list_stock_watches(db_session, project.id)
    assert lst["counts"]["watching"] == 1
    assert lst["items"][0]["question_wb_id"] == "q1"
    assert lst["items"][0]["question_text"] == "Когда появится в наличии?"


async def test_scan_idempotent_rerun(db_session, project):
    await _add_question(db_session, project.id, "q1")
    await db_session.commit()

    first = await stock_watch_service.scan_stock_questions(db_session, project.id)
    second = await stock_watch_service.scan_stock_questions(db_session, project.id)
    assert first["created"] == 1
    assert second["created"] == 0  # повторный скан не плодит дубли
    lst = await stock_watch_service.list_stock_watches(db_session, project.id)
    assert lst["total"] == 1


async def test_scan_dismisses_watch_for_answered_question(db_session, project):
    await _add_question(db_session, project.id, "q1", answered=False)
    await db_session.commit()
    await stock_watch_service.scan_stock_questions(db_session, project.id)

    # вопрос получил ответ (вручную / другим путём) — слежение снимается
    from sqlalchemy import select

    q = (
        await db_session.execute(
            select(WBQuestion).where(
                WBQuestion.project_id == project.id, WBQuestion.wb_id == "q1"
            )
        )
    ).scalar_one()
    q.is_answered = True
    await db_session.commit()

    res = await stock_watch_service.scan_stock_questions(db_session, project.id)
    assert res["dismissed"] == 1
    lst = await stock_watch_service.list_stock_watches(db_session, project.id)
    assert lst["counts"]["dismissed"] == 1
    assert lst["counts"]["watching"] == 0


# ─── stock_watch_tick ────────────────────────────────────────────────────────


async def test_tick_drafts_when_in_stock_template_without_llm(db_session, project, monkeypatch):
    """Остаток появился + нет LLM-ключа → шаблонный черновик draft, watch → drafted."""
    await _add_question(db_session, project.id, "q1")
    watch = await _add_watch(db_session, project.id, "q1")
    monkeypatch.setattr(stock_watch_service.settings, "COMPLAINT_LLM_API_KEY", "")

    async def fake_fetch(nm_ids):
        return {111: 5}  # товар появился

    res = await stock_watch_service.stock_watch_tick(db_session, project.id, fetcher=fake_fetch)
    assert res == {"checked": 1, "drafted": 1, "waiting": 0, "errors": 0}

    from sqlalchemy import select

    w = (
        await db_session.execute(select(WBStockWatch).where(WBStockWatch.id == watch.id))
    ).scalar_one()
    assert w.status == "drafted"
    assert w.reply_id is not None
    assert w.resolved_at is not None

    reply = (
        await db_session.execute(
            select(WBFeedbackReply).where(WBFeedbackReply.id == w.reply_id)
        )
    ).scalar_one()
    assert reply.status == "draft"  # отправка — только ручная
    assert reply.source == "agent"
    assert reply.is_stock_reply is True
    assert reply.generation == "template"
    assert "в наличии" in reply.draft_text  # шаблон RESTOCK_TEMPLATE
    assert reply.target_type == "question"
    assert reply.target_wb_id == "q1"


async def test_tick_waits_when_out_of_stock(db_session, project):
    """Остатка нет → watch остаётся watching, черновик не создаётся."""
    await _add_question(db_session, project.id, "q1")
    await _add_watch(db_session, project.id, "q1")

    async def fake_fetch(nm_ids):
        return {111: 0}

    res = await stock_watch_service.stock_watch_tick(db_session, project.id, fetcher=fake_fetch)
    assert res == {"checked": 1, "drafted": 0, "waiting": 1, "errors": 0}

    rows = await reply_service.list_replies(db_session, project.id)
    assert rows["total"] == 0


async def test_tick_network_error_keeps_watching(db_session, project):
    """Сбой сети не валит тик и не трогает watches."""
    await _add_question(db_session, project.id, "q1")
    await _add_watch(db_session, project.id, "q1")

    async def failing_fetch(nm_ids):
        raise RuntimeError("WB cards/v4/detail HTTP 403")

    res = await stock_watch_service.stock_watch_tick(db_session, project.id, fetcher=failing_fetch)
    assert res["errors"] == 1
    assert res["drafted"] == 0

    lst = await stock_watch_service.list_stock_watches(db_session, project.id)
    assert lst["counts"]["watching"] == 1  # ждём следующий тик


async def test_tick_dismisses_when_reply_already_exists(db_session, project):
    """На вопрос уже есть открытый ответ — дубль не нужен: watch → dismissed."""
    await _add_question(db_session, project.id, "q1")
    await _add_watch(db_session, project.id, "q1")
    db_session.add(
        WBFeedbackReply(
            project_id=project.id, target_type="question", target_wb_id="q1",
            draft_text="уже ответили", status="draft", source="manual",
        )
    )
    await db_session.commit()

    async def fake_fetch(nm_ids):
        return {111: 3}

    res = await stock_watch_service.stock_watch_tick(db_session, project.id, fetcher=fake_fetch)
    assert res["drafted"] == 0
    lst = await stock_watch_service.list_stock_watches(db_session, project.id)
    assert lst["counts"]["dismissed"] == 1


async def test_tick_llm_draft_when_key_present(db_session, project, monkeypatch):
    """С LLM-ключом черновик генерирует модель (мок draft_reply), generation='llm'."""
    await _add_question(db_session, project.id, "q1", nm_id=111, text="Когда появится размер M?")
    await _add_watch(db_session, project.id, "q1", nm_id=111)
    monkeypatch.setattr(stock_watch_service.settings, "COMPLAINT_LLM_API_KEY", "test-key")

    captured: dict = {}

    async def fake_draft(*args, **kwargs):
        captured["rules"] = args[3]  # rules — 4-й позиционный параметр draft_reply
        return {"reply_text": "Здравствуйте! Размер M снова в наличии.", "needs_info": False, "used_kb_ids": []}

    monkeypatch.setattr(reply_llm, "draft_reply", fake_draft)

    async def fake_fetch(nm_ids):
        return {111: 7}

    res = await stock_watch_service.stock_watch_tick(db_session, project.id, fetcher=fake_fetch)
    assert res["drafted"] == 1
    assert "появился" in captured["rules"] or "наличии" in captured["rules"]  # контекст поступления

    from sqlalchemy import select

    reply = (
        await db_session.execute(
            select(WBFeedbackReply).where(
                WBFeedbackReply.project_id == project.id, WBFeedbackReply.target_wb_id == "q1"
            )
        )
    ).scalar_one()
    assert reply.generation == "llm"
    assert "в наличии" in reply.draft_text


async def test_tick_llm_failure_falls_back_to_template(db_session, project, monkeypatch):
    """LLM упал → шаблонный черновик (тик не валится)."""
    await _add_question(db_session, project.id, "q1")
    await _add_watch(db_session, project.id, "q1")
    monkeypatch.setattr(stock_watch_service.settings, "COMPLAINT_LLM_API_KEY", "test-key")

    async def failing_draft(*args, **kwargs):
        raise ValueError("LLM down")

    monkeypatch.setattr(reply_llm, "draft_reply", failing_draft)

    async def fake_fetch(nm_ids):
        return {111: 2}

    res = await stock_watch_service.stock_watch_tick(db_session, project.id, fetcher=fake_fetch)
    assert res["drafted"] == 1
    assert res["errors"] == 0

    from sqlalchemy import select

    reply = (
        await db_session.execute(
            select(WBFeedbackReply).where(
                WBFeedbackReply.project_id == project.id, WBFeedbackReply.target_wb_id == "q1"
            )
        )
    ).scalar_one()
    assert reply.generation == "template"
    assert reply.draft_text == stock_watch_service.RESTOCK_TEMPLATE


async def test_tick_no_watches_noop(db_session, project):
    res = await stock_watch_service.stock_watch_tick(db_session, project.id)
    assert res == {"checked": 0, "drafted": 0, "waiting": 0, "errors": 0}


# ─── last_qty в тике ─────────────────────────────────────────────────────────


async def test_tick_writes_last_qty_when_drafted(db_session, project, monkeypatch):
    """Товар появился → watch → drafted, last_qty = остаток из fetcher."""
    await _add_question(db_session, project.id, "q1")
    watch = await _add_watch(db_session, project.id, "q1")
    monkeypatch.setattr(stock_watch_service.settings, "COMPLAINT_LLM_API_KEY", "")

    async def fake_fetch(nm_ids):
        return {111: 5}

    res = await stock_watch_service.stock_watch_tick(db_session, project.id, fetcher=fake_fetch)
    assert res["drafted"] == 1

    from sqlalchemy import select

    w = (
        await db_session.execute(select(WBStockWatch).where(WBStockWatch.id == watch.id))
    ).scalar_one()
    assert w.status == "drafted"
    assert w.last_qty == 5


async def test_tick_writes_last_qty_when_waiting(db_session, project):
    """Остатка нет → watch остаётся watching, но last_qty фиксируется (0)."""
    await _add_question(db_session, project.id, "q1")
    watch = await _add_watch(db_session, project.id, "q1")
    assert watch.last_qty is None

    async def fake_fetch(nm_ids):
        return {111: 0}

    res = await stock_watch_service.stock_watch_tick(db_session, project.id, fetcher=fake_fetch)
    assert res["waiting"] == 1

    from sqlalchemy import select

    w = (
        await db_session.execute(select(WBStockWatch).where(WBStockWatch.id == watch.id))
    ).scalar_one()
    assert w.status == "watching"
    assert w.last_qty == 0  # проверка была — остаток 0 записан


# ─── dismiss_stock_watch ──────────────────────────────────────────────────────


async def test_dismiss_watching_watch(db_session, project):
    """watching → dismissed (resolved_at выставляется, в списке виден last_qty=None)."""
    await _add_question(db_session, project.id, "q1")
    watch = await _add_watch(db_session, project.id, "q1")

    res = await stock_watch_service.dismiss_stock_watch(db_session, project.id, watch.id)
    assert res is not None
    assert res["status"] == "dismissed"
    assert res["resolved_at"] is not None
    assert res["question_text"] == "Когда появится в наличии?"

    lst = await stock_watch_service.list_stock_watches(db_session, project.id)
    assert lst["counts"]["dismissed"] == 1
    assert lst["counts"]["watching"] == 0


async def test_dismiss_drafted_watch_conflict(db_session, project):
    """drafted → ValueError (роутер маппит в 409)."""
    watch = await _add_watch(db_session, project.id, "q1", status="drafted")
    with pytest.raises(ValueError, match="drafted"):
        await stock_watch_service.dismiss_stock_watch(db_session, project.id, watch.id)


async def test_dismiss_dismissed_watch_conflict(db_session, project):
    """dismissed → ValueError (повторное снятие запрещено)."""
    watch = await _add_watch(db_session, project.id, "q1", status="dismissed")
    with pytest.raises(ValueError):
        await stock_watch_service.dismiss_stock_watch(db_session, project.id, watch.id)


async def test_dismiss_unknown_watch_returns_none(db_session, project):
    """Чужой/несуществующий watch → None (роутер маппит в 404)."""
    res = await stock_watch_service.dismiss_stock_watch(db_session, project.id, 999999)
    assert res is None


# ─── эндпоинт POST /stock-watches/tick ────────────────────────────────────────


async def test_tick_endpoint_manual_run(client, auth_headers, monkeypatch):
    """Ручной тик через API: checked/drafted/waiting/errors в ответе (fetcher замокан)."""
    resp = await client.post("/api/v1/projects", json={"name": "Stock Tick Test"}, headers=auth_headers)
    project_id = resp.json()["id"]
    headers = {**auth_headers, "X-Project-Id": str(project_id)}

    # вопрос о наличии + watch напрямую в БД (та же БД, что и у API через dependency override)
    from tests.conftest_api import TestSessionLocal

    async with TestSessionLocal() as db:
        db.add(
            WBQuestion(
                project_id=project_id, wb_id="q-tick", nm_id=111,
                text="Когда появится в наличии?", is_answered=False,
                created_date=utcnow(), product_name="Товар",
            )
        )
        db.add(WBStockWatch(project_id=project_id, nm_id=111, question_wb_id="q-tick", status="watching"))
        await db.commit()

    # сеть до WB в тестах недоступна — мокаем синхронный fetcher остатков
    monkeypatch.setattr(
        stock_watch_service, "fetch_total_quantities", lambda ids: {i: 0 for i in ids}
    )

    resp = await client.post("/api/v1/reviews/stock-watches/tick", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"checked": 1, "drafted": 0, "waiting": 1, "errors": 0}

    # last_qty записался — виден в списке
    resp = await client.get("/api/v1/reviews/stock-watches", headers=headers)
    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["last_qty"] == 0
    assert item["status"] == "watching"


async def test_dismiss_endpoint_flow(client, auth_headers):
    """API: dismiss watching → 200 dismissed; повторный dismiss → 409; чужой id → 404."""
    resp = await client.post("/api/v1/projects", json={"name": "Stock Dismiss Test"}, headers=auth_headers)
    project_id = resp.json()["id"]
    headers = {**auth_headers, "X-Project-Id": str(project_id)}

    from tests.conftest_api import TestSessionLocal

    async with TestSessionLocal() as db:
        w = WBStockWatch(project_id=project_id, nm_id=111, question_wb_id="q-dis", status="watching")
        db.add(w)
        await db.commit()
        await db.refresh(w)
        watch_id = w.id

    resp = await client.post(f"/api/v1/reviews/stock-watches/{watch_id}/dismiss", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"

    resp = await client.post(f"/api/v1/reviews/stock-watches/{watch_id}/dismiss", headers=headers)
    assert resp.status_code == 409

    resp = await client.post("/api/v1/reviews/stock-watches/999999/dismiss", headers=headers)
    assert resp.status_code == 404


# ─── сериализация схем ───────────────────────────────────────────────────────


def test_stock_watch_schemas_serialization():
    from backend.schemas.reviews import (
        QuestionItem,
        ReplyItem,
        StockWatchItem,
        StockWatchListResponse,
        StockWatchScanResult,
        StockWatchTickResult,
    )

    q = QuestionItem(id="q1", text="Когда появится?", has_stock_watch=True)
    assert q.model_dump()["has_stock_watch"] is True

    item = StockWatchItem(id=1, nm_id=111, question_wb_id="q1", status="watching")
    assert item.model_dump()["reply_id"] is None
    assert item.model_dump()["last_qty"] is None

    resp = StockWatchListResponse(items=[item], total=1, counts={"watching": 1})
    assert resp.model_dump()["total"] == 1

    scan = StockWatchScanResult(scanned=5, created=5, dismissed=1)
    assert scan.model_dump()["created"] == 5

    tick = StockWatchTickResult(checked=3, drafted=1, waiting=2, errors=0)
    assert tick.model_dump()["waiting"] == 2

    r = ReplyItem(
        id=1, target_type="question", target_wb_id="q1", draft_text="d", text="d",
        status="draft", source="agent", is_stock_reply=True,
    )
    assert r.model_dump()["is_stock_reply"] is True


# ─── has_stock_watch в списке вопросов ────────────────────────────────────────


async def test_list_questions_has_stock_watch_badge(db_session, project):
    await _add_question(db_session, project.id, "q1", text="Когда появится в наличии?")
    await _add_question(db_session, project.id, "q2", text="Какой состав?")
    await db_session.commit()
    await stock_watch_service.scan_stock_questions(db_session, project.id)

    data = await reply_service.list_questions(db_session, project.id, is_answered=False)
    by_id = {i["id"]: i for i in data["items"]}
    assert by_id["q1"]["has_stock_watch"] is True
    assert by_id["q2"]["has_stock_watch"] is False
