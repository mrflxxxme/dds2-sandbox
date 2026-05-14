"""Unit-тесты для `_normalize_srid` — нормализация WB-srid для матчинга
с `order_city_map`.

Контекст: WB API `/supplier/orders` возвращает srid с префиксом
`eXX.r` или `eXX.i` (по типу сделки), а Excel-выгрузка «Лента заказов»
сохраняет только core `hex32.X.X`. Без нормализации `city_map.get(srid)`
никогда не матчится — priority-chain fallback по городу не работает,
и всё распределение падает на менее точный okrug-fallback.
"""

from __future__ import annotations

from backend.services.warehouse_need_service import _normalize_srid


def test_normalize_srid_with_r_prefix():
    """Стандартный WB-srid типа 'eXX.r<hex32>.X.X' → core форма."""
    raw = "eBw.r62e0deb30efa49d3b0f2c9e6cc87266c.0.0"
    assert _normalize_srid(raw) == "62e0deb30efa49d3b0f2c9e6cc87266c.0.0"


def test_normalize_srid_with_i_prefix():
    """Вариант 'eXX.i<hex32>.X.X' (исходящий тип) — тоже обрезается."""
    raw = "eAZ.i5c58d61258267db77736c6b32dc6637e.2.0"
    assert _normalize_srid(raw) == "5c58d61258267db77736c6b32dc6637e.2.0"


def test_normalize_srid_already_normalized():
    """Если srid уже в core форме (как в order_city_map) — passthrough."""
    raw = "01d0e655d912407bae34c69f686d3d42.3.0"
    assert _normalize_srid(raw) == raw


def test_normalize_srid_empty_and_none():
    """Пустой/None → пустая строка (не падаем)."""
    assert _normalize_srid("") == ""
    assert _normalize_srid(None) == ""


def test_normalize_srid_short_prefix():
    """Префикс из одного-двух символов: 'eh.r...' (real WB shape)."""
    raw = "eh.red721d8557f745beaee64127cd474667.0.0"
    assert _normalize_srid(raw) == "ed721d8557f745beaee64127cd474667.0.0"


def test_normalize_srid_unrelated_string():
    """Без 'eXX.r/i' префикса — passthrough без изменений."""
    assert _normalize_srid("abc.def") == "abc.def"
