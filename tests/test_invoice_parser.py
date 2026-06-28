# ruff: noqa: RUF001, RUF002, RUF003
"""
Тесты детерминированного распознавания реквизитов из счёта
(backend/services/invoice_parser.py).

Фокус — чистая `extract_requisites_from_text` на реалистичной шапке российской
платёжки. Happy-path р/с сконструирован так, чтобы пройти контроль-ключ по БИК
`044525225` (ПАО Сбербанк, есть в справочнике): см. `test_fixture_rs_is_valid`.
"""

import io
import zipfile
from decimal import Decimal

from backend.services.bank_directory import resolve_bank
from backend.services.invoice_parser import (
    _extract_text,
    _finalize,
    _image_media_type,
    _valid_rs,
    extract_requisites_from_text,
)

# БИК из справочника + счёт, прошедший контроль-ключ (вычислено) и не прошедший.
_BIK = "044525225"
_RS_VALID = "40702810380000100009"
_RS_INVALID = "40702810380000100000"
_CORR = "30101810400000000225"  # к/с Сбербанка (начинается с 301)


def _invoice_text(account: str) -> str:
    """Реалистичная шапка платёжного поручения с переданным расчётным счётом."""
    return (
        "ИНН 7707083893 КПП 770701001\n"
        f"Банк получателя ПАО Сбербанк\n"
        f"БИК {_BIK}\n"
        f"Сч. № {_CORR}\n"
        f"Получатель: ООО «Ромашка»\n"
        f"Сч. № {account}\n"
        "Счёт на оплату № 42 от 28.06.2026\n"
        "Всего к оплате: 1 234,56\n"
    )


def test_fixture_rs_is_valid():
    """Предохранитель: справочный БИК известен, сконструированный р/с проходит ключ."""
    assert resolve_bank(_BIK) is not None
    assert _valid_rs(_RS_VALID, _BIK) is True
    assert _valid_rs(_RS_INVALID, _BIK) is False


def test_extracts_inn_bik_amount():
    r = extract_requisites_from_text(_invoice_text(_RS_VALID))
    assert r.payee_inn == "7707083893"
    assert r.payee_kpp == "770701001"
    assert r.payee_bik == _BIK
    assert r.payee_bank_name == "ПАО Сбербанк"
    assert r.payee_corr_account == _CORR
    assert r.amount == Decimal("1234.56")
    assert r.payee_name == "ООО «Ромашка»"
    assert "payee_inn" in r.fields_found
    assert "payee_bik" in r.fields_found
    assert "amount" in r.fields_found


def test_finalize_llm_output_seller_npd():
    """_finalize: вывод текстового Claude для счёта самозанятого (НПД) — где regex путал
    продавца с покупателем/банком. Гейт доверия: р/с surface'ится, пройдя контроль-ключ."""
    r = _finalize({
        "payee_name": "ЩЕТКИНА АНАСТАСИЯ МИХАЙЛОВНА",
        "payee_inn": "643921964912",  # 12 цифр (самозанятый), НЕ ИНН покупателя
        "payee_kpp": None,
        "payee_account": _RS_VALID,
        "payee_bik": _BIK,
        "amount": "12 060,00",
        "purpose": "Оплата по счёту №30287768 от 12 июня 2026 за дизайн",
    })
    assert r.payee_name == "ЩЕТКИНА АНАСТАСИЯ МИХАЙЛОВНА"
    assert r.payee_inn == "643921964912"
    assert r.payee_kpp is None
    assert r.payee_bik == _BIK
    assert r.payee_account == _RS_VALID  # прошёл контроль-ключ → surface
    assert r.amount == Decimal("12060.00")
    assert r.purpose is not None and r.purpose.startswith("Оплата по счёту №30287768")
    assert {"payee_name", "payee_inn", "payee_bik", "payee_account", "amount", "purpose"} <= set(r.fields_found)


def test_finalize_rejects_bad_account():
    """р/с, не прошедший контроль-ключ по БИК → в warnings, поле пустое (LLM мог ошибиться в цифре)."""
    r = _finalize({"payee_bik": _BIK, "payee_account": _RS_INVALID, "payee_name": "ООО Тест"})
    assert r.payee_account is None
    assert any("контроль-ключ" in w for w in r.warnings)
    assert r.payee_name == "ООО Тест"


def test_payment_grid_name_and_purpose():
    """Реальный кейс (Альфа-счёт ИП): имя — отдельной строкой, «Получатель» стоит рядом со
    «Сч.№» в гриде; назначение — из «Счёт на оплату №X от …». Заказчик (плательщик) НЕ берём."""
    text = (
        f'АО "АЛЬФА-БАНК" БИК {_BIK}\n'
        f"Банк получателя Сч.№ {_CORR}\n"
        "ИНН 370254749999 КПП\n"
        "ИП ВЛАДИМИРОВА ЯРОСЛАВА ЕВГЕНЬЕВНА\n"
        f"Получатель Сч.№ {_RS_VALID}\n"
        "Счёт на оплату №10 от 26 июня 2026 г.\n"
        "Исполнитель ИП ВЛАДИМИРОВА ЯРОСЛАВА ЕВГЕНЬЕВНА, 153023, РОССИЯ\n"
        'Заказчик ООО "ПЛЮС ВАЙБ"\n'
        "Всего к оплате: 1 234,56\n"
    )
    r = extract_requisites_from_text(text)
    # имя — НЕ «Сч.№…» и НЕ заказчик «ООО ПЛЮС ВАЙБ»
    assert r.payee_name == "ИП ВЛАДИМИРОВА ЯРОСЛАВА ЕВГЕНЬЕВНА"
    assert r.payee_account == _RS_VALID
    assert r.purpose is not None and r.purpose.startswith("Оплата по счёту №10")
    assert "payee_name" in r.fields_found and "purpose" in r.fields_found


def test_valid_account_surfaces():
    r = extract_requisites_from_text(_invoice_text(_RS_VALID))
    assert r.payee_account == _RS_VALID
    assert "payee_account" in r.fields_found


def test_invalid_account_goes_to_warnings():
    r = extract_requisites_from_text(_invoice_text(_RS_INVALID))
    assert r.payee_account is None
    assert "payee_account" not in r.fields_found
    assert any("Расчётный счёт" in w for w in r.warnings)


def test_bik_not_in_directory_warns_and_skips_bank():
    text = (
        "ИНН 7707083893\n"
        "Банк получателя Некий Банк\n"
        "БИК 049999999\n"  # нет в справочнике
        "Получатель: ООО «Тест»\n"
        "Всего к оплате: 100,00\n"
    )
    r = extract_requisites_from_text(text)
    assert r.payee_bik is None
    assert r.payee_bank_name is None
    assert any("049999999" in w for w in r.warnings)
    assert r.payee_inn == "7707083893"  # остальное всё равно распозналось


def test_amount_russian_format_parsing():
    text = "Всего к оплате: 1 234,56"
    r = extract_requisites_from_text(text)
    assert r.amount == Decimal("1234.56")


def test_amount_prefers_total_to_pay():
    """При нескольких якорях берём «Всего к оплате», а не промежуточное «Итого»."""
    text = (
        "Итого: 1 000,00\n"
        "В том числе НДС: 166,67\n"
        "Всего к оплате: 1 200,00\n"
    )
    r = extract_requisites_from_text(text)
    assert r.amount == Decimal("1200.00")


def test_empty_text_returns_empty_result():
    r = extract_requisites_from_text("")
    assert r.payee_inn is None
    assert r.payee_bik is None
    assert r.payee_account is None
    assert r.amount is None
    assert r.fields_found == []
    assert r.warnings == []

    # whitespace-only — тоже пусто, без падений
    r2 = extract_requisites_from_text("   \n\t  ")
    assert r2.fields_found == []


def test_inn_12_digits_for_ip():
    """ИП — 12-значный ИНН (юрлицо — 10)."""
    text = "ИНН 770708389312\nПолучатель: ИП Иванов\n"
    r = extract_requisites_from_text(text)
    assert r.payee_inn == "770708389312"


def _build_minimal_docx(body_text: str) -> bytes:
    """Минимальный валидный .docx (zip с [Content_Types].xml + word/document.xml)."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        f"<w:p><w:r><w:t>{body_text}</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("word/document.xml", document)
    return buf.getvalue()


def test_extract_text_from_docx():
    """_extract_text достаёт текст из .docx (stdlib zipfile, без python-docx)."""
    data = _build_minimal_docx("БИК 044525225 ИНН 7707083893")
    text = _extract_text(data, "invoice.docx")
    assert "БИК 044525225" in text
    assert "7707083893" in text


def test_extract_text_unknown_type_returns_empty():
    assert _extract_text(b"plain text, not pdf or docx", "note.txt") == ""


# ─── Фото счёта (vision) ──────────────────────────────────────────────────────────


def test_image_media_type_by_magic():
    """Детект изображения по магическим байтам (имя без расширения)."""
    assert _image_media_type(b"\xff\xd8\xff\xe0blah", "scan") == "image/jpeg"
    assert _image_media_type(b"\x89PNG\r\n\x1a\nblah", "scan") == "image/png"
    assert _image_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ", "scan") == "image/webp"
    # HEIC: «ftypheic» на offset 4
    assert _image_media_type(b"\x00\x00\x00\x18ftypheic", "scan") == "image/heic"


def test_image_media_type_by_extension():
    """Расширение приоритетнее магии (телефон шлёт .heic с произвольным контентом)."""
    assert _image_media_type(b"whatever", "invoice.jpg") == "image/jpeg"
    assert _image_media_type(b"whatever", "INVOICE.HEIC") == "image/heic"
    assert _image_media_type(b"whatever", "scan.png") == "image/png"


def test_image_media_type_none_for_pdf_and_docx():
    assert _image_media_type(b"%PDF-1.7 ...", "invoice.pdf") is None
    assert _image_media_type(b"PK\x03\x04 ...", "invoice.docx") is None
    assert _image_media_type(b"plain", "note.txt") is None


async def test_parse_invoice_async_image_without_key_is_graceful(monkeypatch):
    """Фото без ANTHROPIC_API_KEY (regex по картинке невозможен) → мягкое предупреждение, не 500."""
    from backend.config import settings
    from backend.services import invoice_parser

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    r = await invoice_parser.parse_invoice_async(b"\xff\xd8\xff\xe0fake-jpeg", "scan.jpg")
    assert r.payee_inn is None
    assert r.fields_found == []
    assert r.warnings  # подсказка «введите вручную / приложите PDF»


async def test_parse_invoice_async_heic_conversion_failure_is_graceful(monkeypatch):
    """Битый HEIC (или отсутствие pillow-heif) → понятное предупреждение, без падения."""
    from backend.config import settings
    from backend.services import invoice_parser

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    r = await invoice_parser.parse_invoice_async(b"\x00\x00\x00\x18ftypheicGARBAGE", "photo.heic")
    assert r.fields_found == []
    assert r.warnings


# ─── _finalize: разнесение р/с и к/с (фото-путь) ────────────────────────────────


def test_finalize_picks_rs_by_prefix():
    """Корректно поданные счета: р/с (40…) в payee_account, к/с (301…) в corr."""
    r = _finalize({
        "payee_inn": "7707083893", "payee_kpp": None, "payee_bik": _BIK,
        "payee_account": _RS_VALID, "payee_corr_account": _CORR,
        "amount": "1000.00", "purpose": None, "payee_name": "ООО Тест",
    })
    assert r.payee_account == _RS_VALID
    assert r.payee_corr_account == _CORR
    assert "payee_account" in r.fields_found


def test_finalize_recovers_swapped_accounts():
    """Модель перепутала местами: р/с положила в corr, к/с — в account. Разносим по префиксу."""
    r = _finalize({
        "payee_inn": "7707083893", "payee_kpp": None, "payee_bik": _BIK,
        "payee_account": _CORR,           # на самом деле к/с
        "payee_corr_account": _RS_VALID,  # на самом деле р/с
        "amount": "1000.00", "purpose": None, "payee_name": "ООО Тест",
    })
    assert r.payee_account == _RS_VALID   # р/с восстановлен из перепутанного поля
    assert r.payee_corr_account == _CORR


def test_finalize_rs_fails_control_key_not_surfaced():
    """Счёт, не прошедший контроль-ключ (OCR-ошибка в цифре) — не подставляется, есть warning."""
    r = _finalize({
        "payee_inn": "7707083893", "payee_kpp": None, "payee_bik": _BIK,
        "payee_account": _RS_INVALID, "payee_corr_account": _CORR,
        "amount": "1000.00", "purpose": None, "payee_name": "ООО Тест",
    })
    assert r.payee_account is None
    assert any("контроль-ключ" in w for w in r.warnings)
