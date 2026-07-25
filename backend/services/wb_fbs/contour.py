# ruff: noqa: RUF001, RUF002, RUF003
"""
Контур данных WB FBS: изоляция песочницы от боевого зеркала.

Зачем: гейт режима (`wb_fbs_api._request(write=True)`) закрывает только запись
В WB. Запись в НАШИ таблицы он не гейтит, а зеркало заданий одно на оба контура:
задания песочницы легли бы в `wb_fbs_orders` неотличимо от боевых и дальше
вычитались бы из FBS-остатка, из доступного для сборки и — через
`writeoff_completed_orders` — из РЕАЛЬНОГО `WarehouseStock`.

Отдельной колонки-дискриминатора в моделях домена нет (и не заводится: это
потребовало бы миграции), поэтому метка контура живёт в уже существующей
JSONB-колонке `wb_fbs_orders.raw` под ключом `_dds_contour`. Строки, записанные
до появления метки, считаются боевыми — прежнее поведение прода бит-в-бит.

Контура ровно два: `sandbox` (режим `sandbox`) и `prod` (режимы `safe` и
`prod` — оба читают боевой кабинет, различаются только правом записи в WB).
"""

from typing import Any

from sqlalchemy import or_

from backend.config import WB_FBS_MODE_SANDBOX
from backend.integrations.wb_fbs_api import current_mode

#: Ключ метки внутри `wb_fbs_orders.raw`. Подчёркивание — маркер «поле наше,
#: а не из payload'а WB»: WB такими именами не пользуется.
CONTOUR_KEY = "_dds_contour"

CONTOUR_SANDBOX = "sandbox"
CONTOUR_PROD = "prod"


def current_contour(mode: str | None = None) -> str:
    """Контур текущего режима: `sandbox` для песочницы, `prod` для safe/prod."""
    return CONTOUR_SANDBOX if (mode or current_mode()) == WB_FBS_MODE_SANDBOX else CONTOUR_PROD


def is_sandbox_contour(mode: str | None = None) -> bool:
    """Идёт ли работа на тестовых данных песочницы."""
    return current_contour(mode) == CONTOUR_SANDBOX


def stamp_contour(raw: Any) -> dict[str, Any]:
    """Payload WB + метка контура. Исходный dict не мутируем (он уходит в лог/тесты)."""
    payload: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    payload[CONTOUR_KEY] = current_contour()
    return payload


def contour_condition(raw_column: Any) -> Any:
    """Условие «строка принадлежит ТЕКУЩЕМУ контуру» для WHERE по JSONB-колонке.

    В песочнице — только помеченные `sandbox`. В боевом контуре — всё, кроме
    песочницы, включая строки БЕЗ метки (зеркало, записанное до этой правки).
    Так фантомные задания песочницы перестают вычитаться из боевых остатков
    и списываться из ledger'а даже после возврата `WB_FBS_MODE` в `prod`.
    """
    marker = raw_column[CONTOUR_KEY].astext
    if is_sandbox_contour():
        return marker == CONTOUR_SANDBOX
    return or_(marker.is_(None), marker != CONTOUR_SANDBOX)


__all__ = [
    "CONTOUR_KEY",
    "CONTOUR_PROD",
    "CONTOUR_SANDBOX",
    "contour_condition",
    "current_contour",
    "is_sandbox_contour",
    "stamp_contour",
]
