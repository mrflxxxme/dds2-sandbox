"""АБ-тесты главного фото: ab_photo_tests + ab_photo_variants + ab_photo_rounds

Ротация вариантов главного фото по «кругам»: смена по времени round_minutes,
досрочно — по набору views_per_round показов; метрики круга —
дельты накопительных счётчиков WB «за сегодня» (fullstats по nmId + воронка v3).
Подробности механики — в docstring backend/models/ab_tests.py.

Revision ID: abt01_ab_photo_tests
Revises: wbp05_wb_pass_snapshot
Create Date: 2026-07-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "abt01_ab_photo_tests"
down_revision: str | None = "wbp05_wb_pass_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ab_photo_tests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=False),
        sa.Column("campaign_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("pause_reason", sa.Text(), nullable=True),
        sa.Column("views_per_round", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("round_minutes", sa.Integer(), nullable=False, server_default="45"),
        sa.Column("target_views", sa.Integer(), nullable=False, server_default="10000"),
        sa.Column("max_days", sa.Integer(), nullable=False, server_default="7"),
        sa.Column("original_photo_url", sa.Text(), nullable=True),
        sa.Column("original_media", JSONB(), nullable=True),
        sa.Column("active_variant_id", sa.Integer(), nullable=True),
        sa.Column("last_stat", JSONB(), nullable=True),
        sa.Column("winner_variant_id", sa.Integer(), nullable=True),
        sa.Column("winner_applied_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # FK project_id покрыт составными индексами (project_id — ведущая колонка)
    op.create_index("ix_ab_photo_tests_project_status", "ab_photo_tests", ["project_id", "status"])
    op.create_index("ix_ab_photo_tests_project_nm", "ab_photo_tests", ["project_id", "nm_id"])

    op.create_table(
        "ab_photo_variants",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("test_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("is_control", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("image_key", sa.Text(), nullable=False),
        sa.Column("excluded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["test_id"], ["ab_photo_tests.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("test_id", "position", name="uq_ab_photo_variant_position"),
    )
    op.create_index("ix_ab_photo_variants_test", "ab_photo_variants", ["project_id", "test_id"])

    op.create_table(
        "ab_photo_rounds",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("test_id", sa.Integer(), nullable=False),
        sa.Column("variant_id", sa.Integer(), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("apply_ok", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("flags", JSONB(), nullable=True),
        sa.Column("views", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("atbs", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spend", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("orders_sum", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("organic_open", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("organic_cart", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("organic_orders", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["test_id"], ["ab_photo_tests.id"]),
        sa.ForeignKeyConstraint(["variant_id"], ["ab_photo_variants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("test_id", "round_no", name="uq_ab_photo_round_no"),
    )
    op.create_index("ix_ab_photo_rounds_test", "ab_photo_rounds", ["project_id", "test_id", "round_no"])
    op.create_index("ix_ab_photo_rounds_variant", "ab_photo_rounds", ["project_id", "variant_id"])


def downgrade() -> None:
    op.drop_index("ix_ab_photo_rounds_variant", table_name="ab_photo_rounds")
    op.drop_index("ix_ab_photo_rounds_test", table_name="ab_photo_rounds")
    op.drop_table("ab_photo_rounds")
    op.drop_index("ix_ab_photo_variants_test", table_name="ab_photo_variants")
    op.drop_table("ab_photo_variants")
    op.drop_index("ix_ab_photo_tests_project_nm", table_name="ab_photo_tests")
    op.drop_index("ix_ab_photo_tests_project_status", table_name="ab_photo_tests")
    op.drop_table("ab_photo_tests")
