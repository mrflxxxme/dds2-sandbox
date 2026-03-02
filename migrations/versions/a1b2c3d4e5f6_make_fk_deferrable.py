"""Make cost_order_items FK deferrable

Revision ID: a1b2c3d4e5f6
Revises: 69703897c946
Create Date: 2026-03-02 11:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '69703897c946'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Make cost_order_items_order_no_fkey DEFERRABLE so order_no can be renamed."""
    op.execute(
        "ALTER TABLE cost_order_items "
        "ALTER CONSTRAINT cost_order_items_order_no_fkey "
        "DEFERRABLE INITIALLY IMMEDIATE"
    )


def downgrade() -> None:
    """Revert to NOT DEFERRABLE."""
    op.execute(
        "ALTER TABLE cost_order_items "
        "ALTER CONSTRAINT cost_order_items_order_no_fkey "
        "NOT DEFERRABLE"
    )
