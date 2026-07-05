# ruff: noqa: RUF001, RUF002, RUF003
"""Тесты бэкенд-геометрии паллеты (`pallet_geo`) — паритет с фронтом boxPallet.ts.

Чистые функции (без БД): парсинг box_size, коробок в слое/на паллету, лимит
высоты по складу, эффективное «коробок на паллету» с ручным override.
"""

from backend.services.assembly.pallet_geo import (
    boxes_per_layer,
    boxes_per_pallet,
    effective_boxes_per_pallet,
    max_pallet_height_cm,
    parse_box_size,
)


def test_parse_box_size():
    assert parse_box_size("60x40x40") == (60.0, 40.0, 40.0)
    assert parse_box_size("60×40×50") == (60.0, 40.0, 50.0)  # кир. разделитель
    assert parse_box_size("60*40*40") == (60.0, 40.0, 40.0)
    assert parse_box_size(None) is None
    assert parse_box_size("нет размера") is None
    assert parse_box_size("60x40") is None  # <3 чисел
    assert parse_box_size("60x40x0") is None  # нулевая грань


def test_boxes_per_layer_best_orientation():
    # 60×40 на 120×80: ор1 = floor(120/60)*floor(80/40) = 2*2 = 4; ор2 = 3*1 = 3 → 4.
    assert boxes_per_layer(60, 40) == 4
    # 40×30: ор1 = floor(120/40)*floor(80/30) = 3*2 = 6; ор2 = floor(120/30)*floor(80/40)=4*2=8 → 8.
    assert boxes_per_layer(40, 30) == 8


def test_boxes_per_pallet():
    # высота 40, лимит 180: слоёв floor((180-14.5)/40)=4; 4 коробки/слой × 4 = 16.
    assert boxes_per_pallet((60.0, 40.0, 40.0), 180) == 16
    # коробка выше бюджета высоты → 0 слоёв → None.
    assert boxes_per_pallet((60.0, 40.0, 200.0), 180) is None
    # коробка больше основания → None.
    assert boxes_per_pallet((200.0, 200.0, 20.0), 180) is None


def test_max_pallet_height_cm():
    assert max_pallet_height_cm("Воронеж") == 185
    assert max_pallet_height_cm("Коледино (СГТ)") == 180  # скобки отбрасываются
    assert max_pallet_height_cm("Екатеринбург: Перспективная") == 170  # хвост после «:»
    assert max_pallet_height_cm("Неизвестный склад") == 180  # дефолт
    assert max_pallet_height_cm(None) == 180


def test_effective_boxes_per_pallet_override_wins():
    # ручной override по размеру перебивает геометрию.
    assert effective_boxes_per_pallet("60x40x40", 180, {"60x40x40": 99}) == 99
    # без override — геометрия.
    assert effective_boxes_per_pallet("60x40x40", 180, None) == 16
    # нет габаритов и нет override → None.
    assert effective_boxes_per_pallet(None, 180, None) is None
    assert effective_boxes_per_pallet("нет", 180, {}) is None
