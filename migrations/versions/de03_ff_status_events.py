# ruff: noqa: RUF002, RUF003
"""fulfillment status events: журнал смены статусов/стадий заявок ФФ

Зеркало заявки (fulfillment_requests) хранит лишь текущее состояние и
перетирается синком. Эта таблица — append-only журнал переходов: синк пишет
строку, когда стадия/статус/флаги заявки изменились (или при первом появлении
заявки — event_type=created). Работает для всех провайдеров.

Revision ID: de03_ff_status_events
Revises: de02_nomenclature_weight
Create Date: 2026-06-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de03_ff_status_events"
down_revision: str | None = "de02_nomenclature_weight"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fulfillment_status_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("fulfillment_request_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(length=100), nullable=False),
        sa.Column("number", sa.String(length=100), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="other"),
        sa.Column("event_type", sa.String(length=20), nullable=False),
        sa.Column("old_status", sa.String(length=50), nullable=True),
        sa.Column("new_status", sa.String(length=50), nullable=True),
        sa.Column("old_stage_code", sa.String(length=100), nullable=True),
        sa.Column("new_stage_code", sa.String(length=100), nullable=True),
        sa.Column("old_stage_title", sa.String(length=200), nullable=True),
        sa.Column("new_stage_title", sa.String(length=200), nullable=True),
        sa.Column("old_is_completed", sa.Boolean(), nullable=True),
        sa.Column("new_is_completed", sa.Boolean(), nullable=True),
        sa.Column("old_archived", sa.Boolean(), nullable=True),
        sa.Column("new_archived", sa.Boolean(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["warehouse_id"], ["warehouses.id"]),
        sa.ForeignKeyConstraint(["fulfillment_request_id"], ["fulfillment_requests.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ff_status_events_project_wh_changed",
        "fulfillment_status_events",
        ["project_id", "warehouse_id", "changed_at"],
    )
    op.create_index(
        "ix_ff_status_events_request_id",
        "fulfillment_status_events",
        ["fulfillment_request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_ff_status_events_request_id", table_name="fulfillment_status_events")
    op.drop_index("ix_ff_status_events_project_wh_changed", table_name="fulfillment_status_events")
    op.drop_table("fulfillment_status_events")
