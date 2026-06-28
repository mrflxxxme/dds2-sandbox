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

import html
import io
import logging
import re
import zipfile
from decimal import Decimal, InvalidOperation

from backend.config import settings
from backend.schemas.payment_request import InvoiceParseResult
from backend.services.bank_directory import resolve_bank

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


def _split_accounts(text: str, corr_from_bik: str | None) -> tuple[str | None, str | None]:
    """Разнести все 20-значные счёта на (расчётный, корреспондентский).

    к/с — начинается с «301» (или совпал со справочным corr по БИК); р/с — первый
    не-301 с банковским префиксом (40/30 кроме 301/42/47).
    """
    accounts = _ACC20_RE.findall(text)
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


def _parse_purpose(text: str) -> str | None:
    """«Счёт на оплату №X от <дата>» → «Оплата по счёту №X от <дата>»."""
    if m := _PURPOSE_RE.search(text):
        ref = re.sub(r"\s+", " ", m.group(1)).strip()
        return f"Оплата по счёту {ref}"[:300]
    return None


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
        bik = m.group(1)
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
    "Если поля нет в счёте — верни null. Сумму верни числом с точкой (например 95750.00). "
    "Назначение — кратко из «Счёт на оплату №… от …» и предмета (товар/услуга).\n"
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
            "payee_account": {"type": ["string", "null"], "description": "Расчётный счёт получателя (20 цифр)."},
            "payee_bik": {"type": ["string", "null"], "description": "БИК банка получателя (9 цифр)."},
            "amount": {"type": ["string", "null"], "description": "Сумма к оплате числом, напр. 95750.00."},
            "purpose": {"type": ["string", "null"], "description": "Назначение платежа."},
        },
        "required": ["payee_name", "payee_inn", "payee_kpp", "payee_account", "payee_bik", "amount", "purpose"],
    },
}


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
    if bik and len(bik) == 9:
        r.payee_bik = bik
        found.append("payee_bik")
        bank = resolve_bank(bik)
        if bank is not None:  # справочник добивает банк + корр.счёт (если БИК известен)
            r.payee_bank_name = bank["name"]
            r.payee_corr_account = bank["corr_account"]
            found += ["payee_bank_name", "payee_corr_account"]
    elif bik:
        warnings.append("БИК распознан некорректно — проверьте реквизиты банка вручную")

    acc = _digits(payee.get("payee_account"))
    if acc and len(acc) == 20:
        if r.payee_bik and _valid_rs(acc, r.payee_bik):  # контроль-ключ ЦБ (кросс-проверка р/с+БИК)
            r.payee_account = acc
            found.append("payee_account")
        else:
            warnings.append("Расчётный счёт не прошёл контроль-ключ по БИК — проверьте вручную")
    elif acc:
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
    """Файл счёта → реквизиты. Фото (jpg/png/webp/heic) → vision-Claude; PDF/Word → текстовый
    Claude (надёжно на любых форматах) с regex-fallback. Битый файл → не 500."""
    media_type = _image_media_type(data, filename)
    if media_type is not None:  # фото счёта — текста нет, идём vision-путём
        return await _parse_image_invoice(data, media_type)
    try:
        text = _extract_text(data, filename)
    except Exception:
        logger.warning("invoice parse: не удалось извлечь текст из %s", filename, exc_info=True)
        return InvoiceParseResult(warnings=["Не удалось прочитать файл — введите реквизиты вручную"])
    if not text or not text.strip():
        return InvoiceParseResult(warnings=["Файл пуст или текст не распознан — введите реквизиты вручную"])
    try:
        llm = await extract_requisites_llm(text)
        if llm is not None:
            return llm
    except Exception as e:  # API недоступен/refusal/таймаут — не роняем, идём в regex
        logger.warning("invoice LLM extract failed (%s) — fallback to regex", type(e).__name__)
    return extract_requisites_from_text(text)


def parse_invoice(data: bytes, filename: str) -> InvoiceParseResult:
    """Синхронный regex-only разбор (fallback / юнит-тесты). Эндпоинт использует parse_invoice_async."""
    try:
        text = _extract_text(data, filename)
    except Exception:
        logger.warning("invoice parse: не удалось извлечь текст из %s", filename, exc_info=True)
        return InvoiceParseResult(warnings=["Не удалось прочитать файл — введите реквизиты вручную"])
    return extract_requisites_from_text(text)
