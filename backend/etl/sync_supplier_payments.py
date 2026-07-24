# ruff: noqa: RUF002, RUF003
"""
ETL sync: атрибуция строк выписки поставщику / торговому дому / логисту (и займу)
по КОНТРАКТУ и счёту контрагента.

Хук в ``persist_df`` ПОСЛЕ ``enrich_counterparty_requisites``: CNY/SWIFT-платежи
показывают контрагентом «БАНК ВТБ (ПАО)» (транзитный счёт), а реальный поставщик
опознаётся только по ``CONTRACT <номер>`` из назначения. Резолвим
``contract_number`` → ``counterparty_id`` через ``counterparty_identifier``
(kind=CONTRACT), **перебивая** ошибочную привязку к банку; плюс заём по
``Loan.contract_number``; плюс счёт контрагента (kind=ACCOUNT).

Инварианты:
  • advisory-lock того же namespace (0x50524D), что у остальных матчеров
    persist_df — сериализация по проекту;
  • атрибуция, а НЕ 1:1-потребление: один контракт → один контрагент, ВСЕ его
    строки получают этот ``counterparty_id`` (это и есть история оплат);
  • гейты: НЕ ``is_internal``, НЕ ``is_fx``, НЕ депозит (event_type2 DEPOSIT_*) —
    конверсии/депозиты/внутренние переводы не атрибутируются поставщику;
  • привязка только по ЯВНО заведённому юзером идентификатору — форвардинг/
    логистика попадёт сюда лишь если её контракт назначен карточке логиста;
  • идемпотентно: ``counterparty_id`` / ``loan_id`` ставятся только при изменении.
"""

import structlog
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from backend.models.counterparty import CounterpartyIdentifier, IdentifierKind
from backend.models.loan import Loan, LoanStatus
from backend.models.transactions import Transaction

logger = structlog.get_logger("dds.etl")

# event_type2, при которых строка НЕ относится к расчётам с поставщиком.
_EXCLUDED_EVENT_TYPES = {
    "INTERNAL_TRANSFER",
    "FX_BUY",
    "DEPOSIT_PLACE",
    "DEPOSIT_RETURN",
    "DEPOSIT_INTEREST",
}


def sync_supplier_payments(db: Session, project_id: int) -> None:
    """Attribute statement rows to counterparty/loan by contract + account."""
    db.execute(text("SELECT pg_advisory_xact_lock(:ns, :pid)"), {"ns": 0x50524D, "pid": project_id})

    # Зарегистрированные идентификаторы контрагентов (contract / account).
    contract_to_cp: dict[str, int] = {}
    account_to_cp: dict[str, int] = {}
    for ident in (
        db.execute(
            select(CounterpartyIdentifier).where(
                CounterpartyIdentifier.project_id == project_id,
                CounterpartyIdentifier.is_deleted == False,  # noqa: E712
            )
        )
        .scalars()
        .all()
    ):
        val = (ident.value or "").strip()
        if not val:
            continue
        if ident.kind == IdentifierKind.CONTRACT.value:
            contract_to_cp.setdefault(val, ident.counterparty_id)
        elif ident.kind == IdentifierKind.ACCOUNT.value:
            account_to_cp.setdefault(val, ident.counterparty_id)

    # Активные займы по contract_number (для иностранных займовых контрактов).
    contract_to_loan: dict[str, tuple[int, int]] = {}
    for loan in (
        db.execute(
            select(Loan).where(
                Loan.project_id == project_id,
                Loan.is_deleted == False,  # noqa: E712
                Loan.status == LoanStatus.ACTIVE.value,
            )
        )
        .scalars()
        .all()
    ):
        cn = (loan.contract_number or "").strip()
        if cn:
            contract_to_loan.setdefault(cn, (loan.id, loan.counterparty_id))

    if not contract_to_cp and not account_to_cp and not contract_to_loan:
        return

    # Только релевантные строки: с контрактом ИЛИ со счётом из зарегистрированных.
    conds = []
    if contract_to_cp or contract_to_loan:
        conds.append(Transaction.contract_number.isnot(None))
    if account_to_cp:
        conds.append(Transaction.counterparty_account.in_(list(account_to_cp.keys())))
    if not conds:
        return

    txns = (
        db.execute(
            select(Transaction).where(
                Transaction.project_id == project_id,
                Transaction.is_deleted == False,  # noqa: E712
                or_(*conds),
            )
        )
        .scalars()
        .all()
    )

    cp_matched = 0
    loan_matched = 0
    for t in txns:
        if t.is_internal or t.is_fx or (t.event_type2 in _EXCLUDED_EVENT_TYPES):
            continue
        contract = (t.contract_number or "").strip()
        account = (t.counterparty_account or "").strip()

        resolved_cp: int | None = None
        if contract and contract in contract_to_loan:
            # Заём по контракту: ставим loan_id и контрагента-займодавца.
            loan_id, loan_cp = contract_to_loan[contract]
            if t.loan_id != loan_id:
                t.loan_id = loan_id
                loan_matched += 1
            resolved_cp = loan_cp
        elif contract and contract in contract_to_cp:
            resolved_cp = contract_to_cp[contract]
        elif account and account in account_to_cp:
            resolved_cp = account_to_cp[account]

        if resolved_cp is not None and t.counterparty_id != resolved_cp:
            # Перебиваем ошибочную INN-привязку к «БАНК ВТБ» на CNY/SWIFT-строках.
            t.counterparty_id = resolved_cp
            cp_matched += 1

    if cp_matched or loan_matched:
        logger.info("supplier_payments.matched", project_id=project_id, cp=cp_matched, loan=loan_matched)
    db.flush()
