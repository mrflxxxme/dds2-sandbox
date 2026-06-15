"""add duty_exceptions table (article-level duty overrides)

Per-article override of the category duty rule: for specific seller articles
inside a category, apply a different duty basis+rate than the category's
duty_rules entry. Only the duty (basis+rate) is overridden — util_collect_rub
still comes from the category rule.

Revision ID: de01_duty_exceptions
Revises: ff04_supply_projects
Create Date: 2026-06-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "de01_duty_exceptions"
down_revision: str | None = "ff04_supply_projects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # dutybasis enum already exists (created in 0001) — reference, never recreate.
    duty_basis = postgresql.ENUM("WEIGHT", "AREA", "INVOICE", name="dutybasis", create_type=False)

    op.create_table(
        "duty_exceptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.Integer, sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("article_seller", sa.String(100), nullable=False),
        sa.Column("basis", duty_basis, nullable=False),
        sa.Column("rate", sa.Numeric(10, 6), nullable=False),
        sa.Column("note", sa.String(200)),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
    )

    # FK index (every FK needs one — migrations.md).
    op.create_index("ix_duty_exceptions_project_id", "duty_exceptions", ["project_id"])

    # Partial unique index: only ONE active exception per (project, article).
    # Excludes soft-deleted rows so re-adding a previously deleted article does
    # not collide (SoftDelete + unique mine — see learnings.md).
    op.create_index(
        "uq_duty_exc_project_article",
        "duty_exceptions",
        ["project_id", "article_seller"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )

    # Row-level security — same project-isolation policy as sibling tables.
    op.execute("ALTER TABLE duty_exceptions ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY rls_duty_exceptions_project_isolation ON duty_exceptions
        FOR ALL
        USING (
            project_id = NULLIF(current_setting('app.project_id', true), '')::int
        )
        WITH CHECK (
            project_id = NULLIF(current_setting('app.project_id', true), '')::int
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS rls_duty_exceptions_project_isolation ON duty_exceptions")
    op.execute("ALTER TABLE duty_exceptions DISABLE ROW LEVEL SECURITY")
    op.drop_index("uq_duty_exc_project_article", table_name="duty_exceptions")
    op.drop_index("ix_duty_exceptions_project_id", table_name="duty_exceptions")
    op.drop_table("duty_exceptions")
