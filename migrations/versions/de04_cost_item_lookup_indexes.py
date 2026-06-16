"""add lookup indexes on cost_order_items for auto-recalc affected-order queries

Auto-recalc of себестоимость finds affected orders by barcode (weight/area),
subject (category duty rule), and article_seller (duty exception). These
composite indexes back those DISTINCT order_no lookups.

Revision ID: de04_cost_item_lookup_indexes
Revises: de03_ff_status_events
Create Date: 2026-06-16

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de04_cost_item_lookup_indexes"
down_revision: str | None = "de03_ff_status_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_cost_item_project_barcode", "cost_order_items", ["project_id", "barcode"])
    op.create_index("ix_cost_item_project_subject", "cost_order_items", ["project_id", "subject"])
    op.create_index("ix_cost_item_project_article", "cost_order_items", ["project_id", "article_seller"])


def downgrade() -> None:
    op.drop_index("ix_cost_item_project_article", table_name="cost_order_items")
    op.drop_index("ix_cost_item_project_subject", table_name="cost_order_items")
    op.drop_index("ix_cost_item_project_barcode", table_name="cost_order_items")
