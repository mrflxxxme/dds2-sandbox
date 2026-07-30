# ruff: noqa: RUF001, RUF002, RUF003
"""fbsasm02: журнал переходов статусов заданий FBS (таймлайн «Статус заказа»)

WB Marketplace API истории статусов не отдаёт — журнал пишет наш синк в момент
обнаружения перехода (обе оси: supplier_status / wb_status). Питает модалку
таймлайна задания; прошлое закрывают синтетические якоря из точных дат
(created_at_wb, closed_at/scan_dt поставки, written_off_at).

Цепочка: за fbsasm01_fbs_mirror (локальный хвост ветки feat/main-tree-port).
"""

import sqlalchemy as sa
from alembic import op

revision = "fbsasm02_order_events"
down_revision = "fbsasm01_fbs_mirror"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wb_fbs_order_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("wb_fbs_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("axis", sa.String(20), nullable=False),
        sa.Column("old_value", sa.String(30), nullable=True),
        sa.Column("new_value", sa.String(30), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_wb_fbs_order_events_order",
        "wb_fbs_order_events",
        ["project_id", "order_id", "changed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_wb_fbs_order_events_order", table_name="wb_fbs_order_events")
    op.drop_table("wb_fbs_order_events")
