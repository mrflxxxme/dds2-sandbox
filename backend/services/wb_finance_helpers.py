"""
Helpers for wb_finance row enrichment.

WB API возвращает строки-удержания за отзывы (`bonus_type_name LIKE 'Списание за отзыв%'`)
с пустыми товарными полями: `nm_id=0`, `sa_name=''`, `brand_name=''`, `subject_name=''`.
Идентификатор товара зашит ТОЛЬКО внутри текста `bonus_type_name`:

    "Списание за отзыв XS6uAH...: акция №1777436, товар 742354645"

Эти функции:
1. Парсят nm_id из текста (`parse_review_target`)
2. Собирают уникальные nm_id из пачки (`collect_review_nm_ids`)
3. Обогащают строки готовым lookup-словарём (`enrich_review_rows`)

Lookup словарь обычно строится поверх `wb_finance_rows` — берём DISTINCT ON (nm_id)
последние известные `(brand_name, subject_name, sa_name)` среди продажных строк
того же товара. Это самосогласованно с остальными агрегациями (BDR, OPIU, Cost-DNA).
"""

import re

# Жёстко привязываем регэксп к префиксу "Списание за отзыв" — другие типы удержаний
# (реклама, кредит, подписка «Джем») идентификатор товара в тексте не содержат.
_REVIEW_TARGET_RE = re.compile(r"^Списание за отзыв .*?товар (\d+)")


def parse_review_target(bonus_type_name: str | None) -> int | None:
    """Извлечь nm_id товара из текста списания за отзыв.

    Возвращает int nm_id или None, если строка не подпадает под формат.
    Защищаемся от мусора: nm_id == 0 → None.
    """
    if not bonus_type_name:
        return None
    m = _REVIEW_TARGET_RE.match(bonus_type_name)
    if not m:
        return None
    nm_id = int(m.group(1))
    if nm_id == 0:
        return None
    return nm_id


def collect_review_nm_ids(rows: list[dict]) -> set[int]:
    """Собрать уникальные nm_id, которые надо подтянуть из lookup перед обогащением."""
    nm_ids: set[int] = set()
    for row in rows:
        nm_id = parse_review_target(row.get("bonus_type_name"))
        if nm_id is not None:
            nm_ids.add(nm_id)
    return nm_ids


def enrich_review_rows(rows: list[dict], lookup: dict[int, dict]) -> list[dict]:
    """Обогатить строки-отзывы товарными полями.

    Args:
        rows: список словарей строк (как из `_row_to_values`).
        lookup: {nm_id: {"brand_name": ..., "subject_name": ..., "sa_name": ...}}.
            Обычно строится через DISTINCT ON (nm_id) поверх wb_finance_rows.

    Правила:
    - Только строки с непустым `parse_review_target(bonus_type_name)` обогащаются.
    - Если `nm_id` в строке уже != 0 — НЕ переписываем (это валидная строка-продажа).
    - Если nm_id извлечён, но в lookup нет — ставим только nm_id, поля остаются пустыми.
    - Возвращает тот же список (мутация in-place).
    """
    for row in rows:
        if (row.get("nm_id") or 0) != 0:
            continue
        nm_id = parse_review_target(row.get("bonus_type_name"))
        if nm_id is None:
            continue
        row["nm_id"] = nm_id
        info = lookup.get(nm_id)
        if info is None:
            continue
        # Оставляем уже непустые значения, если вдруг пришли — но обычно у отзывов всё пустое
        if not row.get("brand_name"):
            row["brand_name"] = info.get("brand_name", "") or ""
        if not row.get("subject_name"):
            row["subject_name"] = info.get("subject_name", "") or ""
        if not row.get("sa_name"):
            row["sa_name"] = info.get("sa_name", "") or ""
    return rows
