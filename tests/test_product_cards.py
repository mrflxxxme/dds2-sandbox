# ruff: noqa: RUF002, RUF003 — русские строки в тест-данных
"""Тесты зеркала карточек WB (wb_product_cards) и импорта КБ из карточек."""

import pytest
from sqlalchemy import func, select

from backend.models import WBProductCard, WBProductKB
from backend.services import wb_cards_service
from backend.utils.time import utcnow

# Фикстура — урезанный РЕАЛЬНЫЙ ответ card.json (nm 355693494, накидка на диван)
CARD_JSON = {
    "imt_id": 482927044,
    "nm_id": 355693494,
    "imt_name": "Накидка на диван дивандек антискользящий 2 шт",
    "subj_name": "Чехлы для мебели",
    "vendor_code": "ДД-2шт-беж",
    "description": "Накидка на диван дивандек. Мягкая, антискользящая. Подходит для кожаной мебели.",
    "contents": "накидка - 210x90 - 1шт; накидка - 160x90 - 1шт; Упаковка - 1шт",
    "options": [
        {"name": "Длина упаковки", "value": "37 см"},
        {"name": "Вес с упаковкой (кг)", "value": "2.3 кг", "charc_type": 4},
        {"name": "Цвет", "value": "бежевый; коричневый", "charc_type": 1,
         "is_variable": True, "variable_values": ["бежевый", "коричневый"]},
        {"name": "Особенности материала", "value": "антискользящие; мягкие"},
        {"name": "Количество предметов в упаковке", "value": "2", "is_variable": True},
        {"name": "Страна производства", "value": "Китай"},
        {"name": "", "value": "пустое имя — выбрасываем"},
        {"name": "Пустое значение", "value": ""},
    ],
    "media": {"has_video": True, "photo_count": 10},
}

DETAIL_JSON = {"brand": "Уютопия", "pics": 10, "colors": [{"name": "бежевый"}, {"name": "коричневый"}]}


def _fetcher_ok(card=None, detail=None):
    async def fetch(nm: int) -> dict:
        return {"status": 200, "card": card if card is not None else CARD_JSON, "detail": detail}
    return fetch


# ─── basket-таблица и URL ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("nm_id", "basket"),
    [
        (355693494, 21),  # проверено живьём: vol 3556 → basket-21
        (1_000_000, 1),  # vol 10 ≤ 143
        (14_300_000, 1),  # vol 143 — граница
        (14_400_000, 2),  # vol 144 — следующий basket
        (581_300_000, 29),  # vol 5813 — последний из таблицы
        (590_000_000, 30),  # vol 5900 — экстраполяция +312
        (999_999_999, 43),  # далёкая экстраполяция
    ],
)
def test_basket_number(nm_id, basket):
    assert wb_cards_service.basket_number(nm_id) == basket
    assert wb_cards_service.basket_host(nm_id) == f"basket-{basket:02d}.wbbasket.ru"


def test_card_paths_and_photo_urls():
    nm = 355693494
    assert wb_cards_service.card_json_path(nm) == "/vol3556/part355693/355693494/info/ru/card.json"
    urls = wb_cards_service.photo_urls(nm, 3)
    assert urls == [
        f"https://basket-21.wbbasket.ru/vol3556/part355693/{nm}/images/big/{i}.webp"
        for i in (1, 2, 3)
    ]
    # кап на число фото
    assert len(wb_cards_service.photo_urls(nm, 50)) == 10


# ─── парсинг card.json ────────────────────────────────────────────────────────


def test_normalize_options_from_real_card():
    chars = wb_cards_service.normalize_options(CARD_JSON["options"])
    names = [c["name"] for c in chars]
    # пустые name/value выброшены
    assert "" not in names and "Пустое значение" not in names
    assert names[:3] == ["Длина упаковки", "Вес с упаковкой (кг)", "Цвет"]
    color = next(c for c in chars if c["name"] == "Цвет")
    assert color["value"] == "бежевый; коричневый"


def test_build_card_row_from_real_card():
    row = wb_cards_service.build_card_row(1, 355693494, CARD_JSON, DETAIL_JSON, utcnow())
    assert row["title"] == "Накидка на диван дивандек антискользящий 2 шт"
    assert row["brand"] == "Уютопия"  # бренд — из detail (в card.json его нет)
    assert row["subject"] == "Чехлы для мебели"
    assert row["description"].startswith("Накидка на диван")
    assert row["contents"].startswith("накидка - 210x90")
    assert len(row["characteristics"]) == 6
    assert len(row["photo_urls"]) == 10  # media.photo_count
    assert row["photo_urls"][0].endswith("/images/big/1.webp")


def test_build_card_row_photo_count_fallback_to_detail():
    card = {**CARD_JSON, "media": {}}
    row = wb_cards_service.build_card_row(1, 355693494, card, DETAIL_JSON, utcnow())
    assert len(row["photo_urls"]) == 10  # pics из detail как запасной счётчик


def test_fetch_nm_card_basket_fallback_scan(monkeypatch):
    """Экстраполяционная зона: табличный basket 404 → скан соседних находит реальный."""
    nm = 863823054  # vol 8638: таблица даёт basket-39, реальный — basket-38
    assert wb_cards_service.basket_number(nm) == 39

    def fake_http(host, path, proxy=None, timeout=30):
        if host == "basket-38.wbbasket.ru":
            return 200, CARD_JSON
        if host == "card.wb.ru":
            return 200, {"products": [DETAIL_JSON]}
        return 404, {}

    monkeypatch.setattr(wb_cards_service, "_http_get_json", fake_http)
    res = wb_cards_service.fetch_nm_card(nm, proxy=("x", 1))
    assert res["status"] == 200
    assert res["basket"] == 38  # найден сканом, не таблицей

    # URL фото строятся по РЕАЛЬНОМУ basket
    row = wb_cards_service.build_card_row(1, nm, res["card"], res["detail"], utcnow(), res["basket"])
    assert row["photo_urls"][0].startswith("https://basket-38.wbbasket.ru/")


def test_fetch_nm_card_basket_fallback_all_404(monkeypatch):
    """Все basket 404 (товар удалён) → status 404, карточки нет."""
    nm = 863823054

    def fake_http(host, path, proxy=None, timeout=30):
        return 404, {}

    monkeypatch.setattr(wb_cards_service, "_http_get_json", fake_http)
    res = wb_cards_service.fetch_nm_card(nm, proxy=("x", 1))
    assert res["status"] == 404
    assert res["card"] is None
    assert res["basket"] is None


# ─── маппинг тем характеристик ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "topic"),
    [
        ("Цвет", "Цвет"),
        ("Оттенок", "Цвет"),
        ("Состав", "Состав"),
        ("Особенности материала", "Состав"),
        ("Материал подкладки", "Состав"),
        ("Количество предметов в упаковке", "Комплект"),
        ("Комплектация", "Комплект"),
        ("Гарантия", "Гарантия"),
        ("Срок гарантии", "Гарантия"),
        ("Размер", "Размер"),
        ("Длина упаковки", "Размер"),
        ("Вес с упаковкой (кг)", "Размер"),
        ("Ширина", "Размер"),
        ("Габариты", "Размер"),
        ("Страна производства", "Прочее"),
        ("Бренд", "Прочее"),
        ("", "Прочее"),
        (None, "Прочее"),
    ],
)
def test_map_characteristic_topic(name, topic):
    assert wb_cards_service.map_characteristic_topic(name) == topic


# ─── синк карточек (upsert, пропуск 404/ошибок) ──────────────────────────────


async def test_sync_cards_upsert_and_skip_404(db_session, project):
    calls: dict[int, int] = {}

    async def fetcher(nm: int) -> dict:
        calls[nm] = calls.get(nm, 0) + 1
        if nm == 222:
            return {"status": 404, "card": None, "detail": None}
        if nm == 333:
            raise RuntimeError("сеть упала")
        return {"status": 200, "card": CARD_JSON, "detail": DETAIL_JSON}

    res = await wb_cards_service.sync_project_cards(
        db_session, project.id, [111, 222, 333], fetcher=fetcher, throttle_sec=0
    )
    assert res == {"cards_total": 3, "synced": 1, "not_found": 1, "errors": 1}

    card = await wb_cards_service.get_card(db_session, project.id, 111)
    assert card is not None
    assert card["title"] == CARD_JSON["imt_name"]
    assert card["brand"] == "Уютопия"
    assert len(card["photo_urls"]) == 10
    # 404 и ошибка не создали строк
    assert await wb_cards_service.get_card(db_session, project.id, 222) is None
    assert await wb_cards_service.get_card(db_session, project.id, 333) is None

    # повторный прогон — upsert, а не дубли
    res2 = await wb_cards_service.sync_project_cards(
        db_session, project.id, [111], fetcher=fetcher, throttle_sec=0
    )
    assert res2["synced"] == 1
    total = await db_session.scalar(
        select(func.count(WBProductCard.id)).where(
            WBProductCard.project_id == project.id, WBProductCard.nm_id == 111
        )
    )
    assert total == 1


async def test_sync_cards_collects_nm_from_kb_and_mirrors(db_session, project):
    from backend.models import WBQuestion

    db_session.add(WBProductKB(
        project_id=project.id, nm_id=111, topic="Размер", answer="M", source="manual",
    ))
    db_session.add(WBQuestion(
        project_id=project.id, wb_id="q1", nm_id=222, text="?", is_answered=False,
    ))
    await db_session.commit()

    nm_ids = await wb_cards_service.collect_project_nm_ids(db_session, project.id)
    assert nm_ids == [111, 222]

    res = await wb_cards_service.sync_project_cards(
        db_session, project.id, None, fetcher=_fetcher_ok(), throttle_sec=0
    )
    assert res["cards_total"] == 2
    assert res["synced"] == 2


# ─── импорт КБ из карточек ───────────────────────────────────────────────────


async def _seed_card(db, project_id: int, nm_id: int = 111, card=None, detail=None) -> None:
    row = wb_cards_service.build_card_row(
        project_id, nm_id, card if card is not None else CARD_JSON,
        detail if detail is not None else DETAIL_JSON, utcnow(),
    )
    db.add(WBProductCard(**row))
    await db.commit()


async def test_import_kb_from_cards_creates_entries(db_session, project):
    await _seed_card(db_session, project.id)

    res = await wb_cards_service.import_kb_from_cards(db_session, project.id)
    # описание + комплектация + 6 характеристик
    assert res["cards_total"] == 1
    assert res["created"] == 8
    assert res["updated"] == 0

    rows = (
        await db_session.execute(
            select(WBProductKB).where(
                WBProductKB.project_id == project.id, WBProductKB.source == "card"
            )
        )
    ).scalars().all()
    by_answer = {r.answer: r for r in rows}
    desc = next(r for r in rows if r.topic == "Описание")
    assert desc.answer == CARD_JSON["description"]
    assert desc.question_example is None
    assert desc.question_hash == wb_cards_service._card_hash(111, "__description__")
    color = by_answer["Цвет: бежевый; коричневый"]
    assert color.topic == "Цвет"
    kit = next(r for r in rows if r.topic == "Комплект" and r.answer.startswith("Комплектация:"))
    assert "210x90" in kit.answer
    assert by_answer["Количество предметов в упаковке: 2"].topic == "Комплект"
    assert by_answer["Страна производства: Китай"].topic == "Прочее"


async def test_import_kb_from_cards_idempotent_rerun(db_session, project):
    await _seed_card(db_session, project.id)

    first = await wb_cards_service.import_kb_from_cards(db_session, project.id)
    assert first["created"] == 8

    second = await wb_cards_service.import_kb_from_cards(db_session, project.id)
    assert second["created"] == 0
    assert second["updated"] == 0
    assert second["unchanged"] == 8  # повторный импорт ничего не плодит

    total = await db_session.scalar(
        select(func.count(WBProductKB.id)).where(
            WBProductKB.project_id == project.id, WBProductKB.source == "card"
        )
    )
    assert total == 8


async def test_import_kb_from_cards_updates_changed_characteristic(db_session, project):
    await _seed_card(db_session, project.id)
    await wb_cards_service.import_kb_from_cards(db_session, project.id)

    # ресинк карточки: цвет изменился
    changed = {**CARD_JSON, "options": [
        {**o, "value": "серый"} if o.get("name") == "Цвет" else o
        for o in CARD_JSON["options"]
    ]}
    row = wb_cards_service.build_card_row(project.id, 111, changed, DETAIL_JSON, utcnow())
    await wb_cards_service.upsert_card_rows(db_session, [row])
    await db_session.commit()

    res = await wb_cards_service.import_kb_from_cards(db_session, project.id)
    assert res["created"] == 0
    assert res["updated"] == 1  # обновилось значение цвета
    assert res["unchanged"] == 7

    color = (
        await db_session.execute(
            select(WBProductKB).where(
                WBProductKB.project_id == project.id,
                WBProductKB.source == "card",
                WBProductKB.topic == "Цвет",
            )
        )
    ).scalar_one()
    assert color.answer == "Цвет: серый"
    # и всё ещё ровно 8 записей — дублей нет
    total = await db_session.scalar(
        select(func.count(WBProductKB.id)).where(
            WBProductKB.project_id == project.id, WBProductKB.source == "card"
        )
    )
    assert total == 8


async def test_import_kb_from_cards_preserves_manual_and_import(db_session, project):
    db_session.add(WBProductKB(
        project_id=project.id, nm_id=111, topic="Размер",
        answer="Ручная запись", source="manual",
    ))
    db_session.add(WBProductKB(
        project_id=project.id, nm_id=111, topic="Доставка",
        question_example="Когда доставка?", answer="3 дня", source="import",
        question_hash="a" * 32,
    ))
    await _seed_card(db_session, project.id)

    res = await wb_cards_service.import_kb_from_cards(db_session, project.id)
    assert res["created"] == 8  # только source='card'

    manual = (
        await db_session.execute(
            select(WBProductKB).where(
                WBProductKB.project_id == project.id, WBProductKB.source.in_(["manual", "import"])
            )
        )
    ).scalars().all()
    assert {r.answer for r in manual} == {"Ручная запись", "3 дня"}  # не тронуты


async def test_import_kb_dedupes_contents_vs_characteristic(db_session, project):
    """contents и характеристика «Комплектация» с тем же текстом → ОДНА запись КБ."""
    card = {**CARD_JSON, "options": [
        *CARD_JSON["options"],
        {"name": "Комплектация", "value": CARD_JSON["contents"]},
    ]}
    await _seed_card(db_session, project.id, card=card)

    res = await wb_cards_service.import_kb_from_cards(db_session, project.id)
    assert res["created"] == 8  # дубль «Комплектация» склеен с contents-записью

    rows = (
        await db_session.execute(
            select(WBProductKB).where(
                WBProductKB.project_id == project.id,
                WBProductKB.source == "card",
                WBProductKB.topic == "Комплект",
            )
        )
    ).scalars().all()
    assert len(rows) == 2  # contents + «Количество предметов в упаковке»


async def test_import_kb_long_description_truncated(db_session, project):
    long_desc = ("Длинное описание. " * 300).strip()  # > _DESC_KB_LIMIT
    card = {**CARD_JSON, "description": long_desc}
    await _seed_card(db_session, project.id, card=card)

    await wb_cards_service.import_kb_from_cards(db_session, project.id)
    desc = (
        await db_session.execute(
            select(WBProductKB).where(
                WBProductKB.project_id == project.id,
                WBProductKB.source == "card",
                WBProductKB.topic == "Описание",
            )
        )
    ).scalar_one()
    assert len(desc.answer) <= wb_cards_service._DESC_KB_LIMIT
    assert desc.answer.endswith(".")  # резка по границе предложения
    # в зеркале описание хранится целиком
    full = await wb_cards_service.get_card(db_session, project.id, 111)
    assert full["description"] == long_desc


# ─── сериализация схем ────────────────────────────────────────────────────────


def test_card_schemas_serialization():
    from backend.schemas.reviews import (
        CardItem,
        CardSyncResult,
        KbCardImportResult,
        KbProductItem,
    )

    item = CardItem(nm_id=1, title="Т", characteristics=[{"name": "Цвет", "value": "серый"}])
    assert item.model_dump()["photo_urls"] == []

    res = CardSyncResult(cards_total=3, synced=1, not_found=1, errors=1)
    assert res.model_dump()["not_found"] == 1

    imp = KbCardImportResult(cards_total=2, created=5, updated=1, unchanged=4)
    assert imp.model_dump()["updated"] == 1

    prod = KbProductItem(nm_id=1, kb_count=2, card_synced_at="2026-07-24T12:00:00")
    assert prod.model_dump()["card_synced_at"] == "2026-07-24T12:00:00"
