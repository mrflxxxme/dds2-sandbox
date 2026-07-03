# ruff: noqa: RUF001, RUF002, RUF003
"""Детерминированное распознавание реквизитов из файла счёта (PDF/Word).

БЕЗ ИИ/OCR — только разбор извлечённого текста по якорям («ИНН», «БИК», «Банк
получателя», «Получатель», «Всего к оплате») и структуре российской платёжки.
Результат — ПОДСКАЗКА для формы (`InvoiceParseResult`), в БД не пишется: поля, не
прошедшие проверку (контроль-ключ р/с по БИК, БИК в справочнике), остаются None,
их пользователь вводит вручную.

Текст из PDF — через pdfplumber (как `services/planning/customs.py`); из .docx —
stdlib zipfile + регулярка по `word/document.xml` (без python-docx). Новых
зависимостей не добавляем.
"""

import asyncio
import html
import io
import logging
import re
import zipfile
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from backend.config import settings
from backend.schemas.payment_request import InvoiceParseResult, ParsedDocument
from backend.services import invoice_split
from backend.services.bank_directory import resolve_bank

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger("dds.payment_request")

# ─── Извлечение текста ──────────────────────────────────────────────────────────

_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"
_MAX_PDF_PAGES = 10              # счёт — 1-2 страницы; глубже не разбираем (анти-DoS)
_MAX_XML_BYTES = 20 * 1024 * 1024  # потолок РАСПАКОВАННОГО document.xml (анти zip-bomb)


def _extract_text_pdf(data: bytes) -> str:
    """Собрать текст первых страниц PDF через pdfplumber (кап страниц — анти-DoS)."""
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:_MAX_PDF_PAGES]:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def _extract_text_docx(data: bytes) -> str:
    """Достать текст из .docx без python-docx: word/document.xml → снять теги.

    `</w:p>` (конец абзаца) превращаем в перенос строки, чтобы якоря и значения
    не слипались в одну строку; затем убираем все XML-теги и раскодируем сущности.
    Распаковку ограничиваем `_MAX_XML_BYTES` (защита от zip-bomb).
    """
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        try:
            with zf.open("word/document.xml") as f:
                raw = f.read(_MAX_XML_BYTES)  # bounded read → не распаковываем гигабайты
        except KeyError:
            return ""
    xml = raw.decode("utf-8", errors="replace")
    xml = xml.replace("</w:p>", "\n")
    text = re.sub(r"<[^>]+>", "", xml)
    return html.unescape(text)


def _extract_text(data: bytes, filename: str) -> str:
    """Извлечь текст из счёта по расширению/магическим байтам. Неизвестный тип → ""."""
    name = (filename or "").lower()
    head = data[:4]
    if name.endswith(".pdf") or head == _PDF_MAGIC:
        return _extract_text_pdf(data)
    if name.endswith(".docx") or head == _ZIP_MAGIC:
        return _extract_text_docx(data)
    return ""


# ─── Фото счёта (vision) ──────────────────────────────────────────────────────────
# Айфон/телефон снимает счёт фотографией. Текста извлечь нечего → отдаём картинку
# vision-Claude тем же tool-схемой. HEIC (дефолт айфона) Claude не принимает →
# конвертируем в JPEG через pillow-heif. Claude vision принимает jpeg/png/webp/gif.

_IMAGE_EXT_MEDIA = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
}
_MAX_IMAGE_PIXELS = 40_000_000  # анти-pixel-bomb для HEIC-декода (счёт-фото ≪ 40 Мп)


def _image_media_type(data: bytes, filename: str) -> str | None:
    """Media-type изображения по расширению/магии; не картинка (PDF/Word/прочее) → None."""
    name = (filename or "").lower()
    for ext, mt in _IMAGE_EXT_MEDIA.items():
        if name.endswith(ext):
            return mt
    head = data[:12]
    if head[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if head[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heif", b"mif1", b"msf1", b"hevc"):
        return "image/heic"
    return None


def _convert_heic_to_jpeg(data: bytes) -> bytes:
    """HEIC/HEIF → JPEG (Claude vision не принимает HEIC). Требует pillow-heif."""
    import pillow_heif  # type: ignore  # ставится в образе; нет py.typed → глушим import-untyped
    from PIL import Image

    pillow_heif.register_heif_opener()
    img = Image.open(io.BytesIO(data))  # ленивое открытие — пиксели ещё не декодированы
    w, h = img.size
    if w * h > _MAX_IMAGE_PIXELS:  # огромный холст HEIF → не аллоцируем гигабайты на convert
        raise ValueError(f"image too large: {w}x{h}")
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ─── Разбор реквизитов ──────────────────────────────────────────────────────────

# ИНН у якоря: берём ЦЕЛЫЙ пробег цифр (граница `\b…\b`), длину 10 (юрлицо) / 12 (ИП)
# проверяем в коде — иначе `\d{10}|\d{12}` усекает 12-значный ИНН до 10.
_INN_RE = re.compile(r"ИНН\D{0,40}?\b(\d+)\b")
_KPP_RE = re.compile(r"КПП\D{0,40}?(\d{9})")
_BIK_RE = re.compile(r"БИК\D{0,40}?(\d{9})")
_ACC20_RE = re.compile(r"\b\d{20}\b")
# Счёт разбит пробелами по группам («40802 810 6 4007 0023507», как в счетах Сбербизнеса) —
# слитный \d{20} такое не ловит. Берём пробег цифр+пробелов, нормализуем, проверяем длину 20.
_ACC20_SPACED_RE = re.compile(r"\b\d[\d ]{18,28}\d\b")
# Сумма после якоря «Всего к оплате» / «Итого к оплате» / «Всего» / «Итого».
_TOTAL_RE = re.compile(
    r"(Всего\s+к\s+оплате|Итого\s+к\s+оплате|Всего\s+к\s+уплате|Всего|Итого)"
    r"[^\d\-]{0,40}?(\d[\d\s]*[,\.]\d{2})"
)
# Получатель: текст после «Получатель» до конца строки / до «ИНН» / до «Сч».
_PAYEE_RE = re.compile(r"Получател[ья]\s*:?\s*([^\n]+)")

_RS_PREFIXES = ("40", "30", "42", "47")

# Орг-форма получателя — чтобы отличать имя от «Сч.№…»/мусора. Получатель в счёте =
# исполнитель/поставщик/продавец (НЕ заказчик/покупатель — это плательщик).
_ORG = r"(?:ООО|ЗАО|ОАО|ПАО|НАО|НКО|КФХ|АО|ИП)"
_PAYEE_ROLE_RE = re.compile(rf"(?:Исполнитель|Поставщик|Продавец)\s+({_ORG}\b[^,\n]+)", re.IGNORECASE)
_ORG_LINE_RE = re.compile(rf"(?m)^[ \t]*({_ORG}\b[^\n,]+)")
# Назначение: «Счёт на оплату №X от <дата>» → «Оплата по счёту №X от <дата>».
_PURPOSE_RE = re.compile(r"Сч[её]т\s+на\s+оплату\s+(№\s*\S+\s+от\s+[^\n]+?)(?:\s*г\.|\n|$)", re.IGNORECASE)
# Основание-договор: «Договор № КВ/25-082 от 08.04.2025» → «по договору № … от …».
_CONTRACT_RE = re.compile(
    r"Договор\D{0,40}?(?:№|N)\s*([^\s,;]+)\s*от\s*(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4})", re.IGNORECASE
)
# НДС: явная ставка «НДС 20%» (+ сумма рядом, если есть) либо пометка «Без НДС / не облагается».
_VAT_RATE_RE = re.compile(r"НДС\s*(\d{1,2})\s*%(?:[^\d\-%]{0,25}?(\d[\d\s]*[,.]\d{2}))?", re.IGNORECASE)
_VAT_NONE_RE = re.compile(r"Без\s+НДС|НДС\s+не\s+облага|не\s+облага\w*\s+НДС", re.IGNORECASE)


def _valid_bik(bik: str) -> bool:
    """Российский БИК: 9 цифр, начинается с «04» (код РФ в платёжной системе). Отсекает
    р/с (40…) и к/с (301…), ошибочно принятые распозналкой за БИК (напр. «408028108»)."""
    return len(bik) == 9 and bik.isdigit() and bik.startswith("04")


def _valid_rs(account: str, bik: str) -> bool:
    """Контроль-ключ расчётного счёта по БИК (алгоритм ЦБ РФ): сумма по модулю 10 == 0."""
    if not (len(account) == 20 and account.isdigit() and len(bik) == 9 and bik.isdigit()):
        return False
    s = bik[-3:] + account  # 23 цифры
    weights = [7, 1, 3] * 7 + [7, 1]  # 23 веса: 7,1,3,7,1,3,...,7,1
    return sum(int(d) * w for d, w in zip(s, weights)) % 10 == 0


def _parse_amount(raw: str) -> Decimal | None:
    """«1 234,56» / «1234.56» → Decimal. Неразбираемое → None."""
    cleaned = raw.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _find_amount(text: str) -> Decimal | None:
    """Предпочитаем «Всего к оплате» / «Итого к оплате»; иначе любой «Всего»/«Итого»."""
    best: Decimal | None = None
    best_rank = -1
    priority = {"всего к оплате": 3, "итого к оплате": 3, "всего к уплате": 3, "итого": 1, "всего": 1}
    for m in _TOTAL_RE.finditer(text):
        anchor = re.sub(r"\s+", " ", m.group(1)).strip().lower()
        rank = priority.get(anchor, 0)
        val = _parse_amount(m.group(2))
        if val is None:
            continue
        if rank > best_rank:
            best, best_rank = val, rank
    return best


def _find_accounts(text: str) -> list[str]:
    """20-значные счета: и слитные, и разбитые пробелами («40802 810 6 4007 0023507»)."""
    out: list[str] = []
    seen: set[str] = set()
    for acc in _ACC20_RE.findall(text):
        if acc not in seen:
            seen.add(acc)
            out.append(acc)
    for chunk in _ACC20_SPACED_RE.findall(text):
        d = chunk.replace(" ", "")
        if len(d) == 20 and d.isdigit() and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def _split_accounts(text: str, corr_from_bik: str | None) -> tuple[str | None, str | None]:
    """Разнести все 20-значные счёта на (расчётный, корреспондентский).

    к/с — начинается с «301» (или совпал со справочным corr по БИК); р/с — первый
    не-301 с банковским префиксом (40/30 кроме 301/42/47).
    """
    accounts = _find_accounts(text)
    corr: str | None = None
    rs: str | None = None
    for acc in accounts:
        if corr_from_bik is not None and acc == corr_from_bik:
            corr = acc
            continue
        if acc.startswith("301"):
            if corr is None:
                corr = acc
            continue
        if rs is None and acc.startswith(_RS_PREFIXES):
            rs = acc
    # Фолбэк: р/с среди оставшихся, если префиксная эвристика ничего не дала.
    if rs is None:
        for acc in accounts:
            if not acc.startswith("301") and acc != corr_from_bik:
                rs = acc
                break
    return rs, corr


def _clean_name(s: str) -> str | None:
    """Обрезать кавычки/двоеточия и хвостовые метки (ИНН/КПП/Сч/БИК), max 500."""
    s = re.split(r"\s+(?:ИНН|КПП|Сч\.?|БИК|р/с|расч)", s)[0]
    s = s.strip(" :\t")  # кавычки/«ёлочки» НЕ трогаем — часть названия
    return s[:500] if s else None


def _parse_payee_name(text: str, inn: str | None) -> str | None:
    """Имя получателя. Грид платёжки кладёт «Получатель» рядом со «Сч.№», а само имя —
    отдельной строкой; поэтому опираемся на роль (Исполнитель/Поставщик/Продавец), строку
    после «ИНН <inn> КПП» и орг-форму, отсекая «Заказчик/Покупатель» (это плательщик)."""
    # 1) «Исполнитель/Поставщик/Продавец <ОРГ имя>» — продавец = получатель в счёте.
    if (m := _PAYEE_ROLE_RE.search(text)) and (name := _clean_name(m.group(1))):
        return name
    # 2) строка-имя сразу после «ИНН <inn> КПП …» (грид платёжки).
    if inn and (m := re.search(rf"ИНН\s+{re.escape(inn)}\s+КПП[^\n]*\n[ \t]*({_ORG}\b[^\n]+)", text)):
        if name := _clean_name(m.group(1)):
            return name
    # 3) «Получатель: <ОРГ имя>» — только если это имя, а не «Сч.№…».
    if (m := _PAYEE_RE.search(text)) and re.match(rf"\s*{_ORG}\b", m.group(1), re.IGNORECASE):
        if name := _clean_name(m.group(1)):
            return name
    # 4) последний шанс — орг-строка, кроме «Заказчик/Покупатель/Плательщик».
    for m in _ORG_LINE_RE.finditer(text):
        line = text[text.rfind("\n", 0, m.start()) + 1 : m.start()]
        if any(w in line for w in ("Заказчик", "Покупатель", "Плательщик")):
            continue
        if name := _clean_name(m.group(1)):
            return name
    return None


def _parse_contract(text: str) -> str | None:
    """«Договор № X от <дата>» → «по договору № X от <дата>» (основание платежа)."""
    if m := _CONTRACT_RE.search(text):
        num = m.group(1).strip(" .,;№")
        return f"по договору № {num} от {m.group(2)}"
    return None


def _parse_vat(text: str) -> str | None:
    """Строка НДС для назначения: «В т.ч. НДС 20% — 3 850,00 руб.» / «Без НДС»."""
    if m := _VAT_RATE_RE.search(text):
        rate = m.group(1)
        if m.group(2):
            amount = re.sub(r"\s+", " ", m.group(2)).strip()
            return f"В т.ч. НДС {rate}% — {amount} руб."
        return f"В т.ч. НДС {rate}%"
    if _VAT_NONE_RE.search(text):
        return "Без НДС"
    return None


def _parse_purpose(text: str) -> str | None:
    """Назначение из счёта: «Оплата по счёту №… от …» + основание-договор + строка НДС."""
    parts: list[str] = []
    if m := _PURPOSE_RE.search(text):
        ref = re.sub(r"\s+", " ", m.group(1)).strip()
        parts.append(f"Оплата по счёту {ref}")
    if contract := _parse_contract(text):
        parts.append(contract)
    if not parts:
        return None
    base = " ".join(parts)
    if base.startswith("по договору"):  # счёта-якоря не было — оформляем грамотно
        base = "Оплата " + base
    if vat := _parse_vat(text):
        base = f"{base}. {vat}"
    return base[:300]


def extract_requisites_from_text(text: str) -> InvoiceParseResult:
    """Главная логика: вытащить реквизиты получателя из текста счёта (чистая функция).

    Всё опционально: отсутствующее поле → None. Доверие гейтится — р/с surface'ится
    только пройдя контроль-ключ по БИК, БИК — только если есть в справочнике.
    """
    result = InvoiceParseResult()
    found: list[str] = []
    warnings: list[str] = []
    if not text or not text.strip():
        return result

    # ИНН / КПП.
    if m := _INN_RE.search(text):
        digits = m.group(1)
        if len(digits) in (10, 12):  # юрлицо / ИП; иной пробег — не ИНН
            result.payee_inn = digits
            found.append("payee_inn")
    if m := _KPP_RE.search(text):
        result.payee_kpp = m.group(1)
        found.append("payee_kpp")

    # БИК → справочник (заполняет банк + к/с). Вне справочника → в warnings.
    bik: str | None = None
    corr_from_bik: str | None = None
    if m := _BIK_RE.search(text):
        cand = m.group(1)
        if not _valid_bik(cand):  # р/с/к/с, ошибочно принятый за БИК → не сохраняем
            warnings.append("БИК распознан некорректно — проверьте реквизиты банка вручную")
        else:
            bik = cand
            bank = resolve_bank(bik)
            if bank is not None:
                result.payee_bik = bik
                result.payee_bank_name = bank["name"]
                result.payee_corr_account = bank["corr_account"]
                corr_from_bik = bank["corr_account"]
                found += ["payee_bik", "payee_bank_name", "payee_corr_account"]
            else:
                warnings.append(f"БИК {bik} не найден в справочнике — проверьте реквизиты банка вручную")

    # Счета: р/с + к/с.
    rs, corr = _split_accounts(text, corr_from_bik)
    if corr is not None and result.payee_corr_account is None:
        result.payee_corr_account = corr
        found.append("payee_corr_account")

    # Расчётный счёт — только пройдя контроль-ключ по БИК (гейт доверия).
    if rs is not None:
        if bik is not None and _valid_rs(rs, bik):
            result.payee_account = rs
            found.append("payee_account")
        else:
            warnings.append(
                "Расчётный счёт не распознан / не прошёл проверку — введите вручную"
            )

    # Сумма.
    if (amount := _find_amount(text)) is not None:
        result.amount = amount
        found.append("amount")

    # Получатель.
    if (name := _parse_payee_name(text, result.payee_inn)) is not None:
        result.payee_name = name
        found.append("payee_name")

    # Назначение платежа («Оплата по счёту №… от …»).
    if (purpose := _parse_purpose(text)) is not None:
        result.purpose = purpose
        found.append("purpose")

    result.fields_found = found
    result.warnings = warnings
    return result


# ─── LLM-распознавание (текстовый Claude) ───────────────────────────────────────
# Текст из PDF/Word извлекается детерминированно; Claude нужен только чтобы понять
# СМЫСЛ счёта (кто «Продавец»/получатель, кто «Покупатель»/плательщик) — это плохо
# даётся регексам на двухколоночных счетах и счетах самозанятых (НПД). Haiku 4.5 —
# дёшево (задача простая, текстовая). Доверие к р/с гейтится контроль-ключом по БИК.

_LLM_MODEL = "claude-haiku-4-5-20251001"

_LLM_SYSTEM = (
    "Ты извлекаешь платёжные реквизиты ПОЛУЧАТЕЛЯ из текста российского счёта на оплату.\n"
    "ПОЛУЧАТЕЛЬ — это продавец / исполнитель / поставщик (КОМУ платят); его блок содержит "
    "«Банк получателя», «Сч. №», ИНН/КПП получателя.\n"
    "НЕ ПУТАЙ с ПОКУПАТЕЛЕМ / ЗАКАЗЧИКОМ / ПЛАТЕЛЬЩИКОМ (КТО платит) — его реквизиты НЕ нужны.\n"
    "РАСЧЁТНЫЙ счёт получателя (payee_account) — 20 цифр, обычно начинается с 40 (напр. 40802…). "
    "НЕ путай его с КОРРЕСПОНДЕНТСКИМ счётом банка (payee_corr_account) — тот начинается с 301. "
    "На счёте часто два «Сч. №»: к/с (301…) в блоке банка и р/с (40…) в блоке получателя — верни оба.\n"
    "Если поля нет в счёте — верни null. Сумму верни числом с точкой (например 95750.00). "
    "Назначение (purpose) собери в ОДНУ строку: «Оплата по счёту №… от …», затем основание "
    "«по договору №… от …» (если в счёте есть договор-основание), кратко предмет (товар/услуга) "
    "и строку НДС: «В т.ч. НДС 20% — 3 850,00 руб.» (ставку и сумму НДС бери из итогов счёта) "
    "либо «Без НДС», если счёт без НДС / УСН. Пример: «Оплата по счёту № 7690.1 от 25.06.2026 "
    "по договору № КВ/25-082 от 08.04.2025 за услуги по таможенному оформлению. "
    "В т.ч. НДС 20% — 3 850,00 руб.»\n"
    "Вызови инструмент extract_requisites ровно один раз."
)

_LLM_TOOL: dict = {
    "name": "extract_requisites",
    "description": "Записать реквизиты ПОЛУЧАТЕЛЯ платежа, извлечённые из счёта.",
    "input_schema": {
        "type": "object",
        "properties": {
            "payee_name": {"type": ["string", "null"], "description": "Наименование получателя (продавец/исполнитель). НЕ покупатель/заказчик."},
            "payee_inn": {"type": ["string", "null"], "description": "ИНН получателя (10 цифр юрлицо / 12 цифр ИП-самозанятый)."},
            "payee_kpp": {"type": ["string", "null"], "description": "КПП получателя (9 цифр; у ИП/самозанятых отсутствует)."},
            "payee_account": {"type": ["string", "null"], "description": "РАСЧЁТНЫЙ счёт получателя (20 цифр, обычно с 40, напр. 40802…). НЕ корреспондентский (тот с 301)."},
            "payee_corr_account": {"type": ["string", "null"], "description": "Корреспондентский счёт банка (20 цифр, начинается с 301). Это НЕ расчётный счёт получателя."},
            "payee_bik": {"type": ["string", "null"], "description": "БИК банка получателя (9 цифр)."},
            "amount": {"type": ["string", "null"], "description": "Сумма к оплате числом, напр. 95750.00."},
            "purpose": {"type": ["string", "null"], "description": "Назначение платежа: «Оплата по счёту №… от …» + «по договору №… от …» (если есть основание-договор) + кратко предмет + строка НДС («В т.ч. НДС 20% — 3 850,00 руб.» или «Без НДС»). Не длиннее 300 символов."},
        },
        "required": ["payee_name", "payee_inn", "payee_kpp", "payee_account", "payee_corr_account", "payee_bik", "amount", "purpose"],
    },
}

# Скан-PDF может содержать счёт И акт на разных страницах. Тот же инструмент + классификация
# страниц: реквизиты берём со страницы-счёта, акт-страницы фронт сохранит отдельным документом.
_PAGES_TOOL: dict = {
    "name": "extract_invoice_and_pages",
    "description": "Записать реквизиты ПОЛУЧАТЕЛЯ из счёта и тип КАЖДОЙ присланной страницы.",
    "input_schema": {
        "type": "object",
        "properties": {
            **_LLM_TOOL["input_schema"]["properties"],
            "pages": {
                "type": "array",
                "description": "По одному элементу на КАЖДУЮ присланную страницу, СТРОГО в том же порядке.",
                "items": {
                    "type": "object",
                    "properties": {
                        "doc_type": {
                            "type": "string",
                            "enum": ["INVOICE", "ACT", "OTHER"],
                            "description": "Тип страницы: INVOICE — счёт на оплату; ACT — акт/УПД/выполненные работы; OTHER — прочее.",
                        }
                    },
                    "required": ["doc_type"],
                },
            },
        },
        "required": [*_LLM_TOOL["input_schema"]["required"], "pages"],
    },
}

_PAGES_SYSTEM = (
    _LLM_SYSTEM
    + "\nФайл МНОГОСТРАНИЧНЫЙ. Реквизиты бери СО СТРАНИЦЫ-СЧЁТА. Дополнительно в поле pages "
    "верни тип КАЖДОЙ присланной страницы (по порядку): INVOICE — счёт на оплату; "
    "ACT — акт / УПД / акт выполненных работ; OTHER — прочее."
)


def _digits(v: object) -> str | None:
    if not isinstance(v, str):
        return None
    d = re.sub(r"\D", "", v)
    return d or None


def _finalize(payee: dict) -> InvoiceParseResult:
    """Сырые поля (от LLM) → результат с гейтом доверия: р/с surface'ится только пройдя
    контроль-ключ по БИК; БИК добивает банк/к-с из справочника, если он там есть."""
    r = InvoiceParseResult()
    found: list[str] = []
    warnings: list[str] = []

    inn = _digits(payee.get("payee_inn"))
    if inn and len(inn) in (10, 12):
        r.payee_inn = inn
        found.append("payee_inn")
    kpp = _digits(payee.get("payee_kpp"))
    if kpp and len(kpp) == 9:
        r.payee_kpp = kpp
        found.append("payee_kpp")

    bik = _digits(payee.get("payee_bik"))
    if bik and _valid_bik(bik):
        r.payee_bik = bik
        found.append("payee_bik")
        bank = resolve_bank(bik)
        if bank is not None:  # справочник добивает банк + корр.счёт (если БИК известен)
            r.payee_bank_name = bank["name"]
            r.payee_corr_account = bank["corr_account"]
            found += ["payee_bank_name", "payee_corr_account"]
    elif bik:
        warnings.append("БИК распознан некорректно — проверьте реквизиты банка вручную")

    # Счета: на счёте два «Сч. №» (к/с банка 301… + р/с получателя 40…) — модель может их
    # перепутать местами. Разносим по префиксу из ОБОИХ полей (как в текстовом _split_accounts):
    # 301 = корреспондентский, 40… = расчётный; р/с surface'ится только пройдя контроль-ключ.
    cand = [a for a in (_digits(payee.get("payee_account")), _digits(payee.get("payee_corr_account"))) if a and len(a) == 20]
    rs = next((a for a in cand if a.startswith(_RS_PREFIXES) and not a.startswith("301")), None)
    corr = next((a for a in cand if a.startswith("301")), None)
    if corr and r.payee_corr_account is None:  # справочник по БИК мог не дать к/с
        r.payee_corr_account = corr
        found.append("payee_corr_account")
    if rs:
        if r.payee_bik and _valid_rs(rs, r.payee_bik):  # контроль-ключ ЦБ (кросс-проверка р/с+БИК)
            r.payee_account = rs
            found.append("payee_account")
        else:
            warnings.append("Расчётный счёт не прошёл контроль-ключ по БИК — проверьте вручную")
    elif cand:
        warnings.append("Расчётный счёт распознан некорректно — проверьте вручную")

    name = payee.get("payee_name")
    if isinstance(name, str) and name.strip():
        r.payee_name = name.strip()[:500]
        found.append("payee_name")

    amount = _parse_amount(payee["amount"]) if isinstance(payee.get("amount"), str) else None
    if amount is not None and amount > 0:
        r.amount = amount
        found.append("amount")

    purpose = payee.get("purpose")
    if isinstance(purpose, str) and purpose.strip():
        r.purpose = purpose.strip()[:300]
        found.append("purpose")

    r.fields_found = found
    r.warnings = warnings
    return r


async def extract_requisites_llm(text: str) -> InvoiceParseResult | None:
    """Распознать реквизиты текстовым Claude (понимает «Продавец vs Покупатель»).
    None → ключ не настроен или модель не вернула tool_use (тогда сработает regex-fallback)."""
    key = settings.ANTHROPIC_API_KEY
    if not key or not key.isascii():  # пусто или masked-ключ (sync-prod маскирует) → regex-fallback
        return None
    from backend.services.ai.llm_client import chat

    msg = await chat(
        messages=[{"role": "user", "content": text[:20000]}],
        tools=[_LLM_TOOL],
        system=_LLM_SYSTEM,
        model=_LLM_MODEL,
        max_tokens=1024,
        temperature=0,
        tool_choice={"type": "tool", "name": "extract_requisites"},
    )
    block = next((b for b in msg.content if getattr(b, "type", None) == "tool_use"), None)
    data = getattr(block, "input", None)  # ToolUseBlock.input — getattr обходит union-тип content-блоков
    if not isinstance(data, dict):
        return None
    return _finalize(data)


async def extract_requisites_vision(image_data: bytes, media_type: str) -> InvoiceParseResult | None:
    """Распознать реквизиты с ФОТО счёта vision-Claude (тот же tool, что и текстовый путь).
    None → ключ не настроен (для фото regex-fallback невозможен — нет текста)."""
    key = settings.ANTHROPIC_API_KEY
    if not key or not key.isascii():  # пусто или masked-ключ (sync-prod маскирует)
        return None
    import base64

    from backend.services.ai.llm_client import chat

    b64 = base64.standard_b64encode(image_data).decode("ascii")
    msg = await chat(
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                {"type": "text", "text": "Извлеки платёжные реквизиты ПОЛУЧАТЕЛЯ с этого счёта."},
            ],
        }],
        tools=[_LLM_TOOL],
        system=_LLM_SYSTEM,
        model=_LLM_MODEL,
        max_tokens=1024,
        temperature=0,
        tool_choice={"type": "tool", "name": "extract_requisites"},
    )
    block = next((b for b in msg.content if getattr(b, "type", None) == "tool_use"), None)
    data = getattr(block, "input", None)  # ToolUseBlock.input — getattr обходит union-тип content-блоков
    if not isinstance(data, dict):
        return None
    return _finalize(data)


def _norm_doc_type(v: object) -> str:
    """Тип страницы от модели → INVOICE/ACT (OTHER сворачиваем в INVOICE — едет со счётом)."""
    return "ACT" if isinstance(v, str) and v.strip().upper() == "ACT" else "INVOICE"


async def extract_requisites_vision_pages(
    images: list["Image.Image"],
) -> tuple[InvoiceParseResult, list[str]] | None:
    """Скан-PDF (несколько страниц) → реквизиты счёта + тип каждой страницы (для авто-разнесения).
    None → ключ не настроен. Длину pages выравниваем под число страниц (модель могла сбиться)."""
    key = settings.ANTHROPIC_API_KEY
    if not key or not key.isascii():  # пусто или masked-ключ (sync-prod маскирует)
        return None
    import base64

    from backend.services.ai.llm_client import chat

    content: list[dict] = []
    for img in images:
        b64 = base64.standard_b64encode(invoice_split.image_to_jpeg(img)).decode("ascii")
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}})
    content.append({"type": "text", "text": "Извлеки реквизиты ПОЛУЧАТЕЛЯ со страницы-счёта и определи тип каждой страницы."})

    msg = await chat(
        messages=[{"role": "user", "content": content}],
        tools=[_PAGES_TOOL],
        system=_PAGES_SYSTEM,
        model=_LLM_MODEL,
        max_tokens=1024,
        temperature=0,
        tool_choice={"type": "tool", "name": "extract_invoice_and_pages"},
    )
    block = next((b for b in msg.content if getattr(b, "type", None) == "tool_use"), None)
    data = getattr(block, "input", None)  # ToolUseBlock.input — getattr обходит union-тип content-блоков
    if not isinstance(data, dict):
        return None
    result = _finalize(data)
    raw_pages = data.get("pages")
    pages_list = raw_pages if isinstance(raw_pages, list) else []
    types = [_norm_doc_type(p.get("doc_type")) for p in pages_list if isinstance(p, dict)]
    types = (types + ["INVOICE"] * len(images))[: len(images)]  # выравниваем под число страниц
    return result, types


def _is_real_mix(types: list[str]) -> bool:
    """Резать стоит, только если в файле есть И акт, И не-акт (счёт) — иначе один документ."""
    return "ACT" in types and any(t != "ACT" for t in types)


def _build_split_docs(
    data: bytes, types: list[str], filename: str, images: list["Image.Image"] | None
) -> list[ParsedDocument]:
    """Разрезать многостраничный файл по типам страниц в под-PDF (счёт→INVOICE, акт→ACT).

    images=None → текстовый PDF (lossless-резка pypdfium2); иначе скан-PDF (компактный
    растровый PDF из страниц). Любая ошибка резки → []: фронт прикрепит оригинал как было."""
    import base64

    base = (filename or "Документ").rsplit("/", 1)[-1].rsplit(".", 1)[0][:80] or "Документ"
    out: list[ParsedDocument] = []
    for doc_type, label in (("INVOICE", "Счёт"), ("ACT", "Акт")):
        idx = [i for i, t in enumerate(types) if (t == "ACT") == (doc_type == "ACT")]
        if not idx:
            continue
        try:
            if images is None:
                content = invoice_split.split_pdf_lossless(data, idx)
            else:
                content = invoice_split.assemble_pdf_from_images([images[i] for i in idx])
        except Exception:
            logger.warning("invoice split failed (%s) — отдадим оригинал", doc_type, exc_info=True)
            return []
        out.append(ParsedDocument(
            doc_type=doc_type,
            filename=f"{label}_{base}.pdf",
            mime_type="application/pdf",
            content_b64=base64.standard_b64encode(content).decode("ascii"),
        ))
    return out


async def _requisites_from_text(text: str) -> InvoiceParseResult:
    """Реквизиты из текста: текстовый Claude (понимает продавца/покупателя) с regex-fallback."""
    try:
        llm = await extract_requisites_llm(text)
        if llm is not None:
            return llm
    except Exception as e:  # API недоступен/refusal/таймаут — не роняем, идём в regex
        logger.warning("invoice LLM extract failed (%s) — fallback to regex", type(e).__name__)
    return extract_requisites_from_text(text)


async def _parse_pdf_invoice(data: bytes, filename: str) -> InvoiceParseResult:
    """PDF-счёт: текстовый слой → текстовый Claude+regex; скан (без текста) → vision постранично.
    В обоих случаях, если файл = счёт+акт, разносим страницы по типам (`documents`)."""
    # pdfplumber/pypdfium2/Pillow — CPU-bound и синхронные; уводим с event-loop (иначе тяжёлый
    # многостраничный скан блокирует воркер на секунды).
    try:
        pages_text = await asyncio.to_thread(invoice_split.extract_pages_text, data)
    except Exception:
        logger.warning("invoice parse: не удалось прочитать PDF %s", filename, exc_info=True)
        return InvoiceParseResult(warnings=["Не удалось прочитать файл — введите реквизиты вручную"])

    if any(p.strip() for p in pages_text):  # текстовый PDF
        types = [invoice_split.classify_page_text(t) for t in pages_text]
        invoice_text = "\n".join(t for t, ty in zip(pages_text, types) if ty != "ACT").strip()
        result = await _requisites_from_text(invoice_text or "\n".join(pages_text))
        if _is_real_mix(types):
            result.documents = await asyncio.to_thread(_build_split_docs, data, types, filename, None)
        return result

    # Скан-PDF (нет текстового слоя) → растеризуем и распознаём vision-Claude.
    try:
        images = await asyncio.to_thread(invoice_split.rasterize_pages, data)
    except Exception:
        logger.warning("invoice parse: не удалось растеризовать PDF %s", filename, exc_info=True)
        images = []
    if not images:
        return InvoiceParseResult(warnings=["Не удалось прочитать PDF — введите реквизиты вручную"])
    try:
        parsed = await extract_requisites_vision_pages(images)
    except Exception as e:  # API недоступен/refusal/таймаут — не роняем
        logger.warning("invoice vision(pages) failed (%s)", type(e).__name__)
        return InvoiceParseResult(warnings=["Не удалось распознать PDF-скан — введите реквизиты вручную"])
    if parsed is None:
        return InvoiceParseResult(warnings=["Распознавание PDF-скана недоступно — введите реквизиты вручную или приложите фото"])
    result, types = parsed
    if _is_real_mix(types):
        result.documents = await asyncio.to_thread(_build_split_docs, data, types, filename, images)
    return result


async def _parse_image_invoice(data: bytes, media_type: str) -> InvoiceParseResult:
    """Фото счёта → реквизиты через vision. HEIC сперва конвертим в JPEG. Без ключа/ошибки —
    мягкое предупреждение (regex по картинке невозможен), 500 не роняем."""
    if media_type in ("image/heic", "image/heif"):
        try:
            data, media_type = _convert_heic_to_jpeg(data), "image/jpeg"
        except Exception:
            logger.warning("invoice parse: не удалось конвертировать HEIC", exc_info=True)
            return InvoiceParseResult(warnings=["Не удалось обработать HEIC — приложите фото в JPEG/PNG или PDF"])
    try:
        vision = await extract_requisites_vision(data, media_type)
    except Exception as e:  # API недоступен/refusal/таймаут — не роняем
        logger.warning("invoice vision extract failed (%s)", type(e).__name__)
        return InvoiceParseResult(warnings=["Не удалось распознать фото — введите реквизиты вручную"])
    if vision is None:
        return InvoiceParseResult(warnings=["Распознавание фото недоступно — введите реквизиты вручную или приложите PDF"])
    return vision


async def parse_invoice_async(data: bytes, filename: str) -> InvoiceParseResult:
    """Файл счёта → реквизиты (+ авто-разнесение счёт↔акт для многостраничного PDF).

    Фото (jpg/png/webp/heic) → vision-Claude; PDF → текстовый слой (Claude+regex) либо vision
    по скану (без текста); Word → текстовый Claude+regex. Битый файл → не 500."""
    media_type = _image_media_type(data, filename)
    if media_type is not None:  # фото счёта — текста нет, идём vision-путём
        return await _parse_image_invoice(data, media_type)

    if (filename or "").lower().endswith(".pdf") or data[:4] == _PDF_MAGIC:
        return await _parse_pdf_invoice(data, filename)

    # Word / прочее: текстовый разбор (постраничной резки нет — у .docx нет страниц как у PDF).
    try:
        text = _extract_text(data, filename)
    except Exception:
        logger.warning("invoice parse: не удалось извлечь текст из %s", filename, exc_info=True)
        return InvoiceParseResult(warnings=["Не удалось прочитать файл — введите реквизиты вручную"])
    if not text or not text.strip():
        return InvoiceParseResult(warnings=["Файл пуст или текст не распознан — введите реквизиты вручную"])
    return await _requisites_from_text(text)


def parse_invoice(data: bytes, filename: str) -> InvoiceParseResult:
    """Синхронный regex-only разбор (fallback / юнит-тесты). Эндпоинт использует parse_invoice_async."""
    try:
        text = _extract_text(data, filename)
    except Exception:
        logger.warning("invoice parse: не удалось извлечь текст из %s", filename, exc_info=True)
        return InvoiceParseResult(warnings=["Не удалось прочитать файл — введите реквизиты вручную"])
    return extract_requisites_from_text(text)
