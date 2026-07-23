"""assembly_pickup_cost_history: аудит правок стоимости перевозки заявки (ASM-785)

Revision ID: asm785_pickup_cost_history
Revises: dch01_draft_category_hourly
Create Date: 2026-07-23

Таблица истории изменений `assembly_requests.pickup_cost` (стоимость перевозки/
забора): старая/новая цена, момент и автор («кто поменял»). Пишется на каждую
фактическую правку через PUT /warehouse/assembly/{id}. Питает карточку
«История стоимости перевозки» на деталке сборки.
"""

import sqlalchemy as sa
from alembic import op

revision = "asm785_pickup_cost_history"
down_revision = "dch01_draft_category_hourly"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assembly_pickup_cost_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("assembly_request_id", sa.Integer(), nullable=False),
        sa.Column("old_cost", sa.Numeric(10, 2), nullable=True),
        sa.Column("new_cost", sa.Numeric(10, 2), nullable=False),
        sa.Column("changed_at", sa.DateTime(), nullable=False),
        sa.Column("changed_by", sa.String(length=50), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["assembly_request_id"], ["assembly_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assembly_pickup_cost_history_project_id",
        "assembly_pickup_cost_history",
        ["project_id"],
    )
    op.create_index(
        "ix_assembly_pickup_cost_history_request_id",
        "assembly_pickup_cost_history",
        ["assembly_request_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_assembly_pickup_cost_history_request_id",
        table_name="assembly_pickup_cost_history",
    )
    op.drop_index(
        "ix_assembly_pickup_cost_history_project_id",
        table_name="assembly_pickup_cost_history",
    )
    op.drop_table("assembly_pickup_cost_history")
