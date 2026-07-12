# ruff: noqa: RUF002 — русские строки в тест-данных
"""Тесты reviews_service: no-key ветка и маппинг WB feedbacks → ReviewItem."""

from unittest.mock import AsyncMock, MagicMock

from backend.services import reviews_service

PROJECT_ID = 4321


async def test_list_reviews_no_key_returns_empty(monkeypatch):
    """Нет активного WB-ключа → has_key=False, пустой список, без обращения к WB."""
    monkeypatch.setattr(
        "backend.services.funnel.wb_api_client.get_wb_key",
        AsyncMock(return_value=None),
    )
    res = await reviews_service.list_reviews(AsyncMock(), PROJECT_ID)

    assert res.has_key is False
    assert res.items == []
    assert res.count_unanswered == 0
    assert res.average_rating is None


async def test_list_reviews_maps_and_averages(monkeypatch):
    """Ключ есть → feedbacks мапятся в ReviewItem, средняя оценка считается."""
    monkeypatch.setattr(
        "backend.services.funnel.wb_api_client.get_wb_key",
        AsyncMock(return_value="test-key"),
    )
    fake_client = MagicMock()
    fake_client.get_feedbacks = AsyncMock(
        return_value={
            "countUnanswered": 2,
            "countArchive": 5,
            "feedbacks": [
                {
                    "id": "a",
                    "text": "Отличный товар",
                    "productValuation": 5,
                    "createdDate": "2026-07-01T10:00:00Z",
                    "userName": "Иван",
                    "answer": None,
                    "productDetails": {
                        "nmId": 111,
                        "productName": "Носки",
                        "supplierArticle": "SKU1",
                        "brandName": "Бренд",
                    },
                },
                {
                    "id": "b",
                    "text": "Плохо",
                    "productValuation": 3,
                    "pros": "цена",
                    "cons": "качество",
                    "answer": {"text": "спасибо за отзыв"},
                    "productDetails": {"nmId": 222},
                },
            ],
        }
    )
    monkeypatch.setattr(
        "backend.integrations.wb_api.WBApiClient",
        MagicMock(return_value=fake_client),
    )

    res = await reviews_service.list_reviews(AsyncMock(), PROJECT_ID, is_answered=False)

    assert res.has_key is True
    assert res.count_unanswered == 2
    assert res.count_archive == 5
    assert len(res.items) == 2
    assert res.average_rating == 4.0  # (5 + 3) / 2

    first, second = res.items
    assert first.rating == 5
    assert first.nm_id == 111
    assert first.product_name == "Носки"
    assert first.article == "SKU1"
    assert first.brand == "Бренд"
    assert first.is_answered is False

    # answer присутствует → is_answered=True; productName отсутствует → None
    assert second.is_answered is True
    assert second.pros == "цена"
    assert second.cons == "качество"
    assert second.product_name is None
