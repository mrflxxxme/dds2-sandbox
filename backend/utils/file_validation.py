"""
File upload validation: magic bytes + size check.
Zero-dependency approach — no python-magic required.
"""

import os
from fastapi import HTTPException


# File signatures (magic bytes) per extension
FILE_SIGNATURES: dict[str, list[bytes]] = {
    ".xlsx": [b"PK\x03\x04"],                    # ZIP (Office Open XML)
    ".xls": [
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",  # OLE2 Compound Document (real .xls)
        b"<html",                              # HTML table saved as .xls (bank exports)
        b"<HTML",
        b"<!DOCTYPE",
        b"<?xml",                              # SpreadsheetML / XML saved as .xls
        b"\x09\x08",                           # BIFF5/BIFF8 BOF record
        b"PK\x03\x04",                         # Actually .xlsx renamed to .xls
    ],
    ".pdf": [b"%PDF"],                            # PDF
    ".csv": [],                                   # Text format — no magic bytes
    # Изображения (фото счёта). HEIC намеренно НЕ перечислен: его «ftyp…» лежит
    # на offset 4, а здесь проверяется только префикс → валидируется при открытии
    # через pillow-heif в invoice_parser.
    ".jpg": [b"\xff\xd8\xff"],                    # JPEG
    ".jpeg": [b"\xff\xd8\xff"],
    ".png": [b"\x89PNG\r\n\x1a\n"],               # PNG
    ".webp": [b"RIFF"],                           # WebP — RIFF-контейнер (WEBP на offset 8)
}


def validate_file_content(data: bytes, filename: str) -> None:
    """
    Validate that file content matches the declared extension.
    Raises HTTPException(400) if content doesn't match.

    Args:
        data: raw file bytes
        filename: original filename (used to extract extension)
    """
    if not data:
        raise HTTPException(400, "Файл пуст")

    ext = os.path.splitext(filename or "")[1].lower()

    if ext not in FILE_SIGNATURES:
        # Unknown extension — skip magic validation, rely on extension check
        return

    signatures = FILE_SIGNATURES[ext]

    if not signatures:
        # No magic bytes to check (e.g. .csv) — validate it's valid text
        if ext == ".csv":
            _validate_text_file(data)
        return

    # Check if file starts with any of the expected signatures
    # Strip BOM and leading whitespace for text-like formats (.xls can be HTML)
    header = data[:64]
    header_stripped = header.lstrip(b"\xef\xbb\xbf\xff\xfe \t\r\n")
    for sig in signatures:
        if header[:len(sig)] == sig or header_stripped[:len(sig)] == sig:
            return

    # None of the signatures matched
    expected = ", ".join(repr(s) for s in signatures)
    raise HTTPException(
        400,
        f"Содержимое файла не соответствует расширению {ext}. "
        f"Файл может быть повреждён или иметь неверное расширение.",
    )


def _validate_text_file(data: bytes) -> None:
    """Check that file content is valid text (UTF-8 or CP1251)."""
    try:
        data[:4096].decode("utf-8")
        return
    except UnicodeDecodeError:
        pass

    try:
        data[:4096].decode("cp1251")
        return
    except UnicodeDecodeError:
        pass

    raise HTTPException(
        400,
        "CSV файл содержит невалидную кодировку. Ожидается UTF-8 или CP1251.",
    )
