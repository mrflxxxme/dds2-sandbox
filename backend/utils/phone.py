"""Нормализация телефона к формату WB-пропуска (setTRNDetails)."""

import re

_PHONE_DIGITS_RE = re.compile(r"\D+")


def normalize_ru_phone(phone: str | None) -> str:
    """Телефон в формат WB-пропуска `79XXXXXXXXX` (11 цифр, ведущая 7).

    WB `setTRNDetails` отбивает всё, кроме чистых 11 цифр с ведущей 7:
    «8-918-882-98-32», «+79057026248», пробелы → `-32003 «Номер телефона не
    валиден»`. Приводим: оставляем только цифры; ведущая 8 при 11 цифрах → 7;
    10 цифр → префикс 7. Нераспознанный формат возвращаем как очищенные цифры
    (лучше отдать что-то, чем гарантированный отказ на дефисах/плюсе).
    """
    digits = _PHONE_DIGITS_RE.sub("", phone or "")
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return digits
