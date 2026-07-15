# ruff: noqa: RUF001, RUF002, RUF003
"""_normalize_ru_phone — телефон в формат WB-пропуска 79XXXXXXXXX.

Регресс прод-бага: setTRNDetails отбивал «8-918-…»/«+7…» как «Номер телефона
не валиден», проходил только чистый 79XXXXXXXXX (см. заносы ASM-316/581/582…).
"""

import pytest

from backend.integrations.wb_portal_client import _normalize_ru_phone


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("8-918-882-98-32", "79188829832"),   # ведущая 8 + дефисы (Колисниченко)
        ("8-909-363-11-66", "79093631166"),   # Хайдарханов
        ("+79057026248", "79057026248"),       # +7 (Звягинцев)
        ("79032861381", "79032861381"),        # уже валидный (Нечаев)
        ("8 909 363 11 66", "79093631166"),   # пробелы
        ("+7 (905) 702-62-48", "79057026248"), # +7 со скобками/пробелами
        ("9057026248", "79057026248"),          # 10 цифр без кода
    ],
)
def test_normalizes_to_wb_format(raw, expected):
    assert _normalize_ru_phone(raw) == expected


def test_empty_and_none_safe():
    assert _normalize_ru_phone("") == ""
    assert _normalize_ru_phone(None) == ""  # type: ignore[arg-type]


def test_unrecognized_returns_digits():
    # Нераспознанная длина — отдаём очищенные цифры (не роняем занос на дефисах).
    assert _normalize_ru_phone("12-34") == "1234"
