"""wb_warehouse_remains: зеркало отчёта WB «Остатки на складах» (analytics API)

Фактические остатки как в кабинете WB — включая приёмку и межскладской
транзит, которых statistics supplier/stocks не видит. Псевдо-склады
(«В пути до получателей», «В пути возвраты на склад WB», «Всего находится
на складах») хранятся строками как отдаёт API. Full replace на каждом синке.

Revision ID: wbr01_wb_warehouse_remains
Revises: abt01_ab_photo_tests
Create Date: 2026-07-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "wbr01_wb_warehouse_remains"
down_revision: str | None = "abt01_ab_photo_tests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wb_warehouse_remains",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("barcode", sa.String(100), nullable=False, server_default=""),
        sa.Column("vendor_code", sa.String(100), nullable=True),
        sa.Column("brand", sa.String(200), nullable=True),
        sa.Column("subject", sa.String(200), nullable=True),
        sa.Column("tech_size", sa.String(50), nullable=True),
        sa.Column("volume", sa.Numeric(10, 3), nullable=True),
        sa.Column("warehouse_name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.UniqueConstraint(
            "project_id", "nm_id", "barcode", "warehouse_name", name="uq_wb_remains_nm_barcode_wh"
        ),
    )
    op.create_index("ix_wb_warehouse_remains_project_id", "wb_warehouse_remains", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_wb_warehouse_remains_project_id", table_name="wb_warehouse_remains")
    op.drop_table("wb_warehouse_remains")
