# ruff: noqa: RUF002, RUF003
"""Статусы переезда — полное зеркало заявки на сборку

Канон юзера 31.07.2026: переезд между складами ФФ ведётся как заявка на сборку —
те же ступени, те же сигналы синка провайдера, машина внутри той же цепочки.

Тонкая шкала DRAFT / IN_TRANSIT / COMPLETED заменяется на
PENDING → IN_PROGRESS → READY → VEHICLE_ASSIGNED → SHIPPED → DELIVERED
(+ RETURNED → CLOSED, CANCELLED).

Что делает миграция:
  1. Вехи цепочки: `actual_ready_date` («ФФ собрал») и `shipped_at` («уехал») —
     их требует сводный список логиста, где переезды идут вперемешку с заявками
     и обязаны заполнять те же колонки. Из статуса не вывести: он хранит только
     ТЕКУЩЕЕ состояние.
  2. Журнал `stock_transfer_status_history` — один в один AssemblyStatusHistory.
     Статус двигают трое (человек, синк ФФ, авто-переходы), и без журнала разбор
     «почему сток уехал» упирался бы в пустоту.
  3. `server_default` статуса: 'DRAFT' → 'PENDING'. Старого значения в шкале
     больше нет, и любой INSERT мимо ORM положил бы невалидный статус.
  4. Перевод существующих строк.

Перевод:
    DRAFT + vehicle_assigned_at → VEHICLE_ASSIGNED
    DRAFT                       → PENDING
    IN_TRANSIT                  → SHIPPED
    COMPLETED                   → DELIVERED

Порядок расщепления DRAFT критичен. У старой шкалы не было ступени «машина
назначена»: переезд с заполненным `vehicle_assigned_at` ждал отправки, оставаясь
DRAFT. Схлопни его в PENDING — и он станет НЕОТПРАВЛЯЕМЫМ (`send_transfer`
пускает только READY|VEHICLE_ASSIGNED), а логисту пришлось бы переназначать
машину руками по каждому.

Соответствие смысловое, а не косметическое: движения стока привязаны к тем же
переходам (→ SHIPPED списывает, → DELIVERED приходует). Ни одной строки движений
эта миграция не трогает.

Обратный перевод полный: ступени, которых в старой шкале не было
(IN_PROGRESS / READY / VEHICLE_ASSIGNED), схлопываются в DRAFT — все они «до
отгрузки», сток не двигали; RETURNED / CLOSED → COMPLETED.

Revision ID: trv04_transfer_status
Revises: trv03_transfer_invariants
Create Date: 2026-07-31 18:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "trv04_transfer_status"
down_revision: str | None = "trv03_transfer_invariants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FORWARD = {"IN_TRANSIT": "SHIPPED", "COMPLETED": "DELIVERED"}
_BACK = {
    "PENDING": "DRAFT",
    "IN_PROGRESS": "DRAFT",
    "READY": "DRAFT",
    "VEHICLE_ASSIGNED": "DRAFT",
    "SHIPPED": "IN_TRANSIT",
    "DELIVERED": "COMPLETED",
    "RETURNED": "COMPLETED",
    "CLOSED": "COMPLETED",
    "CANCELLED": "DRAFT",
}


def _remap(mapping: dict[str, str]) -> None:
    conn = op.get_bind()
    for old, new in mapping.items():
        conn.execute(
            text("UPDATE stock_transfers SET status = :new WHERE status = :old"),
            {"new": new, "old": old},
        )


def upgrade() -> None:
    # ─── 1. Вехи цепочки ───────────────────────────────────────────────────
    op.add_column("stock_transfers", sa.Column("actual_ready_date", sa.Date(), nullable=True))
    op.add_column("stock_transfers", sa.Column("shipped_at", sa.DateTime(), nullable=True))

    # ─── 2. Журнал статусов ────────────────────────────────────────────────
    op.create_table(
        "stock_transfer_status_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("stock_transfer_id", sa.Integer(), nullable=False),
        sa.Column("old_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("changed_by", sa.String(length=50), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["stock_transfer_id"], ["stock_transfers.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stock_transfer_status_history_project_id",
        "stock_transfer_status_history",
        ["project_id"],
    )
    op.create_index(
        "ix_stock_transfer_status_history_transfer_id",
        "stock_transfer_status_history",
        ["stock_transfer_id"],
    )

    # ─── 3. Дефолт статуса ─────────────────────────────────────────────────
    op.alter_column("stock_transfers", "status", server_default="PENDING")

    # ─── 4. Перевод существующих строк (порядок важен, см. докстрингу) ─────
    conn = op.get_bind()
    conn.execute(
        text(
            "UPDATE stock_transfers SET status = 'VEHICLE_ASSIGNED' "
            "WHERE status = 'DRAFT' AND vehicle_assigned_at IS NOT NULL"
        )
    )
    conn.execute(text("UPDATE stock_transfers SET status = 'PENDING' WHERE status = 'DRAFT'"))
    _remap(_FORWARD)


def downgrade() -> None:
    _remap(_BACK)
    op.alter_column("stock_transfers", "status", server_default="DRAFT")
    op.drop_index(
        "ix_stock_transfer_status_history_transfer_id", table_name="stock_transfer_status_history"
    )
    op.drop_index(
        "ix_stock_transfer_status_history_project_id", table_name="stock_transfer_status_history"
    )
    op.drop_table("stock_transfer_status_history")
    op.drop_column("stock_transfers", "shipped_at")
    op.drop_column("stock_transfers", "actual_ready_date")
