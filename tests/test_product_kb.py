# ruff: noqa: RUF002, RUF003 — русские строки в тест-данных
"""Тесты базы знаний товаров (wb_product_kb): классификация, импорт, КБ в прогоне агента."""

import pytest

from backend.models import WBFeedback, WBFeedbackReply, WBProductKB, WBQuestion, WBReplyAgent
from backend.services import reply_service
from backend.services.ai import reply_llm
from backend.utils.time import utcnow


# ─── classify_kb_topic ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "topic"),
    [
        ("Подскажите, какой размер выбрать на обхват 90?", "Размер"),
        ("Товар маломерит?", "Размер"),
        ("Когда будет доставка в ПВЗ?", "Доставка"),
        ("Какой срок отправки?", "Доставка"),
        ("Есть брак на шве, швы разошлись", "Качество"),
        ("Какое качество материала?", "Качество"),
        ("Какой состав ткани, есть ли хлопок?", "Состав"),
        ("Цвет соответствует фото?", "Цвет"),
        ("Что входит в комплект?", "Комплект"),
        ("Сколько штук в упаковке?", "Комплект"),
        ("Есть ли гарантия на товар?", "Гарантия"),
        ("Можно ли оформить возврат?", "Гарантия"),
        ("Спасибо за товар!", "Прочее"),
        ("", "Прочее"),
        (None, "Прочее"),
    ],
)
def test_classify_kb_topic(text, topic):
    assert reply_service.classify_kb_topic(text) == topic


# ─── helpers ─────────────────────────────────────────────────────────────────


async def _add_answered_question(db, project_id: int, wb_id: str, nm_id: int = 111, text: str = "Вопрос?", answer: str = "Ответ."):
    db.add(
        WBQuestion(
            project_id=project_id,
            wb_id=wb_id,
            nm_id=nm_id,
            text=text,
            answer_text=answer,
            is_answered=True,
            created_date=utcnow(),
            product_name="Товар",
            article="ART1",
            brand="Бренд",
        )
    )


async def _add_unanswered_question(db, project_id: int, wb_id: str, nm_id: int = 111, text: str = "Новый вопрос?"):
    db.add(
        WBQuestion(
            project_id=project_id,
            wb_id=wb_id,
            nm_id=nm_id,
            text=text,
            is_answered=False,
            created_date=utcnow(),
            product_name="Товар",
            brand="Бренд",
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


# ─── import_kb_from_answered_questions ───────────────────────────────────────


async def test_import_kb_creates_entries_by_nm(db_session, project):
    await _add_answered_question(db_session, project.id, "q1", nm_id=111, text="Какой размер?", answer="M.")
    await _add_answered_question(db_session, project.id, "q2", nm_id=111, text="Какая доставка?", answer="3 дня.")
    await _add_answered_question(db_session, project.id, "q3", nm_id=222, text="Какой состав?", answer="Хлопок.")
    # неотвеченный и пустой ответ — в импорт не идут
    await _add_unanswered_question(db_session, project.id, "q4", nm_id=111)
    await db_session.commit()

    res = await reply_service.import_kb_from_answered_questions(db_session, project.id)
    assert res["created"] == 3
    assert res["nm_count"] == 2
    assert res["source_questions"] == 3

    kb = await reply_service.list_kb(db_session, project.id, nm_id=111)
    assert kb["total"] == 2
    topics = {i["topic"] for i in kb["items"]}
    assert topics == {"Размер", "Доставка"}  # тема — эвристикой по тексту вопроса
    assert all(i["source"] == "import" for i in kb["items"])


async def test_import_kb_dedup_on_rerun(db_session, project):
    await _add_answered_question(db_session, project.id, "q1", text="Какой размер?", answer="M.")
    # дубль текста вопроса с другим wb_id — тоже дедупится по md5
    await _add_answered_question(db_session, project.id, "q2", text="какой  размер?", answer="L.")
    await db_session.commit()

    first = await reply_service.import_kb_from_answered_questions(db_session, project.id)
    assert first["created"] == 1
    assert first["skipped_dupe"] == 1

    second = await reply_service.import_kb_from_answered_questions(db_session, project.id)
    assert second["created"] == 0
    assert second["skipped_dupe"] == 2  # повторный импорт ничего не плодит

    kb = await reply_service.list_kb(db_session, project.id)
    assert kb["total"] == 1


# ─── rank_kb_entries / find_direct_kb_match ──────────────────────────────────


def test_rank_kb_prefers_thematic_match():
    entries = [
        {"id": 1, "topic": "Доставка", "question_example": "Когда доставка?", "answer": "3 дня"},
        {"id": 2, "topic": "Размер", "question_example": "Какой размер выбрать?", "answer": "M"},
    ]
    ranked = reply_service.rank_kb_entries(entries, "Подскажите размер")
    assert ranked[0]["id"] == 2  # совпадение слова «размер» с темой/примером


def test_find_direct_kb_match_exact():
    entries = [
        {"id": 1, "topic": "Размер", "question_example": "Какой размер выбрать?", "answer": "M"},
    ]
    hit = reply_service.find_direct_kb_match(entries, "какой   РАЗМЕР выбрать?")
    assert hit is not None and hit["id"] == 1
    assert reply_service.find_direct_kb_match(entries, "А какой состав?") is None


# ─── run_reply_agent с базой знаний ──────────────────────────────────────────


async def test_run_agent_without_kb_needs_info_no_llm(db_session, project, monkeypatch):
    """Нет записей КБ по товару → draft с needs_info, LLM НЕ вызывается."""
    await _add_unanswered_question(db_session, project.id, "q1", nm_id=999)
    await db_session.commit()
    agent = await _add_agent(db_session, project.id, target="question")

    async def llm_must_not_run(*args, **kwargs):
        raise AssertionError("LLM не должен вызываться без записей КБ")

    monkeypatch.setattr(reply_llm, "draft_reply", llm_must_not_run)
    monkeypatch.setattr(reply_service.settings, "COMPLAINT_LLM_API_KEY", "test-key")

    res = await reply_service.run_reply_agent(db_session, project.id, agent.id)
    assert res["drafted"] == 1
    assert res["needs_info"] == 1
    assert res["errors"] == 0

    rows = await reply_service.list_replies(db_session, project.id)
    item = rows["items"][0]
    assert item["status"] == "draft"
    assert item["needs_info"] is True
    assert item["draft_text"] == ""
    assert item["generation"] is None


async def test_run_agent_with_kb_prompt_contains_facts(db_session, project, monkeypatch):
    """Есть записи КБ → LLM вызывается, факты КБ попадают в промпт."""
    await _add_unanswered_question(db_session, project.id, "q1", nm_id=111, text="Какой размер подойдёт?")
    db_session.add(
        WBProductKB(
            project_id=project.id, nm_id=111, topic="Размер",
            question_example="Какой размер?", answer="Размерная сетка: M на обхват 90.",
            source="manual",
        )
    )
    await db_session.commit()
    agent = await _add_agent(db_session, project.id, target="question")

    captured: dict = {}

    async def fake_draft(*args, **kwargs):
        captured.update(kwargs)
        return {"reply_text": "M на обхват 90.", "needs_info": False, "used_kb_ids": []}

    monkeypatch.setattr(reply_llm, "draft_reply", fake_draft)
    monkeypatch.setattr(reply_service.settings, "COMPLAINT_LLM_API_KEY", "test-key")

    res = await reply_service.run_reply_agent(db_session, project.id, agent.id)
    assert res["drafted"] == 1
    assert res["needs_info"] == 0

    kb_entries = captured["kb_entries"]
    assert len(kb_entries) == 1
    assert "обхват 90" in kb_entries[0]["answer"]  # факт КБ передан в LLM

    # проверяем, что промпт реально содержит факт (сборка промпта)
    prompt = reply_llm._build_user_prompt(
        "Правила", None, {"name": "Т"}, {"text": "Какой размер подойдёт?"}, "question", kb_entries
    )
    assert "Размерная сетка: M на обхват 90." in prompt
    assert "БАЗА ЗНАНИЙ" in prompt

    rows = await reply_service.list_replies(db_session, project.id)
    assert rows["items"][0]["generation"] == "llm"
    assert rows["items"][0]["needs_info"] is False


async def test_run_agent_needs_info_from_llm(db_session, project, monkeypatch):
    """LLM вернула needs_info=true → черновик помечается needs_info."""
    await _add_unanswered_question(db_session, project.id, "q1", nm_id=111)
    db_session.add(
        WBProductKB(
            project_id=project.id, nm_id=111, topic="Состав",
            question_example="Какой состав?", answer="Хлопок.", source="manual",
        )
    )
    await db_session.commit()
    agent = await _add_agent(db_session, project.id, target="question")

    async def fake_draft(*args, **kwargs):
        return {"reply_text": "", "needs_info": True, "used_kb_ids": []}

    monkeypatch.setattr(reply_llm, "draft_reply", fake_draft)
    monkeypatch.setattr(reply_service.settings, "COMPLAINT_LLM_API_KEY", "test-key")

    res = await reply_service.run_reply_agent(db_session, project.id, agent.id)
    assert res["needs_info"] == 1
    rows = await reply_service.list_replies(db_session, project.id)
    assert rows["items"][0]["needs_info"] is True
    # needs_info-черновик нельзя одобрить к отправке с пустым текстом
    with pytest.raises(ValueError, match="Нечего отправлять"):
        await reply_service.update_draft(db_session, project.id, rows["items"][0]["id"], {"action": "approve"})


async def test_run_agent_kb_direct_fallback_without_llm_key(db_session, project, monkeypatch):
    """Без LLM-ключа: точное совпадение с КБ → draft_text = эталонный ответ (kb_direct)."""
    await _add_unanswered_question(db_session, project.id, "q1", nm_id=111, text="Какой размер выбрать?")
    db_session.add(
        WBProductKB(
            project_id=project.id, nm_id=111, topic="Размер",
            question_example="Какой размер выбрать?", answer="Берите M — в размер.",
            source="import",
        )
    )
    await db_session.commit()
    agent = await _add_agent(db_session, project.id, target="question")

    async def llm_must_not_run(*args, **kwargs):
        raise AssertionError("LLM не должен вызываться без ключа")

    monkeypatch.setattr(reply_llm, "draft_reply", llm_must_not_run)
    monkeypatch.setattr(reply_service.settings, "COMPLAINT_LLM_API_KEY", "")

    res = await reply_service.run_reply_agent(db_session, project.id, agent.id)
    assert res["drafted"] == 1
    assert res["needs_info"] == 0

    rows = await reply_service.list_replies(db_session, project.id)
    item = rows["items"][0]
    assert item["draft_text"] == "Берите M — в размер."  # эталонный ответ из КБ как есть
    assert item["generation"] == "kb_direct"
    assert item["needs_info"] is False
    assert item["error"] is None


async def test_run_agent_no_match_without_llm_key_needs_info(db_session, project, monkeypatch):
    """Без LLM-ключа и без точного совпадения → needs_info, draft_text пустой."""
    await _add_unanswered_question(db_session, project.id, "q1", nm_id=111, text="А подходит ли для стирки в машинке при 60 градусах?")
    db_session.add(
        WBProductKB(
            project_id=project.id, nm_id=111, topic="Состав",
            question_example="Какой состав ткани?", answer="Хлопок 100%.",
            source="import",
        )
    )
    await db_session.commit()
    agent = await _add_agent(db_session, project.id, target="question")
    monkeypatch.setattr(reply_service.settings, "COMPLAINT_LLM_API_KEY", "")

    res = await reply_service.run_reply_agent(db_session, project.id, agent.id)
    assert res["drafted"] == 1
    assert res["needs_info"] == 1
    rows = await reply_service.list_replies(db_session, project.id)
    assert rows["items"][0]["draft_text"] == ""
    assert rows["items"][0]["generation"] is None


# ─── CRUD базы знаний ────────────────────────────────────────────────────────


async def test_kb_crud_flow(db_session, project):
    with pytest.raises(ValueError, match="nm_id"):
        await reply_service.create_kb(db_session, project.id, {"topic": "Размер", "answer": "M"})
    with pytest.raises(ValueError, match="тема"):
        await reply_service.create_kb(db_session, project.id, {"nm_id": 111, "answer": "M"})
    with pytest.raises(ValueError, match="ответ"):
        await reply_service.create_kb(db_session, project.id, {"nm_id": 111, "topic": "Размер", "answer": " "})

    item = await reply_service.create_kb(
        db_session, project.id,
        {"nm_id": 111, "topic": "Размер", "question_example": "Какой размер?", "answer": "M"},
    )
    assert item["source"] == "manual"
    assert item["enabled"] is True

    upd = await reply_service.update_kb(db_session, project.id, item["id"], {"answer": "L", "enabled": False})
    assert upd["answer"] == "L"
    assert upd["enabled"] is False

    # отключённая запись не попадает в подбор для агента
    kb_map = await reply_service.load_kb_map(db_session, project.id, [111])
    assert kb_map.get(111) in (None, [])

    ok = await reply_service.delete_kb(db_session, project.id, item["id"])
    assert ok is True
    assert await reply_service.delete_kb(db_session, project.id, item["id"]) is False


async def test_list_kb_products_names_from_mirror(db_session, project):
    await _add_answered_question(db_session, project.id, "q1", nm_id=111)
    await _add_answered_question(db_session, project.id, "q2", nm_id=111, text="Другой вопрос", answer="Другой ответ")
    await db_session.commit()
    await reply_service.import_kb_from_answered_questions(db_session, project.id)

    res = await reply_service.list_kb_products(db_session, project.id)
    assert res["total"] == 1
    item = res["items"][0]
    assert item["nm_id"] == 111
    assert item["kb_count"] == 2
    assert item["product_name"] == "Товар"  # имя из зеркала вопросов
    assert item["article"] == "ART1"


# ─── сериализация схем ───────────────────────────────────────────────────────


def test_kb_schemas_serialization():
    from backend.schemas.reviews import (
        KbImportResult,
        KbItem,
        KbListResponse,
        KbProductItem,
        KbProductsResponse,
    )

    p = KbProductItem(nm_id=1, kb_count=3, product_name="Товар")
    assert p.model_dump()["kb_count"] == 3

    item = KbItem(id=1, nm_id=1, topic="Размер", answer="M")
    assert item.model_dump()["enabled"] is True

    resp = KbListResponse(items=[item], total=1)
    assert resp.model_dump()["total"] == 1

    prods = KbProductsResponse(items=[p], total=1)
    assert prods.model_dump()["items"][0]["nm_id"] == 1

    imp = KbImportResult(source_questions=10, created=8, skipped_dupe=2, nm_count=5)
    assert imp.model_dump()["nm_count"] == 5


def test_reply_item_needs_info_fields():
    from backend.schemas.reviews import ReplyItem

    item = ReplyItem(
        id=1, target_type="question", target_wb_id="q1", draft_text="", text="",
        status="draft", source="agent", needs_info=True, generation=None,
    )
    d = item.model_dump()
    assert d["needs_info"] is True
    assert d["generation"] is None
