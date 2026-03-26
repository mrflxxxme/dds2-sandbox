"""add_role_pages_to_project_members

Revision ID: f3754b9a9aa9
Revises: fix_campaign_type_str
Create Date: 2026-03-26 10:01:15.511656

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3754b9a9aa9"
down_revision: str | None = "fix_campaign_type_str"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add role and pages to project_members
    op.add_column("project_members", sa.Column("role", sa.String(length=20), server_default="editor", nullable=False))
    op.add_column("project_members", sa.Column("pages", sa.Text(), nullable=True))

    # Add role and pages to project_invites
    op.add_column("project_invites", sa.Column("role", sa.String(length=20), server_default="editor", nullable=False))
    op.add_column("project_invites", sa.Column("pages", sa.Text(), nullable=True))

    # Data migration: set owner role for project owners
    op.execute("""
        UPDATE project_members pm
        SET role = 'owner'
        FROM projects p
        WHERE pm.project_id = p.id AND pm.user_id = p.owner_id
    """)

    # All other members become admin (safest default — no one loses access)
    op.execute("""
        UPDATE project_members
        SET role = 'admin'
        WHERE role = 'editor'
        AND user_id NOT IN (
            SELECT pm.user_id FROM project_members pm
            JOIN projects p ON pm.project_id = p.id AND pm.user_id = p.owner_id
        )
    """)


def downgrade() -> None:
    op.drop_column("project_members", "pages")
    op.drop_column("project_members", "role")
    op.drop_column("project_invites", "pages")
    op.drop_column("project_invites", "role")
