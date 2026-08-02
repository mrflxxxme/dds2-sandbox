# ruff: noqa: RUF002, RUF003
"""Tests: раздел «Биржа карточек товаров» — справочник категорий, маппинг, витрина, корзина.

Клиент WB мокается FakeClient (get_wb_portal_client подменяется), БД к WB не ходит.
"""

import json

import pytest

from backend.integrations.wb_portal_client import WbPortalError, WbSessionExpired
from backend.schemas.card_exchange import ShowcaseQuery
from backend.services import integrations_service
from backend.services.card_exchange import categories as cat_ref
from backend.services.card_exchange import showcase as svc


# ─── FakeClient ─────────────────────────────────────────────────────────────


class FakeClient:
    """Заглушка WbPortalClient: подставные предметы/объявления, лог фильтров/курсоров."""

    def __init__(self, *, subjects=None, pages=None, expired=False, portal_error=False, ad_subjects=None):
        # subjects: [{"id","name"}]; pages: список страниц (каждая — список ad-dict WB-формы)
        self._subjects = subjects if subjects is not None else [
            {"id": 100, "name": "Компрессоры автомобильные", "category": ""},
            {"id": 101, "name": "Насосы автомобильные", "category": ""},
            {"id": 200, "name": "Брюки", "category": ""},
        ]
        self._pages = pages if pages is not None else [[]]
        # adID → предметы вариантов объявления (источник корневых категорий)
        self._ad_subjects = ad_subjects or {}
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

    async def showcase_ad_details(self, ad_id):
        names = self._ad_subjects.get(ad_id, [])
        return [{"nmID": 1, "meta": {"subjectName": n}} for n in names]

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


@pytest.fixture(autouse=True)
async def _clear_ad_subject_cache():
    """Кэш предметов объявлений глобальный (Redis) — чистим, чтобы тесты не влияли друг на друга."""
    from backend.cache import get_redis

    async def _wipe():
        try:
            redis = await get_redis()
            keys = await redis.keys(f"{svc._AD_SUBJ_PREFIX}*")
            if keys:
                await redis.delete(*keys)
        except Exception:  # noqa: BLE001 — без Redis тесты всё равно проходят
            pass

    await _wipe()
    yield
    await _wipe()


@pytest.fixture
def patch_client(monkeypatch):
    """Подменяет клиент СОБСТВЕННОЙ сессии биржи (не сессии поставок)."""
    def _apply(client):
        async def fake_get(db, project_id):
            return client
        monkeypatch.setattr(integrations_service, "get_wb_exchange_client", fake_get)
        return client
    return _apply


async def _add_nomenclature(db, project_id, *, barcode, nm_id, subject):
    from backend.models.cost import Nomenclature

    db.add(Nomenclature(project_id=project_id, barcode=barcode, article_wb=nm_id, subject=subject))
    await db.commit()


# ─── справочник категорий ────────────────────────────────────────────────────


def test_categories_loaded():
    cats = cat_ref.list_root_categories()
    assert len(cats) == 96
    assert sum(c["subject_count"] for c in cats) == 7424
    names = {c["category"] for c in cats}
    assert "Автоаксессуары и дополнительное оборудование" in names


def test_categories_match_response_schema():
    """Ключи сервиса обязаны собираться в RootCategory — роутер делает RootCategory(**c).

    Ловит рассинхрон имён (был баг: сервис отдавал camelCase subjectCount → 500 на /categories).
    """
    from backend.schemas.card_exchange import RootCategory

    items = [RootCategory(**c) for c in cat_ref.list_root_categories()]
    assert len(items) == 96
    assert all(i.subject_count > 0 for i in items)


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

    monkeypatch.setattr(integrations_service, "mark_wb_exchange_expired", fake_mark)
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


def test_root_category_reverse_index():
    assert cat_ref.root_category_for_subject("Компрессоры автомобильные") == "Автоаксессуары и дополнительное оборудование"
    assert cat_ref.root_category_for_subject("Корректоры") == "Красота"
    assert cat_ref.root_category_for_subject("Такого предмета нет") is None
    # несколько предметов → уникальные категории
    cats = cat_ref.root_categories_for_subjects(["Корректоры", "Компрессоры автомобильные", "Корректоры"])
    assert cats == sorted(set(cats), key=cats.index)
    assert "Красота" in cats and "Автоаксессуары и дополнительное оборудование" in cats


async def test_showcase_marks_categories_and_ours(db_session, project, patch_client):
    """Объявление получает корневые категории по предметам вариантов; наши — пересечение."""
    await _add_nomenclature(db_session, project.id, barcode="bc-cat", nm_id=555, subject="Корректоры")
    client = patch_client(FakeClient(
        pages=[[_ad(10, 1), _ad(9, 2)]],
        # у первого объявления два предмета из разных категорий, у второго — не наш
        ad_subjects={10: ["Корректоры", "Компрессоры автомобильные"], 9: ["Компрессоры автомобильные"]},
    ))
    res = await svc.list_showcase(db_session, project.id, ShowcaseQuery())
    first, second = res["ads"][0], res["ads"][1]
    assert first["categories"] == sorted(first["categories"], key=first["categories"].index)
    assert set(first["categories"]) == {"Красота", "Автоаксессуары и дополнительное оборудование"}
    assert first["our_categories"] == ["Красота"]      # наши товары только в «Красоте»
    assert second["our_categories"] == []              # пересечения с нашими нет
    assert client.last_filter is not None


async def test_showcase_category_enrich_survives_details_error(db_session, project, patch_client):
    """Сбой деталей не роняет выдачу — объявление просто без категорий."""
    class Boom(FakeClient):
        async def showcase_ad_details(self, ad_id):
            raise WbPortalError("details 500")

    patch_client(Boom(pages=[[_ad(10, 1)]]))
    res = await svc.list_showcase(db_session, project.id, ShowcaseQuery())
    assert res["ads"][0]["categories"] == []


# ─── сессия биржи: отдельный слот ────────────────────────────────────────────


async def test_exchange_session_is_separate_slot(db_session, project):
    """Сессия биржи хранится отдельно от сессии поставок и не видна через неё."""
    from backend.models.integrations import IntegrationKey
    from backend.utils.crypto import encrypt

    db_session.add(
        IntegrationKey(
            project_id=project.id,
            service=integrations_service.WB_EXCHANGE_SERVICE,
            label=integrations_service.WB_EXCHANGE_LABEL,
            encrypted_key=encrypt("exchange-token"),
            is_active=True,
            config={"status": "ACTIVE"},
        )
    )
    await db_session.commit()

    assert (await integrations_service.get_wb_exchange_status(db_session, project.id))["status"] == "ACTIVE"
    # слот поставок при этом пуст — сессии независимы
    assert (await integrations_service.get_wb_portal_status(db_session, project.id))["status"] == "NONE"
    client = await integrations_service.get_wb_exchange_client(db_session, project.id)
    assert client.authorizev3 == "exchange-token"


async def test_exchange_session_absent_raises(db_session, project):
    with pytest.raises(ValueError, match="Биржа карточек"):
        await integrations_service.get_wb_exchange_client(db_session, project.id)


def test_normalize_exchange_input_bundle_with_supplier():
    """Бандл со списком cookie: определяем продавца и схлопываем дубли по имени."""
    raw = json.dumps({
        "authorizev3": "tok",
        "cookies": [
            {"name": "x-supplier-id", "value": "sup-42"},
            {"name": "__zzatw-wb", "value": "old"},
            {"name": "__zzatw-wb", "value": "new"},  # дубль — последний выигрывает
        ],
    })
    session_json, supplier = integrations_service._normalize_exchange_input(raw)
    assert supplier == "sup-42"
    parsed = json.loads(session_json)
    assert parsed["authorizev3"] == "tok"
    names = [c["name"] for c in parsed["cookies"]]
    assert names.count("__zzatw-wb") == 1
    assert next(c["value"] for c in parsed["cookies"] if c["name"] == "__zzatw-wb") == "new"


def test_normalize_exchange_input_cookie_string():
    """cookies строкой (как document.cookie) — тоже принимаем."""
    raw = json.dumps({"authorizev3": "tok", "cookies": "x-supplier-id=sup-7; cfidsw-wb=abc"})
    session_json, supplier = integrations_service._normalize_exchange_input(raw)
    assert supplier == "sup-7"
    assert {c["name"] for c in json.loads(session_json)["cookies"]} == {"x-supplier-id", "cfidsw-wb"}


def test_normalize_exchange_input_bare_token_and_errors():
    session_json, supplier = integrations_service._normalize_exchange_input("plain-token")
    assert json.loads(session_json)["authorizev3"] == "plain-token"
    assert supplier is None
    with pytest.raises(ValueError, match="Пустой доступ"):
        integrations_service._normalize_exchange_input("   ")
    with pytest.raises(ValueError, match="не разобрался"):
        integrations_service._normalize_exchange_input("{сломанный json")
    with pytest.raises(ValueError, match="нет authorizev3"):
        integrations_service._normalize_exchange_input(json.dumps({"cookies": []}))


async def test_exchange_session_expired_status(db_session, project):
    from backend.models.integrations import IntegrationKey
    from backend.utils.crypto import encrypt

    db_session.add(
        IntegrationKey(
            project_id=project.id,
            service=integrations_service.WB_EXCHANGE_SERVICE,
            label=integrations_service.WB_EXCHANGE_LABEL,
            encrypted_key=encrypt("t"),
            is_active=True,
            config={"status": "ACTIVE"},
        )
    )
    await db_session.commit()
    await integrations_service.mark_wb_exchange_expired(db_session, project.id)
    assert (await integrations_service.get_wb_exchange_status(db_session, project.id))["status"] == "EXPIRED"
