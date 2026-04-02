"""auth: add soft delete to projects and project_members

Revision ID: auth01_soft_del
Revises: wh01_nom_idx_cost
Create Date: 2026-04-02

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "auth01_soft_del"
down_revision: str | None = "wh01_nom_idx_cost"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    # Projects
    op.add_column("projects", sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("projects", sa.Column("deleted_at", sa.DateTime(), nullable=True))

    # ProjectMembers
    op.add_column("project_members", sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("project_members", sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_members", "deleted_at")
    op.drop_column("project_members", "is_deleted")
    op.drop_column("projects", "deleted_at")
    op.drop_column("projects", "is_deleted")
