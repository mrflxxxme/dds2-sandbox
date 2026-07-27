# ruff: noqa: RUF002, RUF003 — русские строки в тест-данных
"""Тесты автоответов: reply_service (агенты/черновики/отправка) и reply_llm._parse_reply."""

import pytest

from backend.models import WBFeedback, WBFeedbackReply, WBProductKB, WBQuestion, WBReplyAgent
from backend.services import reply_service
from backend.services.ai import reply_llm
from backend.utils.crypto import encrypt
from backend.utils.time import utcnow


# ─── reply_llm._parse_reply ──────────────────────────────────────────────────


def test_parse_reply_plain_text_fallback():
    # не-JSON ответ модели трактуется как текст черновика
    parsed = reply_llm._parse_reply("  Здравствуйте! Спасибо за отзыв.  ")
    assert parsed == {"reply_text": "Здравствуйте! Спасибо за отзыв.", "needs_info": False, "used_kb_ids": []}


def test_parse_reply_json_structured():
    parsed = reply_llm._parse_reply('{"reply_text": "Да, размер в размер", "needs_info": false, "used_kb_ids": [5, "12"]}')
    assert parsed["reply_text"] == "Да, размер в размер"
    assert parsed["needs_info"] is False
    assert parsed["used_kb_ids"] == [5, 12]


def test_parse_reply_json_needs_info():
    parsed = reply_llm._parse_reply('{"reply_text": "", "needs_info": true, "used_kb_ids": []}')
    assert parsed["needs_info"] is True
    assert parsed["reply_text"] == ""


def test_parse_reply_strips_prefix_and_quotes():
    assert reply_llm._parse_reply('Ответ: «Спасибо за отзыв!»')["reply_text"] == "Спасибо за отзыв!"
    assert reply_llm._parse_reply('"Готово"')["reply_text"] == "Готово"


def test_parse_reply_truncates():
    assert len(reply_llm._parse_reply("x" * 5000)["reply_text"]) == reply_llm.MAX_REPLY_LEN


def test_parse_reply_empty():
    assert reply_llm._parse_reply("")["reply_text"] == ""
    assert reply_llm._parse_reply("")["needs_info"] is True
    assert reply_llm._parse_reply(None)["reply_text"] == ""


# ─── _row_from_question ──────────────────────────────────────────────────────


def test_row_from_question_maps_wb_fields():
    row = reply_service._row_from_question(
        1,
        {
            "id": "q1",
            "text": "Есть размер?",
            "createdDate": "2026-07-20T10:00:00Z",
            "answer": {"text": "Да"},
            "productDetails": {"nmId": 123, "productName": "Накидка", "supplierArticle": "ART1", "brandName": "Бренд"},
        },
        utcnow(),
    )
    assert row["wb_id"] == "q1"
    assert row["is_answered"] is True  # отвечен — есть answer
    assert row["answer_text"] == "Да"
    assert row["nm_id"] == 123
    assert row["created_date"].year == 2026


def test_row_from_question_no_id_returns_none():
    assert reply_service._row_from_question(1, {"text": "?"}, utcnow()) is None


# ─── helpers ─────────────────────────────────────────────────────────────────


async def _add_question(db, project_id: int, wb_id: str, *, answered: bool = False, text: str = "Вопрос?"):
    db.add(
        WBQuestion(
            project_id=project_id,
            wb_id=wb_id,
            nm_id=111,
            text=text,
            is_answered=answered,
            created_date=utcnow(),
            product_name="Товар",
            brand="Бренд",
        )
    )


async def _add_feedback(db, project_id: int, wb_id: str, rating: int = 5, *, answered: bool = False):
    db.add(
        WBFeedback(
            project_id=project_id,
            wb_id=wb_id,
            nm_id=222,
            rating=rating,
            text="Отзыв",
            has_text=True,
            is_answered=answered,
            created_date=utcnow(),
        )
    )


async def _add_agent(db, project_id: int, **kw) -> WBReplyAgent:
    a = WBReplyAgent(
        project_id=project_id,
        name=kw.pop("name", "Агент"),
        rules=kw.pop("rules", "Вежливо"),
        **kw,
    )
    db.add(a)
    await db.commit()
    await db.refresh(a)
    return a


# ─── run_reply_agent (LLM замокан) ───────────────────────────────────────────


async def _add_kb(db, project_id: int, nm_id: int, **kw):
    db.add(
        WBProductKB(
            project_id=project_id,
            nm_id=nm_id,
            topic=kw.pop("topic", "Размер"),
            question_example=kw.pop("question_example", "Какой размер?"),
            answer=kw.pop("answer", "Размер в размер."),
            source=kw.pop("source", "manual"),
            **kw,
        )
    )


async def test_run_reply_agent_drafts_feedback_and_question(db_session, project, monkeypatch):
    await _add_feedback(db_session, project.id, "fb1")
    await _add_feedback(db_session, project.id, "fb2", answered=True)  # пропуск: отвечен
    await _add_question(db_session, project.id, "q1")
    await _add_kb(db_session, project.id, 111)  # КБ для nm_id вопроса
    await _add_kb(db_session, project.id, 222)  # КБ для nm_id отзыва
    await db_session.commit()
    agent = await _add_agent(db_session, project.id, target="both")

    async def fake_draft(*args, **kwargs):
        return {"reply_text": "Черновик ответа", "needs_info": False, "used_kb_ids": []}

    monkeypatch.setattr(reply_llm, "draft_reply", fake_draft)
    monkeypatch.setattr(reply_service.settings, "COMPLAINT_LLM_API_KEY", "test-key")

    res = await reply_service.run_reply_agent(db_session, project.id, agent.id)
    assert res["checked"] == 2  # fb1 + q1 (fb2 отвечен — не отобран)
    assert res["drafted"] == 2
    assert res["errors"] == 0
    assert res["needs_info"] == 0

    rows = await reply_service.list_replies(db_session, project.id)
    assert rows["counts"]["draft"] == 2  # автоотправка отключена → всегда draft
    assert {i["target_wb_id"] for i in rows["items"]} == {"fb1", "q1"}
    assert {i["generation"] for i in rows["items"]} == {"llm"}


async def test_run_reply_agent_auto_send_ignored_stays_draft(db_session, project, monkeypatch):
    # auto_send=True у агента осознанно игнорируется: только ручное одобрение
    await _add_feedback(db_session, project.id, "fb1")
    await _add_kb(db_session, project.id, 222)
    await db_session.commit()
    agent = await _add_agent(db_session, project.id, target="feedback", auto_send=True)

    async def fake_draft(*args, **kwargs):
        return {"reply_text": "Автоответ", "needs_info": False, "used_kb_ids": []}

    monkeypatch.setattr(reply_llm, "draft_reply", fake_draft)
    monkeypatch.setattr(reply_service.settings, "COMPLAINT_LLM_API_KEY", "test-key")

    res = await reply_service.run_reply_agent(db_session, project.id, agent.id)
    assert res["drafted"] == 1
    assert res["auto_send"] is False
    rows = await reply_service.list_replies(db_session, project.id)
    assert rows["counts"]["draft"] == 1
    assert rows["counts"]["approved"] == 0  # НЕ approved, несмотря на auto_send=True


async def test_run_reply_agent_skips_busy_and_llm_error_not_fatal(db_session, project, monkeypatch):
    await _add_feedback(db_session, project.id, "fb_busy")
    await _add_feedback(db_session, project.id, "fb_err")
    await _add_kb(db_session, project.id, 222)
    db_session.add(
        WBFeedbackReply(
            project_id=project.id, target_type="feedback", target_wb_id="fb_busy",
            draft_text="уже есть", status="draft", source="agent",
        )
    )
    await db_session.commit()
    agent = await _add_agent(db_session, project.id, target="feedback")

    async def failing_draft(*args, **kwargs):
        raise ValueError("LLM down")

    monkeypatch.setattr(reply_llm, "draft_reply", failing_draft)
    monkeypatch.setattr(reply_service.settings, "COMPLAINT_LLM_API_KEY", "test-key")

    res = await reply_service.run_reply_agent(db_session, project.id, agent.id)
    assert res["checked"] == 1  # fb_busy занят — отобран только fb_err
    assert res["drafted"] == 0
    assert res["errors"] == 1  # сбой LLM не валит прогон


async def test_run_reply_agent_requires_rules(db_session, project):
    agent = await _add_agent(db_session, project.id, rules="")
    with pytest.raises(ValueError, match="Правила"):
        await reply_service.run_reply_agent(db_session, project.id, agent.id)


# ─── ручные черновики и модерация ────────────────────────────────────────────


async def test_create_draft_validations(db_session, project):
    with pytest.raises(ValueError, match="target_type"):
        await reply_service.create_draft(db_session, project.id, {"target_type": "x", "target_wb_id": "1", "text": "t"})
    with pytest.raises(ValueError, match="Пустой текст"):
        await reply_service.create_draft(db_session, project.id, {"target_type": "question", "target_wb_id": "1", "text": " "})
    with pytest.raises(ValueError, match="не найден"):
        await reply_service.create_draft(db_session, project.id, {"target_type": "question", "target_wb_id": "nope", "text": "t"})


async def test_create_draft_and_moderation_flow(db_session, project):
    await _add_question(db_session, project.id, "q1")
    await db_session.commit()

    r = await reply_service.create_draft(
        db_session, project.id, {"target_type": "question", "target_wb_id": "q1", "text": "Черновик"}
    )
    assert r["status"] == "draft"
    assert r["source"] == "manual"
    assert r["target"]["text"] == "Вопрос?"  # данные цели из зеркала

    r = await reply_service.update_draft(db_session, project.id, r["id"], {"text": "Правка", "action": "approve"})
    assert r["status"] == "approved"
    assert r["text"] == "Правка"  # final_text побеждает draft_text

    r = await reply_service.update_draft(db_session, project.id, r["id"], {"action": "reject"})
    assert r["status"] == "rejected"
    r = await reply_service.update_draft(db_session, project.id, r["id"], {"action": "reopen"})
    assert r["status"] == "draft"


async def test_update_sent_reply_forbidden(db_session, project):
    await _add_question(db_session, project.id, "q1")
    db_session.add(
        WBFeedbackReply(
            project_id=project.id, target_type="question", target_wb_id="q1",
            draft_text="отправлен", status="sent", source="manual",
        )
    )
    await db_session.commit()
    from sqlalchemy import select

    rid = (
        await db_session.execute(
            select(WBFeedbackReply.id).where(
                WBFeedbackReply.project_id == project.id, WBFeedbackReply.target_wb_id == "q1"
            )
        )
    ).scalar_one()
    with pytest.raises(ValueError, match="уже отправлен"):
        await reply_service.update_draft(db_session, project.id, rid, {"text": "x"})


# ─── send_pending_replies (WB API замокан) ───────────────────────────────────


async def _add_wb_key(db, project_id: int):
    from backend.models import IntegrationKey

    db.add(
        IntegrationKey(
            project_id=project_id,
            service="wb_feedbacks",
            label="test",
            encrypted_key=encrypt("fake-wb-key"),
            is_active=True,
        )
    )
    await db.commit()


async def test_send_pending_replies_success(db_session, project, monkeypatch):
    await _add_wb_key(db_session, project.id)
    await _add_question(db_session, project.id, "q1")
    db_session.add(
        WBFeedbackReply(
            project_id=project.id, target_type="question", target_wb_id="q1",
            draft_text="Ответ", status="approved", source="manual",
        )
    )
    await db_session.commit()

    calls = []

    async def fake_answer_question(self, qid, text):
        calls.append((qid, text))
        return True

    monkeypatch.setattr(
        "backend.integrations.wb_api.WBApiClient.answer_question", fake_answer_question
    )

    res = await reply_service.send_pending_replies(db_session, project.id)
    assert res == {"sent": 1, "errors": 0, "pending": 0}
    assert calls == [("q1", "Ответ")]

    rows = await reply_service.list_replies(db_session, project.id)
    assert rows["counts"]["sent"] == 1
    assert rows["items"][0]["sent_at"] is not None
    # зеркало обновлено
    from sqlalchemy import select

    q = (
        await db_session.execute(
            select(WBQuestion).where(
                WBQuestion.project_id == project.id, WBQuestion.wb_id == "q1"
            )
        )
    ).scalar_one()
    assert q.is_answered is True
    assert q.answer_text == "Ответ"


async def test_send_pending_replies_wb_error_marks_error(db_session, project, monkeypatch):
    await _add_wb_key(db_session, project.id)
    db_session.add(
        WBFeedbackReply(
            project_id=project.id, target_type="feedback", target_wb_id="fb1",
            draft_text="Ответ", status="approved", source="agent",
        )
    )
    await db_session.commit()

    async def failing_answer(self, fid, text):
        raise ValueError("WB API error: HTTP 400 — bad id")

    monkeypatch.setattr(
        "backend.integrations.wb_api.WBApiClient.answer_feedback", failing_answer
    )

    res = await reply_service.send_pending_replies(db_session, project.id)
    assert res["sent"] == 0
    assert res["errors"] == 1
    rows = await reply_service.list_replies(db_session, project.id)
    assert rows["counts"]["error"] == 1
    assert "HTTP 400" in rows["items"][0]["error"]


async def test_send_pending_replies_no_key(db_session, project):
    with pytest.raises(ValueError, match="WB-ключа"):
        await reply_service.send_pending_replies(db_session, project.id)


# ─── сериализация схем ───────────────────────────────────────────────────────


def test_schemas_serialization():
    from backend.schemas.reviews import (
        QuestionItem,
        RepliesListResponse,
        ReplyAgentItem,
        ReplyItem,
        ReplyTarget,
    )

    q = QuestionItem(id="q1", text="текст", is_answered=False)
    assert q.model_dump()["id"] == "q1"

    agent = ReplyAgentItem(id=1, name="a", rules="r")
    assert agent.model_dump()["auto_send"] is False

    item = ReplyItem(
        id=1, target_type="question", target_wb_id="q1", draft_text="d", text="d",
        status="draft", source="manual",
        target=ReplyTarget(text="вопрос", rating=None),
    )
    assert item.model_dump()["target"]["text"] == "вопрос"

    resp = RepliesListResponse(items=[item], total=1, counts={"draft": 1})
    assert resp.model_dump()["total"] == 1
