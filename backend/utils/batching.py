"""Порезка последовательностей на батчи (bulk-insert, внешние API).

ЗАЧЕМ: asyncpg не принимает >32767 параметров на statement (строки × колонки) —
multi-VALUES INSERT на тысячи строк обязан идти чанками. Уже стреляло на
wb_ad_upd (15к списаний × 10 колонок роняли дозагрузку «Истории затрат»).
"""

from collections.abc import Iterator, Sequence
from typing import TypeVar

T = TypeVar("T")

# ≤32767/колонок с запасом: 2000 строк безопасны для таблиц до 16 колонок
INSERT_CHUNK = 2000


def chunked(seq: Sequence[T], size: int = INSERT_CHUNK) -> Iterator[Sequence[T]]:
    """Режет последовательность на куски по size, сохраняя порядок."""
    if size <= 0:
        raise ValueError("size must be positive")
    for i in range(0, len(seq), size):
        yield seq[i : i + size]
