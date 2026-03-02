"""Fix IntegrationKey and WbPayout unique constraints to include project_id

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-03-02 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "c3d4e5f6g7h8"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── IntegrationKey: (service, label) → (project_id, service, label) ──────
    op.drop_constraint("uq_integration_service_label", "integration_keys", type_="unique")
    op.create_unique_constraint(
        "uq_integration_project_service_label",
        "integration_keys",
        ["project_id", "service", "label"],
    )

    # ── WbPayout: request_id (globally unique) → (project_id, request_id) ───
    # Drop the old unique constraint on request_id
    op.drop_constraint("wb_payouts_request_id_key", "wb_payouts", type_="unique")
    op.create_unique_constraint(
        "uq_wb_payout_project_request",
        "wb_payouts",
        ["project_id", "request_id"],
    )


def downgrade() -> None:
    # ── WbPayout: restore global unique on request_id ────────────────────────
    op.drop_constraint("uq_wb_payout_project_request", "wb_payouts", type_="unique")
    op.create_unique_constraint("wb_payouts_request_id_key", "wb_payouts", ["request_id"])

    # ── IntegrationKey: restore (service, label) ─────────────────────────────
    op.drop_constraint("uq_integration_project_service_label", "integration_keys", type_="unique")
    op.create_unique_constraint("uq_integration_service_label", "integration_keys", ["service", "label"])
