# ruff: noqa: RUF001, RUF002, RUF003
"""БИК → корр. счёт банка получателя (справочник для авто-заполнения реквизитов).

Корр. счёт банка НЕ выводится формулой из БИК — он берётся из справочника ЦБ РФ.
Здесь — выверенный набор крупных банков РФ (покрывает подавляющее большинство
перевозчиков). Для БИК вне набора фронт даёт ввести к/с вручную (он сохраняется на
контрагенте). Дополнительная страховка: банк (Faktura /payments/validate) проверяет
к/с перед созданием черновика, так что неверное авто-значение не уйдёт в платёжку.

Полный справочник ЦБ (ED807) — отдельным заходом (loader + таблица).
"""

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# БИК → {corr_account, name}. Корр. счёт сверен (последние 3 цифры = последние 3 БИК).
_BANKS: dict[str, dict[str, str]] = {
    "044525225": {"corr_account": "30101810400000000225", "name": "ПАО Сбербанк"},
    "044525187": {"corr_account": "30101810700000000187", "name": "Банк ВТБ (ПАО)"},
    "044525593": {"corr_account": "30101810200000000593", "name": "АО «Альфа-Банк»"},
    "044525974": {"corr_account": "30101810145250000974", "name": "АО «ТБанк»"},
    "044525104": {"corr_account": "30101810745374525104", "name": "ООО «Банк Точка»"},
    "044525700": {"corr_account": "30101810200000000700", "name": "АО «Райффайзенбанк»"},
    "044525823": {"corr_account": "30101810200000000823", "name": "«Газпромбанк» (АО)"},
    "044525985": {"corr_account": "30101810300000000985", "name": "ПАО Банк «ФК Открытие»"},
    "044525555": {"corr_account": "30101810400000000555", "name": "ПАО «Промсвязьбанк»"},
}

_BIC_RE = re.compile(r"^\d{9}$")
_CORR_RE = re.compile(r"^30101810\d{12}$")


def resolve_bank(bic: str | None) -> dict[str, str] | None:
    """Вернуть {bic, corr_account, name} по БИК из справочника, либо None.

    Запись отдаётся только если к/с структурно валиден (20 цифр, префикс 30101810,
    последние 3 цифры совпадают с последними 3 БИК) — отсекает опечатки в наборе.
    """
    bic = (bic or "").strip()
    if not _BIC_RE.match(bic):
        return None
    bank = _BANKS.get(bic)
    if bank is None:
        return None
    corr = bank["corr_account"]
    if not _CORR_RE.match(corr) or corr[-3:] != bic[-3:]:
        return None
    return {"bic": bic, "corr_account": corr, "name": bank["name"]}


async def resolve_bank_db(db: "AsyncSession", bic: str | None) -> dict[str, str] | None:
    """БИК → банк из таблицы `cbr_bic` (полный справочник ЦБ), фолбэк на зашитый набор.

    Авторитетный резолвер для платёжки и авто-заполнения формы: покрывает ЛЮБОЙ банк РФ,
    как только заполнен справочник (джоб `cbr_bic_sync`). Если таблица пуста / банка нет /
    к/с структурно невалиден — откат на `resolve_bank` (зашитые крупные банки), чтобы платежи
    в основные банки работали даже без синка. Структурная проверка к/с — как в resolve_bank.
    """
    bic = (bic or "").strip()
    if not _BIC_RE.match(bic):
        return None
    from backend.models.refs import CbrBic

    row = await db.get(CbrBic, bic)
    if row is not None:
        corr = row.corr_account or ""
        if _CORR_RE.match(corr) and corr[-3:] == bic[-3:]:
            return {"bic": bic, "corr_account": corr, "name": row.name or ""}
    return resolve_bank(bic)
