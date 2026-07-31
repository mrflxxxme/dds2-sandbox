# ruff: noqa: RUF001, RUF002, RUF003
"""fbsasm03: история статусов задания FBS из КАБИНЕТА WB

Публичный Marketplace API истории не отдаёт — только текущие значения осей.
Кабинет отдаёт полную цепочку с точным временем и городом плеча
(`/ns/marketplace-app/marketplace-remote-wh/api/v3/portal/orders/{id}/history`),
что делает поздние этапы аналитики восстановимыми задним числом.

Имя статуса храним СЫРЫМ: словарь WB открытый, маппинг в вехи — на чтении.

Цепочка: за ffret01_returns_repack (голова ветки на момент правки; за
fbsasm02_order_events уже зацепилась миграция возвратов ФФ — 67ee2664).
"""

import sqlalchemy as sa
from alembic import op

revision = "fbsasm03_order_history"
down_revision = "ffret01_returns_repack"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wb_fbs_order_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("wb_fbs_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("place", sa.String(120), nullable=True),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("project_id", "order_id", "at", "name", name="uq_wb_fbs_order_history"),
    )
    op.create_index(
        "ix_wb_fbs_order_history_order",
        "wb_fbs_order_history",
        ["project_id", "order_id", "at"],
    )

    op.add_column("wb_fbs_orders", sa.Column("history_synced_at", sa.DateTime(), nullable=True))
    op.add_column("wb_fbs_orders", sa.Column("delivery_date_plan", sa.DateTime(), nullable=True))
    # Очередь догона: «сначала те, кого ни разу не забирали, потом самые старые».
    # Сам порядок NULLS FIRST задаёт запрос (`nullsfirst()`) — в Postgres ASC по
    # умолчанию NULLS LAST; индекс здесь нужен ради отбора, а не сортировки.
    op.create_index(
        "ix_wb_fbs_orders_history_sync",
        "wb_fbs_orders",
        ["project_id", "history_synced_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_wb_fbs_orders_history_sync", table_name="wb_fbs_orders")
    op.drop_column("wb_fbs_orders", "delivery_date_plan")
    op.drop_column("wb_fbs_orders", "history_synced_at")
    op.drop_index("ix_wb_fbs_order_history_order", table_name="wb_fbs_order_history")
    op.drop_table("wb_fbs_order_history")
