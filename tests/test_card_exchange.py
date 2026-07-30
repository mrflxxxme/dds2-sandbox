# ruff: noqa: RUF002, RUF003
"""Tests: раздел «Биржа карточек товаров» — справочник категорий, маппинг, витрина, корзина.

Клиент WB мокается FakeClient (get_wb_portal_client подменяется), БД к WB не ходит.
"""

import pytest

from backend.integrations.wb_portal_client import WbPortalError, WbSessionExpired
from backend.schemas.card_exchange import ShowcaseQuery
from backend.services import integrations_service
from backend.services.card_exchange import categories as cat_ref
from backend.services.card_exchange import showcase as svc


# ─── FakeClient ─────────────────────────────────────────────────────────────


class FakeClient:
    """Заглушка WbPortalClient: подставные предметы/объявления, лог фильтров/курсоров."""

    def __init__(self, *, subjects=None, pages=None, expired=False, portal_error=False):
        # subjects: [{"id","name"}]; pages: список страниц (каждая — список ad-dict WB-формы)
        self._subjects = subjects if subjects is not None else [
            {"id": 100, "name": "Компрессоры автомобильные", "category": ""},
            {"id": 101, "name": "Насосы автомобильные", "category": ""},
            {"id": 200, "name": "Брюки", "category": ""},
        ]
        self._pages = pages if pages is not None else [[]]
        self._call = 0
        self.expired = expired
        self.portal_error = portal_error
        self.last_filter = None
        self.last_sort = None
        self.last_cursor = None
        self.last_search = None
        self.cart_added = []
        self.cart_deleted = []

    async def showcase_subjects(self):
        return self._subjects

    async def showcase_ads(self, *, search=None, filter=None, sort=None, cursor=None):
        if self.expired:
            raise WbSessionExpired("протух")
        if self.portal_error:
            raise WbPortalError("WB 400")
        self.last_filter, self.last_sort, self.last_cursor, self.last_search = filter, sort, cursor, search
        page = self._pages[self._call] if self._call < len(self._pages) else []
        self._call += 1
        return {"ads": page}

    async def exc_cart_get(self):
        return {"suppliers": [], "flags": {"hasChangedPrice": False}}

    async def exc_cart_add(self, ad_id):
        self.cart_added.append(ad_id)
        return True

    async def exc_cart_delete(self, ad_ids):
        self.cart_deleted.extend(ad_ids)
        return True

    async def aclose(self):
        pass


def _ad(ad_id, nm_id, *, title="t", price=1000, rating=4.5, count=10, subject_ok=True):
    """WB-объявление в исходной camelCase-форме."""
    return {
        "adID": ad_id,
        "nmID": nm_id,
        "imtID": nm_id + 1,
        "meta": {"title": title, "brand": "B", "supplierName": "ИП", "imtCount": 3,
                 "stockQty": 50, "photo": "http://x/1.webp", "contactCountries": ["Китай"], "isKiz": False},
        "totalPrice": price,
        "feedbacks": {"rating": rating, "count": count},
        "hasInCart": False,
        "isCardOwner": False,
    }


@pytest.fixture
def patch_client(monkeypatch):
    def _apply(client):
        async def fake_get(db, project_id):
            return client
        monkeypatch.setattr(integrations_service, "get_wb_portal_client", fake_get)
        return client
    return _apply


async def _add_nomenclature(db, project_id, *, barcode, nm_id, subject):
    # Сырой INSERT только по нужным колонкам (ORM-INSERT тащит все поля модели, а в
    # локальной тест-БД схема nomenclature может отставать — напр. нет chrt_id).
    from sqlalchemy import text

    await db.execute(
        text(
            "INSERT INTO nomenclature (project_id, barcode, article_wb, subject, updated_at) "
            "VALUES (:pid, :bc, :nm, :subj, now())"
        ),
        {"pid": project_id, "bc": barcode, "nm": nm_id, "subj": subject},
    )
    await db.commit()


# ─── справочник категорий ────────────────────────────────────────────────────


def test_categories_loaded():
    cats = cat_ref.list_root_categories()
    assert len(cats) == 96
    assert sum(c["subjectCount"] for c in cats) == 7424
    names = {c["category"] for c in cats}
    assert "Автоаксессуары и дополнительное оборудование" in names


def test_subjects_for_category():
    subs = cat_ref.subjects_for_category("Автоаксессуары и дополнительное оборудование")
    assert "Компрессоры автомобильные" in subs
    assert cat_ref.subjects_for_category("НЕТ ТАКОЙ") == []


def test_resolve_subject_ids_matched_and_unmatched():
    name_to_id = {"Компрессоры автомобильные": 100, "Насосы автомобильные": 101}
    ids, unmatched = cat_ref.resolve_subject_ids(
        ["Автоаксессуары и дополнительное оборудование"], name_to_id
    )
    assert 100 in ids and 101 in ids
    assert ids == sorted(set(ids))  # уникальны и отсортированы
    assert "Компрессоры автомобильные" not in unmatched
    assert len(unmatched) > 0  # в категории предметов больше, чем в карте


# ─── маппинг ─────────────────────────────────────────────────────────────────


def test_map_ad_flattens_and_marks_ours():
    mapped = svc._map_ad(_ad(1, 555), {555})
    assert mapped["ad_id"] == 1
    assert mapped["nm_id"] == 555
    assert mapped["imt_id"] == 556
    assert mapped["supplier_name"] == "ИП"
    assert mapped["stock_qty"] == 50
    assert mapped["total_price"] == 1000
    assert mapped["rating"] == 4.5 and mapped["feedbacks_count"] == 10
    assert mapped["contact_countries"] == ["Китай"]
    assert mapped["is_ours"] is True
    assert svc._map_ad(_ad(2, 999), {555})["is_ours"] is False


# ─── витрина ─────────────────────────────────────────────────────────────────


async def test_showcase_plain_page(db_session, project, patch_client):
    patch_client(FakeClient(pages=[[_ad(10, 1), _ad(9, 2)]]))
    q = ShowcaseQuery()
    res = await svc.list_showcase(db_session, project.id, q)
    assert len(res["ads"]) == 2
    assert res["has_more"] is True
    assert res["next_cursor"]["last_ad_id"] == 9  # последняя карточка
    assert res["next_cursor"]["last_value"] == 10  # feedbacksCount по умолчанию


async def test_showcase_category_filter_resolves_subject_ids(db_session, project, patch_client):
    client = patch_client(FakeClient(pages=[[_ad(10, 1)]]))
    q = ShowcaseQuery(root_categories=["Автоаксессуары и дополнительное оборудование"])
    await svc.list_showcase(db_session, project.id, q)
    # в WB ушли subjectIDs, покрывающие сматченные предметы категории
    assert client.last_filter["subjectIDs"] == [100, 101]


async def test_showcase_category_all_unmatched_returns_empty(db_session, project, patch_client):
    # справочник WB без нужных предметов → subjectIDs пуст → пустая витрина, без вызова WB
    client = patch_client(FakeClient(subjects=[{"id": 1, "name": "Брюки", "category": ""}]))
    q = ShowcaseQuery(root_categories=["Автоаксессуары и дополнительное оборудование"])
    res = await svc.list_showcase(db_session, project.id, q)
    assert res["ads"] == []
    assert res["unmatched_subjects"]  # диагностика рассинхрона
    assert client.last_filter is None  # showcase_ads не звали


async def test_showcase_our_categories_mode(db_session, project, patch_client):
    await _add_nomenclature(db_session, project.id, barcode="bc1", nm_id=1, subject="Насосы автомобильные")
    client = patch_client(FakeClient(pages=[[_ad(10, 1)]]))
    q = ShowcaseQuery(our_mode="categories")
    await svc.list_showcase(db_session, project.id, q)
    assert client.last_filter["subjectIDs"] == [101]  # предмет нашего товара → его subjectID


async def test_showcase_exact_mode_scans_and_matches_nm(db_session, project, patch_client):
    await _add_nomenclature(db_session, project.id, barcode="bc2", nm_id=777, subject="X")
    # две страницы, наш nmID 777 на второй; затем пустая — конец
    client = patch_client(FakeClient(pages=[[_ad(10, 1), _ad(9, 2)], [_ad(8, 777), _ad(7, 3)], []]))
    q = ShowcaseQuery(our_mode="exact")
    res = await svc.list_showcase(db_session, project.id, q)
    assert [a["nm_id"] for a in res["ads"]] == [777]
    assert res["ads"][0]["is_ours"] is True
    assert res["scanned_pages"] == 2
    assert res["scan_truncated"] is False


async def test_showcase_session_expired_marks_and_raises(db_session, project, patch_client, monkeypatch):
    marked = {}

    async def fake_mark(db, pid):
        marked["pid"] = pid

    monkeypatch.setattr(integrations_service, "mark_wb_portal_expired", fake_mark)
    patch_client(FakeClient(expired=True))
    with pytest.raises(svc.CardExchangeError):
        await svc.list_showcase(db_session, project.id, ShowcaseQuery())
    assert marked["pid"] == project.id


async def test_showcase_portal_error_maps_to_domain(db_session, project, patch_client):
    patch_client(FakeClient(portal_error=True))
    with pytest.raises(svc.CardExchangeError):
        await svc.list_showcase(db_session, project.id, ShowcaseQuery())


# ─── корзина ─────────────────────────────────────────────────────────────────


async def test_cart_add_and_delete(db_session, project, patch_client):
    client = patch_client(FakeClient())
    assert await svc.cart_add(db_session, project.id, 555) is True
    assert client.cart_added == [555]
    assert await svc.cart_delete(db_session, project.id, [555, 556]) is True
    assert client.cart_deleted == [555, 556]


async def test_cart_get(db_session, project, patch_client):
    patch_client(FakeClient())
    cart = await svc.get_cart(db_session, project.id)
    assert "suppliers" in cart
