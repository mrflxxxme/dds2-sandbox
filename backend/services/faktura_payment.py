# ruff: noqa: RUF002, RUF003
"""
Faktura payment-draft creation — «Создать оплату» для заявки на оплату.

РЕАЛЬНАЯ запись в ВБ Банк (необратимо). Платёжка строится ПО ПРОВЕРЕННОМУ контракту
``POST /payments/validate`` (scripts/faktura_validate_probe.py — вернул ``errors:[]``
на проде): payee*AccountNumber/BankBic/BankAccount + payerAccountId (внутренний id
счёта из /accounts, НЕ номер) + payerName/Kpp + queue/uip/urgent/payouts/income.

Идемпотентность: ``bank_guid`` (uuid4) КОММИТИТСЯ на заявке ДО вызова /payments/save,
поэтому таймаут/краш после создания черновика в банке не плодит новый guid → дубль.

⚠ ``/payments/save`` ещё НЕ гонялся живьём ни разу (только /validate). До включения
для операторов — тест на 1₽ через validate→save (роль fingerprint на /save не ясна).
Гейт: вызывать только при confirm=true.
"""

import logging
import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cache import invalidate_cache
from backend.integrations.faktura_api import DEFAULT_HOST, FakturaApiClient
from backend.models.payment_request import PaymentRequest, PaymentRequestStatus
from backend.services import payment_request_status as prs
from backend.services.bank_directory import resolve_bank_db
from backend.services.faktura_service import _get_faktura_key
from backend.utils.time import utcnow

logger = logging.getLogger("dds.payment_request")


class PaymentDraftError(Exception):
    """Bank-side validation rejected the payment. Carries the list of error messages."""

    def __init__(self, bank_errors: list[str]):
        self.bank_errors = bank_errors
        super().__init__("; ".join(bank_errors) or "payment validation failed")


# Назначение платежа (поле 24) — ограниченный набор символов (ЦБ 762-П). Банк (Faktura) отбивает
# типографику: длинное/короткое тире «—/–», «ёлочки»/«кавычки», неразрывный пробел, «₽», «…».
# Заменяем на ASCII-аналоги, всё вне набора — убираем. «№», «%», «,», «.», кавычки, скобки — ok.
_PURPOSE_MAP = str.maketrans({
    "—": "-", "–": "-", "−": "-", "‒": "-", "―": "-", "•": "-", "·": "-",
    "«": '"', "»": '"', "„": '"', "“": '"', "”": '"', "‟": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "`": "'", "´": "'",
    " ": " ", " ": " ", " ": " ", "\t": " ", "\r": " ", "\n": " ",
    "…": "...", "₽": "р.",
})
_PURPOSE_ALLOWED_RE = re.compile(r"[^0-9A-Za-zА-Яа-яЁё №%.,:;()\-+/=*!?#\"'& ]")


def _sanitize_purpose(text: str | None) -> str:
    """Назначение → допустимый банком набор символов (ЦБ 762-П): типографику в ASCII, прочее убрать.
    Чинит «Поле "Назначение платежа" содержит недопустимые символы» (длинное тире из распозналки/LLM)."""
    s = (text or "").translate(_PURPOSE_MAP)
    s = _PURPOSE_ALLOWED_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _build_payment_body(pr: PaymentRequest, payer: dict, guid: str, *, corr_account: str, fingerprint: str = "", nds: str = "-1") -> dict:
    """Платёжка по проверенному /payments/validate контракту (см. faktura_validate_probe.py).

    payer — объект счёта из /accounts: {id, number, currency, owner:{name,inn,kpp},
    bank:{bic,correspondentAccountNumber}}. Банк ждёт payerAccountId = внутренний id.
    """
    owner = payer.get("owner") or {}
    return {
        "amount": float(pr.amount),  # банк ждёт число (проба слала числовой amount)
        "docDate": utcnow().date().isoformat(),
        "docNumber": "".join(c for c in (pr.number or "") if c.isdigit()) or str(pr.id),
        "fingerprint": fingerprint,
        "guid": guid,
        "payoutsCode": "",
        "incomeTypeCode": "",
        "nds": nds,
        "payeeAccountNumber": pr.payee_account or "",
        "payeeBankBic": pr.payee_bik or "",
        "payeeBankAccount": corr_account,  # корр. счёт банка получателя (резолв по БИК из справочника ЦБ)
        "payeeName": pr.payee_name or "",
        "payeeInn": pr.payee_inn or "",
        "payeeKpp": pr.payee_kpp or "",
        "payerName": owner.get("name") or "",
        "payerKpp": owner.get("kpp") or "",
        "payerAccountId": payer.get("id"),
        "purpose": _sanitize_purpose(pr.purpose),  # ЦБ-charset: типографику (тире «—» и т.п.) → ASCII
        "queue": 5,  # очерёдность платежа (поле 21)
        "uip": "0",
        "urgent": "false",
    }


def _resolve_payer(accounts: list[dict], payer_account_id: str | None) -> dict:
    """Выбрать счёт-плательщик. При нескольких RUB-счетах и без явного id — НЕ угадываем."""
    if not accounts:
        raise ValueError("У интеграции Faktura нет ни одного счёта-плательщика")
    if payer_account_id:
        for acc in accounts:
            if str(acc.get("id")) == str(payer_account_id) or str(acc.get("number")) == str(payer_account_id):
                return acc
        raise ValueError(f"Счёт-плательщик {payer_account_id} не найден среди счетов интеграции")
    # Faktura отдаёт currency словарём ({"code": "RUR", ...}), код — RUR (не RUB).
    def _cur_code(a: dict) -> str:
        c = a.get("currency")
        return str((c.get("code") if isinstance(c, dict) else c) or "").upper()

    rub = [a for a in accounts if _cur_code(a) in ("RUB", "RUR", "643")] or accounts
    if len(rub) > 1:
        opts = ", ".join(f"{a.get('number')} (id={a.get('id')})" for a in rub)
        raise ValueError(
            "Несколько счетов-плательщиков — укажите конкретный счёт списания "
            f"(payer_account_id в запросе или в config ключа Faktura). Доступны: {opts}"
        )
    return rub[0]


def _extract_doc_id(result: object) -> str | None:
    """Достать id созданного документа из ответа банка на /payments/save.

    Формат ответа /save контрактом НЕ зафиксирован (первый живой запуск показал, что
    id не в id/docId/guid). Пробуем типовые поля и вложенные контейнеры; если не нашли —
    вызывающий логирует ключи ответа, чтобы добить точечно."""
    if not isinstance(result, dict):
        return None
    containers: list[dict] = [result]
    for key in ("data", "document", "result", "doc", "payment"):
        nested = result.get(key)
        if isinstance(nested, dict):
            containers.append(nested)
    for container in containers:
        for field in ("id", "docId", "documentId", "document_id", "doc_id", "number", "guid", "uid"):
            value = container.get(field)
            if value:
                return str(value)
    return None


async def create_payment_draft(
    db: AsyncSession,
    project_id: int,
    pr: PaymentRequest,
    *,
    payer_account_id: str | None = None,
    changed_by: str = "user",
) -> PaymentRequest:
    """Build + validate + save a real payment draft in the bank, then flip status → DRAFT_CREATED."""
    # Анти-double-spend: лочим строку заявки FOR UPDATE и работаем с перечитанным
    # инстансом. Конкурентный запрос/двойной клик дождётся лока и переиспользует ТОТ ЖЕ
    # bank_guid (банк идемпотентен по guid → второго реального черновика не возникнет).
    locked = (
        await db.execute(
            select(PaymentRequest)
            .where(PaymentRequest.id == pr.id, PaymentRequest.project_id == project_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if locked is None:
        raise ValueError("Заявка на оплату не найдена")
    pr = locked
    if pr.status not in (PaymentRequestStatus.PENDING_REVIEW.value, PaymentRequestStatus.APPROVED.value):
        raise ValueError("Создать оплату можно только для заявки на проверке или согласованной")
    if not (pr.payee_inn and pr.payee_account and pr.payee_bik and pr.amount and pr.amount > 0):
        raise ValueError("Неполные реквизиты получателя — нельзя создать платёжку")

    # К/с банка получателя резолвим по БИК из справочника ЦБ (авторитетно; к/с НЕ выводится
    # формулой из БИК). Фолбэк на сохранённый к/с заявки. Пусто на обоих → понятная ошибка
    # вместо отбойника банка «Неверно указан к/с».
    bank = await resolve_bank_db(db, pr.payee_bik)
    corr_account = (bank["corr_account"] if bank else None) or pr.payee_corr_account or ""
    if not corr_account:
        raise ValueError(
            f"Не удалось определить к/с банка по БИК {pr.payee_bik} — заполните к/с получателя вручную (в счёте указан)"
        )

    # Credentials (raises ValueError with a clear message if the Faktura key is absent).
    key, login, password = await _get_faktura_key(db, project_id)
    cfg = key.config or {}
    host = cfg.get("host") or DEFAULT_HOST
    # Счёт-плательщик: явный аргумент → config ключа → авто (только если RUB-счёт один).
    payer_account_id = payer_account_id or cfg.get("payer_account_id")

    guid = pr.bank_guid or str(uuid4())

    async with FakturaApiClient(login, password, host) as client:
        await client.authenticate()
        accounts = await client.get_accounts()
        payer = _resolve_payer(accounts, payer_account_id)
        payment = _build_payment_body(pr, payer, guid, corr_account=corr_account)

        errors = await client.validate_payment(payment)
        if errors:
            messages = [str(e.get("message") or e) if isinstance(e, dict) else str(e) for e in errors]
            raise PaymentDraftError(messages)

        # Идемпотентность: КОММИТИМ guid ДО необратимого save (expire_on_commit=False —
        # атрибуты pr остаются доступны). Краш/таймаут после save → ретрай тем же guid.
        if pr.bank_guid != guid:
            pr.bank_guid = guid
            await db.commit()

        result = await client.save_payment(payment)
        pr.bank_doc_id = _extract_doc_id(result)
        if pr.bank_doc_id is None:
            # id документа не нашёлся в ожидаемых полях — логируем ТОЛЬКО ключи ответа
            # (без значений → без PII), чтобы по факту добить точное поле.
            keys = sorted(result.keys()) if isinstance(result, dict) else type(result).__name__
            logger.warning("Faktura /payments/save: id документа не найден в ответе; ключи=%s", keys)
        # НЕ логируем сырой ответ банка — в нём финансовые PII (реквизиты/номера). Только doc id.
        logger.info("Faktura /payments/save ok (pr=%d, doc=%s)", pr.id, pr.bank_doc_id)

    prs.apply_transition(
        db, pr, PaymentRequestStatus.DRAFT_CREATED, changed_by=changed_by,
        comment=f"Создан черновик платёжки в банке (doc={pr.bank_doc_id or '?'})",
    )
    await db.commit()
    await db.refresh(pr)
    await invalidate_cache(f"payment_request_list:project_id={project_id}")
    await invalidate_cache(f"payment_request_detail:project_id={project_id}")
    logger.info("Faktura payment draft created: pr=%d project=%d doc=%s", pr.id, project_id, pr.bank_doc_id)
    return pr
