# ruff: noqa: RUF001, RUF002, RUF003
"""Постраничные операции над PDF-счётом: распознавание скан-PDF и авто-разнесение счёт↔акт.

- `extract_pages_text` — текст по страницам (текстовый слой; пусто у сканов).
- `rasterize_pages` — рендер страниц в PIL (для vision-распознавания скан-PDF).
- `classify_page_text` — тип страницы по тексту: счёт / акт.
- Разрезание PDF постранично в под-документы:
  * текстовый PDF → `split_pdf_lossless` (pypdfium2) — сохраняем оригинал бит-в-бит;
  * скан-PDF → `assemble_pdf_from_images` — растеризуем в downscaled-страницы и собираем
    компактный PDF (исходные телефонные фото ≈5–8 МБ/стр → ≈0.3 МБ/стр; иначе ответ/хранилище
    раздуваются на порядок — измерено на реальном счёте).

Зависимостей не добавляем: pdfplumber / pypdfium2 / Pillow уже в образе (pypdfium2 — транзитивно
через pdfplumber, явно запинен в requirements ради прямого использования). У pypdfium2 нет
py.typed → точечный import-ignore для mypy.
"""

import io
import logging
import re

from PIL import Image

logger = logging.getLogger("dds.payment_request")

MAX_PDF_PAGES = 10            # счёт/акт — единицы страниц; глубже не разбираем (анти-DoS)
_RASTER_DPI = 150             # рендер скан-страницы
# Анти-pixel-bomb: bitmap = (w·dpi/72)·(h·dpi/72) пикселей; pypdfium2 render→to_pil() аллоцирует
# буфер ПОД РАЗМЕР MediaBox и обходит Pillow DecompressionBomb (frombuffer). Враждебный PDF с
# гигантским MediaBox → OOM. Кап как у HEIC-пути (_MAX_IMAGE_PIXELS в invoice_parser).
_RASTER_MAX_PIXELS = 40_000_000
_STORE_MAX_LONG_PX = 2200     # потолок длинной стороны хранимого под-файла (читаемо, но компактно)
_VISION_MAX_LONG_PX = 1568    # потолок vision-входа (рекомендация Claude; экономит токены/латентность)
_JPEG_QUALITY = 85

# Заголовки документов. «Акт № …» / «Акт выполненных…» / УПД → ACT; «Счёт на оплату» → INVOICE.
_ACT_TITLE_RE = re.compile(
    r"Акт(?:\s+№|\s+N|\s+выполненн|\s+сдачи|\s+оказанн)|Универсальн\w*\s+передаточн|\bУПД\b",
    re.IGNORECASE,
)
_INVOICE_TITLE_RE = re.compile(r"Сч[её]т\s+на\s+оплату", re.IGNORECASE)


def extract_pages_text(data: bytes) -> list[str]:
    """Текст по страницам PDF (≤MAX_PDF_PAGES). Пустой текстовый слой (скан) → пустые строки."""
    import pdfplumber

    out: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            out.append(page.extract_text() or "")
    return out


def classify_page_text(text: str) -> str:
    """Тип страницы по тексту: ACT (акт/УПД) либо INVOICE (счёт/прочее — по умолчанию)."""
    if _ACT_TITLE_RE.search(text) and not _INVOICE_TITLE_RE.search(text):
        return "ACT"
    return "INVOICE"


def _to_rgb_downscaled(img: Image.Image, max_long_px: int) -> Image.Image:
    """RGB-копия с длинной стороной ≤ max_long_px (для хранения/vision; экономит байты)."""
    out = img.convert("RGB")
    if max(out.size) > max_long_px:
        out.thumbnail((max_long_px, max_long_px))
    return out


def rasterize_pages(data: bytes) -> list[Image.Image]:
    """Скан-PDF → список PIL.Image (RGB, длинная сторона ≤_STORE_MAX_LONG_PX, ≤MAX_PDF_PAGES стр.)."""
    import pdfplumber

    out: list[Image.Image] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages[:MAX_PDF_PAGES]:
            # Анти-pixel-bomb: размер bitmap считаем ДО рендера (to_pil() обходит DecompressionBomb).
            px = (page.width / 72 * _RASTER_DPI) * (page.height / 72 * _RASTER_DPI)
            if px > _RASTER_MAX_PIXELS:
                logger.warning("invoice rasterize: страница %.0f×%.0f pt слишком большая — пропуск", page.width, page.height)
                continue
            pil = page.to_image(resolution=_RASTER_DPI).original  # pypdfium2-рендер под капотом
            out.append(_to_rgb_downscaled(pil, _STORE_MAX_LONG_PX))
    return out


def image_to_jpeg(img: Image.Image, max_long_px: int = _VISION_MAX_LONG_PX) -> bytes:
    """PIL.Image → JPEG-байты для vision-входа (доп. downscale + сжатие)."""
    buf = io.BytesIO()
    _to_rgb_downscaled(img, max_long_px).save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


def split_pdf_lossless(data: bytes, page_indices: list[int]) -> bytes:
    """Под-PDF с заданными страницами оригинала (lossless: текстовый/вектор слой сохраняется)."""
    import pypdfium2 as pdfium  # type: ignore[import-untyped]  # нет py.typed

    src = pdfium.PdfDocument(data)
    try:
        valid = [i for i in page_indices if 0 <= i < len(src)]
        dst = pdfium.PdfDocument.new()
        dst.import_pages(src, valid)
        buf = io.BytesIO()
        dst.save(buf)
        return buf.getvalue()
    finally:
        src.close()


def assemble_pdf_from_images(images: list[Image.Image]) -> bytes:
    """Список PIL.Image → один многостраничный PDF (Pillow, FLATE). Для скан-страниц.

    FLATE (дефолт Pillow) на счёте/акте — мостовая заливка + текст — жмёт сильнее JPEG
    (≈0.3 МБ/стр против ≈0.4 МБ JPEG и ≈5–8 МБ lossless-оригинала). Вход ожидается уже
    подготовленным `rasterize_pages` (RGB, ≤_STORE_MAX_LONG_PX) — повторно не downscale'им
    (лишняя копия каждой страницы = ненужный пик памяти), только страхуем режим RGB."""
    if not images:
        raise ValueError("no images to assemble")
    pages = [im if im.mode == "RGB" else im.convert("RGB") for im in images]
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:])
    return buf.getvalue()
