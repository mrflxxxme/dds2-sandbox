# ruff: noqa: RUF002, RUF003
"""
Counterparty requisites enrichment from the bank statement.

Хук в ETL (`persist_df`): после загрузки выписки тянет банковские реквизиты
перевозчиков/контрагентов в справочник:
  • bank_account (р/с) — самый частый счёт получателя из ИСХОДЯЩИХ транзакций по ИНН;
  • bik/kpp/bank_name — из сырых ops Faktura API (в transactions их нет — парсер
    исторически выбрасывал `bank`), передаются опционально только на Faktura-синке;
  • тег CARRIER — контрагентам с отгрузками (`outbound_shipments`), у кого тип OTHER.

Заполняет ТОЛЬКО пустые поля (ручные правки в карточке не перетираются).
"""

import logging

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger("dds.etl")


def enrich_counterparty_requisites(
    db: Session, project_id: int, ops_requisites: dict[str, dict] | None = None
) -> None:
    """Fill counterparty banking requisites from the statement + tag carriers with shipments."""
    # 0. Создать карточки для ИНН из выписки, у кого их ещё нет (идемпотентно — как
    #    _upsert_counterparties_from_df на импорте, но ретроактивно для уже залитых txn).
    db.execute(
        text(
            """
            INSERT INTO counterparty (
                project_id, inn, name, primary_type, created_by_import,
                is_deleted, created_at, updated_at
            )
            SELECT :pid, t.inn,
                   COALESCE((array_agg(t.counterparty ORDER BY length(COALESCE(t.counterparty, '')) DESC))[1], t.inn),
                   'OTHER', TRUE, FALSE, NOW(), NOW()
            FROM transactions t
            WHERE t.project_id = :pid AND t.is_deleted = false AND t.inn IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM counterparty c
                  WHERE c.project_id = :pid AND c.inn = t.inn AND c.is_deleted = false
              )
            GROUP BY t.inn
            ON CONFLICT (project_id, inn) WHERE inn IS NOT NULL AND is_deleted = false DO NOTHING
            """
        ),
        {"pid": project_id},
    )

    # 1. bank_account (р/с) — most frequent payee account among OUTGOING txns per INN.
    db.execute(
        text(
            """
            WITH ranked AS (
                SELECT t.inn, t.counterparty_account,
                       ROW_NUMBER() OVER (
                           PARTITION BY t.inn ORDER BY COUNT(*) DESC, MAX(t.date) DESC
                       ) AS rn
                FROM transactions t
                WHERE t.project_id = :pid AND t.is_deleted = false
                  AND t.expense > 0
                  AND t.inn IS NOT NULL
                  AND t.counterparty_account ~ '^[0-9]{20}$'
                GROUP BY t.inn, t.counterparty_account
            )
            UPDATE counterparty cp
            SET bank_account = ranked.counterparty_account, updated_at = NOW()
            FROM ranked
            WHERE ranked.rn = 1
              AND cp.project_id = :pid AND cp.is_deleted = false
              AND cp.inn = ranked.inn
              AND (cp.bank_account IS NULL OR cp.bank_account = '')
            """
        ),
        {"pid": project_id},
    )

    # 2. bik/kpp/bank_name from Faktura raw ops (only the API sync path has them).
    for inn, req in (ops_requisites or {}).items():
        db.execute(
            text(
                """
                UPDATE counterparty cp SET
                    bik = COALESCE(NULLIF(cp.bik, ''), :bik),
                    kpp = COALESCE(NULLIF(cp.kpp, ''), :kpp),
                    bank_name = COALESCE(NULLIF(cp.bank_name, ''), :bank_name),
                    bank_account = COALESCE(NULLIF(cp.bank_account, ''), :account),
                    updated_at = NOW()
                WHERE cp.project_id = :pid AND cp.is_deleted = false AND cp.inn = :inn
                """
            ),
            {
                "pid": project_id, "inn": inn,
                "bik": req.get("bik"), "kpp": req.get("kpp"),
                "bank_name": req.get("bank_name"), "account": req.get("account"),
            },
        )

    # 3. Tag OTHER counterparties that have outbound shipments as CARRIER.
    db.execute(
        text(
            """
            UPDATE counterparty cp SET primary_type = 'CARRIER', updated_at = NOW()
            WHERE cp.project_id = :pid AND cp.is_deleted = false AND cp.primary_type = 'OTHER'
              AND EXISTS (
                  SELECT 1 FROM outbound_shipments os
                  WHERE os.counterparty_id = cp.id AND os.is_deleted = false
              )
            """
        ),
        {"pid": project_id},
    )
    db.flush()
