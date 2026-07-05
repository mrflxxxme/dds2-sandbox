# ruff: noqa: RUF002, RUF003
"""Геометрия паллеты из габаритов коробки — БЭКЕНД-ЗЕРКАЛО фронта
`frontend-react/src/lib/utils/boxPallet.ts`. Значения и формулы держать В ПАРИТЕТЕ
(WB-правила FBW: евро-паллета 120×80, база 14,5 см, лимит высоты по складу).

Оценка числа паллет заявки = `ceil(Σ qtyᵢ / units_per_palletᵢ)` — тот же footprint
смешанной паллеты, что на фронте (каждый SKU по своей геометрии). Это лишь ОЦЕНКА
для авто-подстановки (кол-во паллет затем правится вручную).
"""

from __future__ import annotations

import math
import re

# Полезное основание евро-паллеты, см.
EURO_PALLET_LENGTH = 120
EURO_PALLET_WIDTH = 80
# Высота самой паллеты без товара, см (евростандарт).
PALLET_BASE_HEIGHT_CM = 14.5
# Дефолтный лимит высоты «товар + паллета», см, если склад неизвестен.
DEFAULT_MAX_PALLET_HEIGHT_CM = 180

# Зеркало WAREHOUSE_MAX_PALLET_HEIGHT_CM (boxPallet.ts) — сверять при изменении.
WAREHOUSE_MAX_PALLET_HEIGHT_CM: dict[str, int] = {
    "воронеж": 185,
    "коледино": 180,
    "электросталь": 180,
    "казань": 180,
    "тула": 180,
    "невинномысск": 180,
    "чехов-1": 180,
    "чехов-2": 180,
    "чехов": 180,
    "сарапул": 180,
    "владимир": 180,
    "новосемейкино": 175,
    "самара": 175,
    "краснодар": 170,
    "волгоград": 170,
    "алматы атакент": 170,
    "екатеринбург": 170,
    "астана 2": 170,
    "котовск": 160,
    "рязань": 160,
}

_PARENS_RE = re.compile(r"\s*\([^)]*\)\s*")
_TAIL_RE = re.compile(r"[:,].*$")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _norm_warehouse_key(name: str) -> str:
    """Нормализация имени склада для lookup высоты (зеркало normWarehouseKey)."""
    s = (name or "").lower()
    s = _PARENS_RE.sub(" ", s)
    s = _TAIL_RE.sub("", s)
    return s.strip()


def max_pallet_height_cm(warehouse_name: str | None) -> int:
    """Лимит высоты паллеты для склада (см). Неизвестный склад → дефолт 180.

    Точное совпадение нормализованного ключа, иначе префиксное (имя начинается с
    известного ключа), иначе дефолт. Зеркало `maxPalletHeightCm`.
    """
    if not warehouse_name:
        return DEFAULT_MAX_PALLET_HEIGHT_CM
    key = _norm_warehouse_key(warehouse_name)
    if key in WAREHOUSE_MAX_PALLET_HEIGHT_CM:
        return WAREHOUSE_MAX_PALLET_HEIGHT_CM[key]
    for k, v in WAREHOUSE_MAX_PALLET_HEIGHT_CM.items():
        if key.startswith(k):
            return v
    return DEFAULT_MAX_PALLET_HEIGHT_CM


def parse_box_size(box_size: str | None) -> tuple[float, float, float] | None:
    """`Д×Ш×В` (см) → (length, width, height). None если <3 чисел или ≤0.

    Извлекаем числа независимо от разделителей/опечаток (зеркало parseBoxSize).
    """
    if not box_size:
        return None
    nums = [float(x) for x in _NUM_RE.findall(box_size)]
    if len(nums) < 3:
        return None
    length, width, height = nums[0], nums[1], nums[2]
    if length <= 0 or width <= 0 or height <= 0:
        return None
    return length, width, height


def boxes_per_layer(length: float, width: float) -> int:
    """Коробок данного размера в ОДНОМ слое на основание (лучшая из 2 ориентаций)."""
    o1 = math.floor(EURO_PALLET_LENGTH / length) * math.floor(EURO_PALLET_WIDTH / width)
    o2 = math.floor(EURO_PALLET_LENGTH / width) * math.floor(EURO_PALLET_WIDTH / length)
    return max(o1, o2)


def boxes_per_pallet(dims: tuple[float, float, float], max_height_cm: float = DEFAULT_MAX_PALLET_HEIGHT_CM) -> int | None:
    """Коробок данного размера на ОДНУ паллету (слои × коробок/слой). None если не
    помещается на основание ИЛИ высота+паллета превышает лимит склада."""
    length, width, height = dims
    per_layer = boxes_per_layer(length, width)
    if per_layer < 1:
        return None
    layers = math.floor((max_height_cm - PALLET_BASE_HEIGHT_CM) / height)
    if layers < 1:
        return None
    return per_layer * layers


def effective_boxes_per_pallet(
    box_size: str | None,
    max_height_cm: float,
    overrides: dict[str, int] | None,
) -> int | None:
    """Ручной override «коробок на паллету» по размеру ПЕРЕБИВАЕТ геометрию.
    None — нет габаритов и нет override. Зеркало `effectiveBoxesPerPallet`.
    """
    if overrides:
        from backend.services.settings_service import _canon_box_size

        ov = overrides.get(_canon_box_size(box_size or ""))
        if ov and ov > 0:
            return ov
    dims = parse_box_size(box_size)
    return boxes_per_pallet(dims, max_height_cm) if dims else None
