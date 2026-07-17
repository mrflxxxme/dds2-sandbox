# ruff: noqa: RUF002, RUF003 — русские строки в тест-данных
"""Тесты reviews_service: список и сводная аналитика из зеркала БД wb_feedbacks."""

from datetime import datetime, timedelta

from backend.models import Nomenclature, ProductTag, ProductTagMap, WBFeedback
from backend.services import reviews_service
from backend.utils.time import utcnow


async def _add_feedback(
    db,
    project_id: int,
    wb_id: str,
    rating: int,
    nm_id: int | None,
    *,
    text: str | None = None,
    pros: str | None = None,
    cons: str | None = None,
    is_answered: bool = False,
    created: datetime | None = None,
    brand: str | None = None,
):
    db.add(
        WBFeedback(
            project_id=project_id,
            wb_id=wb_id,
            nm_id=nm_id,
            rating=rating,
            text=text,
            pros=pros,
            cons=cons,
            has_text=bool(text or pros or cons),
            is_answered=is_answered,
            created_date=created,
            brand=brand,
        )
    )


async def _seed(db, project_id: int):
    """5 отзывов + номенклатура + активный/удалённый ярлык."""
    await _add_feedback(db, project_id, "f1", 5, 111, text="отлично", created=datetime(2026, 6, 15))
    await _add_feedback(db, project_id, "f2", 4, 111, is_answered=True, created=datetime(2026, 6, 20))
    await _add_feedback(db, project_id, "f3", 1, 222, text="плохо", created=datetime(2026, 7, 1))
    await _add_feedback(db, project_id, "f4", 2, 333, pros="цена", created=datetime(2026, 7, 5), brand="СнапБренд")
    await _add_feedback(db, project_id, "f5", 0, 222, text="никак", created=datetime(2026, 7, 5))

    db.add(Nomenclature(project_id=project_id, barcode="bc111", article_wb=111, subject="Носки", brand="БрендA"))
    db.add(Nomenclature(project_id=project_id, barcode="bc222", article_wb=222, subject="Кружки", brand="БрендB"))

    tag_hit = ProductTag(project_id=project_id, name="Хит")
    tag_old = ProductTag(project_id=project_id, name="Старый", is_deleted=True)
    db.add(tag_hit)
    db.add(tag_old)
    await db.flush()
    db.add(ProductTagMap(project_id=project_id, tag_id=tag_hit.id, nm_id=111))
    db.add(ProductTagMap(project_id=project_id, tag_id=tag_old.id, nm_id=222))
    await db.commit()


# ─── list_reviews ───────────────────────────────────────────────────────────


async def test_list_reviews_no_data_no_key(db_session, project):
    """Пустое зеркало и нет ключа → has_key=False, пустой список."""
    res = await reviews_service.list_reviews(db_session, project.id)
    assert res.has_key is False
    assert res.items == []
    assert res.average_rating is None


async def test_list_reviews_filters_and_aggregates(db_session, project):
    await _seed(db_session, project.id)

    unanswered = await reviews_service.list_reviews(db_session, project.id, is_answered=False)
    # f1,f3,f4,f5 без ответа
    assert {i.id for i in unanswered.items} == {"f1", "f3", "f4", "f5"}
    assert unanswered.count_unanswered == 4
    assert unanswered.average_rating == 3.0  # (5+4+1+2)/4, rating=0 исключён
    assert unanswered.has_key is True  # есть строки → показываем данные

    answered = await reviews_service.list_reviews(db_session, project.id, is_answered=True)
    assert {i.id for i in answered.items} == {"f2"}


async def test_list_reviews_by_nm_id(db_session, project):
    """nm_id → все отзывы товара (и отвеченные, и нет), текстовые сверху."""
    await _seed(db_session, project.id)
    # nm 111 → f1(текст, без ответа) и f2(без текста, отвечен)
    res = await reviews_service.list_reviews(db_session, project.id, nm_id=111)
    assert {i.id for i in res.items} == {"f1", "f2"}  # оба, несмотря на разный is_answered
    assert res.total == 2
    assert res.items[0].id == "f1"  # текстовый отзыв идёт первым


async def test_list_reviews_pagination_total(db_session, project):
    """total = размер среза (по фильтру); take/skip пагинируют без пересечений."""
    await _seed(db_session, project.id)

    page1 = await reviews_service.list_reviews(db_session, project.id, is_answered=False, take=2, skip=0)
    assert page1.total == 4  # всего без ответа в срезе
    assert len(page1.items) == 2  # взяли страницу

    page2 = await reviews_service.list_reviews(db_session, project.id, is_answered=False, take=2, skip=2)
    assert len(page2.items) == 2
    # две страницы покрывают весь срез без пересечений
    assert {i.id for i in page1.items}.isdisjoint({i.id for i in page2.items})
    assert {i.id for i in page1.items} | {i.id for i in page2.items} == {"f1", "f3", "f4", "f5"}


# ─── summary ────────────────────────────────────────────────────────────────


async def test_summary_kpis(db_session, project):
    await _seed(db_session, project.id)
    res = await reviews_service.get_reviews_summary(db_session, project.id)
    s = res["summary"]
    assert s["total"] == 5
    assert s["average_rating"] == 3.0
    assert s["count_no_text"] == 1  # f2 без текста
    assert s["count_with_text"] == 4
    assert s["count_unanswered"] == 4
    assert s["count_positive"] == 2  # f1(5), f2(4)
    assert s["count_negative"] == 2  # f3(1), f4(2)
    assert res["has_key"] is True


async def test_summary_monthly(db_session, project):
    await _seed(db_session, project.id)
    res = await reviews_service.get_reviews_summary(db_session, project.id)

    rating = {p["month"]: p for p in res["monthly_rating"]}
    assert rating["2026-06"]["count"] == 2 and rating["2026-06"]["avg_rating"] == 4.5
    assert rating["2026-07"]["count"] == 3 and rating["2026-07"]["avg_rating"] == 1.5

    vol = {p["month"]: p for p in res["monthly_volume"]}
    assert (vol["2026-06"]["r5"], vol["2026-06"]["r4"]) == (1, 1)
    assert (vol["2026-07"]["r1"], vol["2026-07"]["r2"]) == (1, 1)


async def test_summary_by_category_and_brand(db_session, project):
    await _seed(db_session, project.id)
    res = await reviews_service.get_reviews_summary(db_session, project.id)

    cats = {g["name"]: g for g in res["by_category"]}
    assert cats["Носки"]["count"] == 2 and cats["Носки"]["avg_rating"] == 4.5
    # распределение r1..r5: Носки = f1(5) + f2(4)
    assert cats["Носки"]["r5"] == 1 and cats["Носки"]["r4"] == 1
    assert cats["Кружки"]["count"] == 2 and cats["Кружки"]["avg_rating"] == 1.0
    assert cats["Кружки"]["r1"] == 1  # f3(1); f5(0) не попадает в r1..5
    assert cats["Без категории"]["count"] == 1  # nm 333 нет в справочнике

    brands = {g["name"]: g for g in res["by_brand"]}
    assert brands["БрендA"]["count"] == 2 and brands["БрендA"]["r5"] == 1
    assert brands["БрендB"]["count"] == 2
    assert brands["СнапБренд"]["count"] == 1  # фолбэк на снапшот отзыва


async def test_summary_tag_filter(db_session, project):
    """tag=имя ярлыка → вся сводка только по товарам ярлыка (как в воронке)."""
    await _seed(db_session, project.id)

    # Ярлык «Хит» навешан на nm 111 → отзывы f1(5), f2(4)
    res = await reviews_service.get_reviews_summary(db_session, project.id, tag="Хит")
    assert res["summary"]["total"] == 2
    assert res["summary"]["average_rating"] == 4.5
    cats = {g["name"]: g for g in res["by_category"]}
    assert set(cats) == {"Носки"}  # только категория товаров ярлыка

    # Soft-deleted ярлык «Старый» → не резолвится, пустая выборка
    empty = await reviews_service.get_reviews_summary(db_session, project.id, tag="Старый")
    assert empty["summary"]["total"] == 0

    # Несуществующий ярлык → пусто
    none = await reviews_service.get_reviews_summary(db_session, project.id, tag="Нет такого")
    assert none["summary"]["total"] == 0


async def test_summary_project_isolation(db_session, project, other_project):
    await _seed(db_session, project.id)
    await _add_feedback(db_session, other_project.id, "x1", 3, 999, text="чужой")
    await db_session.commit()

    res = await reviews_service.get_reviews_summary(db_session, project.id)
    assert res["summary"]["total"] == 5  # чужой отзыв не попал


async def test_summary_empty_project(db_session, project):
    """Нет отзывов и нет ключа → пустая сводка, has_key=False."""
    res = await reviews_service.get_reviews_summary(db_session, project.id)
    assert res["summary"]["total"] == 0
    assert res["summary"]["average_rating"] is None
    assert res["has_key"] is False
    assert res["monthly_rating"] == []


# ─── period (диапазон выборки) ───────────────────────────────────────────────


async def test_summary_period_window(db_session, project):
    """period ограничивает окно по дате; 'all' включает старые отзывы."""
    now = utcnow().replace(tzinfo=None)
    await _add_feedback(db_session, project.id, "recent", 5, 111, text="свежий", created=now - timedelta(days=3))
    await _add_feedback(db_session, project.id, "ancient", 1, 111, text="древний", created=now - timedelta(days=730))
    await db_session.commit()

    short = await reviews_service.get_reviews_summary(db_session, project.id, period="2w")
    assert short["summary"]["total"] == 1  # только свежий в окне 2 недели
    assert short["granularity"] == "day"  # короткий период → посуточно
    assert short["period"] == "2w"

    allp = await reviews_service.get_reviews_summary(db_session, project.id, period="all")
    assert allp["summary"]["total"] == 2  # всё время → оба
    assert allp["granularity"] == "month"


async def test_summary_period_default_year(db_session, project):
    """Дефолт — год: отзыв старше года отсекается, свежий остаётся."""
    now = utcnow().replace(tzinfo=None)
    await _add_feedback(db_session, project.id, "in", 5, 111, created=now - timedelta(days=100))
    await _add_feedback(db_session, project.id, "out", 5, 111, created=now - timedelta(days=400))
    await db_session.commit()

    res = await reviews_service.get_reviews_summary(db_session, project.id)  # period=1y по умолчанию
    assert res["period"] == "1y"
    assert res["summary"]["total"] == 1  # только «in» (100 дней назад)


async def test_summary_bad_period_falls_back_to_year(db_session, project):
    """Неизвестный period → нормализуется в дефолтный «год»."""
    await _seed(db_session, project.id)
    res = await reviews_service.get_reviews_summary(db_session, project.id, period="bogus")
    assert res["period"] == "1y"


async def test_new_low_rated(db_session, project):
    """Проблемные новинки: новинка+плохой рейтинг → в списке; хороший/старый → нет."""
    now = utcnow().replace(tzinfo=None)
    # 501 — новинка (первый отзыв 5 дн назад) + плохой рейтинг (avg 3.0)
    await _add_feedback(db_session, project.id, "n1", 3, 501, created=now - timedelta(days=5))
    await _add_feedback(db_session, project.id, "n2", 3, 501, created=now - timedelta(days=2))
    # 502 — новинка, но рейтинг хороший (5.0) → исключается
    await _add_feedback(db_session, project.id, "g1", 5, 502, created=now - timedelta(days=5))
    # 503 — плохой рейтинг, но старый (первый отзыв 100 дн назад) → не новинка
    await _add_feedback(db_session, project.id, "o1", 2, 503, created=now - timedelta(days=100))
    await db_session.commit()

    res = await reviews_service.get_new_low_rated(db_session, project.id, days=30, max_rating=4.6)
    nms = {i["nm_id"] for i in res["items"]}
    assert 501 in nms  # новинка + плохой
    assert 502 not in nms  # новинка, но хороший рейтинг
    assert 503 not in nms  # плохой, но старый

    item = next(i for i in res["items"] if i["nm_id"] == 501)
    assert item["avg_rating"] == 3.0
    assert item["count"] == 2
    assert item["days_on_sale"] <= 30


async def test_new_low_rated_first_sale_date_precedence(db_session, project):
    """first_sale_date из номенклатуры важнее даты первого отзыва при определении новинки."""
    now = utcnow().replace(tzinfo=None)
    # старые отзывы (200 дн), но first_sale_date 10 дн назад → всё равно новинка
    await _add_feedback(db_session, project.id, "s1", 2, 504, created=now - timedelta(days=200))
    db_session.add(
        Nomenclature(
            project_id=project.id, barcode="bc504", article_wb=504,
            first_sale_date=(now - timedelta(days=10)).date(),
        )
    )
    await db_session.commit()

    res = await reviews_service.get_new_low_rated(db_session, project.id, days=30, max_rating=4.6)
    item = next((i for i in res["items"] if i["nm_id"] == 504), None)
    assert item is not None  # новинка по first_sale_date, несмотря на старый отзыв
    assert item["days_on_sale"] == 10
    assert item["date_source"] == "sale"  # дата взята из номенклатуры


async def test_new_low_rated_date_source_review_fallback(db_session, project):
    """Нет first_sale_date → дата и пометка источника берутся по первому отзыву."""
    now = utcnow().replace(tzinfo=None)
    await _add_feedback(db_session, project.id, "rv1", 2, 505, created=now - timedelta(days=5))
    # номенклатуры для 505 нет → first_sale_date отсутствует
    await db_session.commit()

    res = await reviews_service.get_new_low_rated(db_session, project.id, days=30, max_rating=4.6)
    item = next((i for i in res["items"] if i["nm_id"] == 505), None)
    assert item is not None
    assert item["date_source"] == "review"  # фолбэк на дату первого отзыва
    assert item["days_on_sale"] == 5


async def test_new_low_rated_kpi_and_complaints(db_session, project):
    """total_newcomers (все новинки), neg_unanswered (негатив без ответа), темы жалоб."""
    now = utcnow().replace(tzinfo=None)
    # проблемная новинка (nm 701): 2 негатива без ответа + повторяющиеся слова жалоб
    await _add_feedback(db_session, project.id, "p1", 1, 701, text="ужасное тонкий материал", created=now - timedelta(days=3))
    await _add_feedback(db_session, project.id, "p2", 2, 701, cons="тонкий материал брак", created=now - timedelta(days=2))
    # хорошая новинка (nm 702): попадает в total_newcomers, но не в проблемные
    await _add_feedback(db_session, project.id, "g1", 5, 702, created=now - timedelta(days=4))
    await db_session.commit()

    res = await reviews_service.get_new_low_rated(db_session, project.id, days=30, max_rating=4.6)
    assert res["total_newcomers"] == 2  # 701 (плохая) + 702 (хорошая)
    ids = {it["nm_id"]: it for it in res["items"]}
    assert 701 in ids and 702 not in ids  # только проблемная в списке
    assert ids[701]["neg_unanswered"] == 2  # оба 1–2★ без ответа

    terms = {t["term"]: t["count"] for t in res["complaint_terms"]}
    assert terms.get("тонкий") == 2 and terms.get("материал") == 2  # частые слова жалоб
    assert "ужасное" not in terms  # одиночные (count<2) отсекаются


async def test_complaint_reviews_by_term(db_session, project):
    """Клик по теме жалоб → негативные отзывы проблемных новинок, содержащие слово."""
    now = utcnow().replace(tzinfo=None)
    await _add_feedback(db_session, project.id, "c1", 2, 801, text="цвет не совпал с фото", created=now - timedelta(days=3))
    await _add_feedback(db_session, project.id, "c2", 1, 801, cons="ужасный цвет", created=now - timedelta(days=2))
    await _add_feedback(db_session, project.id, "c3", 2, 801, text="качество плохое", created=now - timedelta(days=2))
    await db_session.commit()

    res = await reviews_service.get_complaint_reviews(db_session, project.id, "цвет", days=30, max_rating=4.6)
    assert res.total == 2  # только c1 (text) и c2 (cons) содержат «цвет»
    assert all("цвет" in f"{r.text or ''} {r.cons or ''}".lower() for r in res.items)

    # пустой term → пусто, без ошибки
    empty = await reviews_service.get_complaint_reviews(db_session, project.id, "   ", days=30, max_rating=4.6)
    assert empty.total == 0


async def test_new_low_rated_breakdowns(db_session, project):
    """Разрезы новинок по категории/бренду/ярлыку: агрегат по проблемным новинкам."""
    now = utcnow().replace(tzinfo=None)
    # две новинки категории «Носки» (nm 601, 602), одна «Кружки» (nm 603) — все плохие
    await _add_feedback(db_session, project.id, "b1", 2, 601, created=now - timedelta(days=3))
    await _add_feedback(db_session, project.id, "b2", 3, 602, created=now - timedelta(days=3))
    await _add_feedback(db_session, project.id, "b3", 1, 603, created=now - timedelta(days=3))
    db_session.add(Nomenclature(project_id=project.id, barcode="bc601", article_wb=601, subject="Носки", brand="БрендA"))
    db_session.add(Nomenclature(project_id=project.id, barcode="bc602", article_wb=602, subject="Носки", brand="БрендB"))
    db_session.add(Nomenclature(project_id=project.id, barcode="bc603", article_wb=603, subject="Кружки", brand="БрендA"))
    tag = ProductTag(project_id=project.id, name="Хит")
    db_session.add(tag)
    await db_session.flush()
    db_session.add(ProductTagMap(project_id=project.id, tag_id=tag.id, nm_id=601))
    await db_session.commit()

    res = await reviews_service.get_new_low_rated(db_session, project.id, days=30, max_rating=4.6)

    cats = {g["name"]: g for g in res["by_category"]}
    assert cats["Носки"]["products"] == 2  # 601 + 602
    assert cats["Кружки"]["products"] == 1

    brands = {g["name"]: g for g in res["by_brand"]}
    assert brands["БрендA"]["products"] == 2  # 601 + 603
    assert brands["БрендB"]["products"] == 1

    tags = {g["name"]: g for g in res["by_tag"]}
    assert tags["Хит"]["products"] == 1  # только 601 с ярлыком
    assert tags["Без ярлыка"]["products"] == 2  # 602 + 603 без ярлыка

    # каждый item несёт свои ярлыки — для фильтра списка по ярлыку на фронте
    by_nm = {it["nm_id"]: it for it in res["items"]}
    assert by_nm[601]["tags"] == ["Хит"]
    assert by_nm[602]["tags"] == []
    assert by_nm[603]["tags"] == []


async def test_summary_old_reviews_keep_data_ui(db_session, project):
    """Все отзывы старше окна и ключа нет → окно пустое, но has_key=True (data-UI,
    не экран «настройте ключ») — фронт покажет «за период пусто»."""
    now = utcnow().replace(tzinfo=None)
    await _add_feedback(db_session, project.id, "old", 5, 111, created=now - timedelta(days=500))
    await db_session.commit()

    res = await reviews_service.get_reviews_summary(db_session, project.id, period="1y")
    assert res["summary"]["total"] == 0  # вне окна
    assert res["has_key"] is True  # но отзывы есть → показываем data-UI, а не «нет ключа»
