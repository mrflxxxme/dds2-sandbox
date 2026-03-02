"""Add project_id to CategoryRef, LeadTime, Nomenclature, DutyRule, CustomsTopup, CustomsAlloc, CustomsDT

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-02 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "b2c3d4e5f6g7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Add project_id columns ──────────────────────────────────────────────

    op.add_column("category_ref", sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True))
    op.add_column("lead_time", sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True))
    op.add_column("nomenclature", sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True))
    op.add_column("duty_rules", sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True))
    op.add_column("customs_topup", sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True))
    op.add_column("customs_alloc", sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True))
    op.add_column("customs_dt", sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=True))

    # ── Drop old unique constraints and create new ones with project_id ─────

    # CategoryRef: (direction, cat_lvl1, cat_lvl2) → (project_id, direction, cat_lvl1, cat_lvl2)
    op.drop_constraint("uq_cat_ref", "category_ref", type_="unique")
    op.create_unique_constraint("uq_cat_ref", "category_ref", ["project_id", "direction", "cat_lvl1", "cat_lvl2"])

    # LeadTime: direction (unique column) → (project_id, direction)
    op.drop_constraint("lead_time_direction_key", "lead_time", type_="unique")
    op.create_unique_constraint("uq_lead_time_project_dir", "lead_time", ["project_id", "direction"])

    # Nomenclature: barcode (unique column) → (project_id, barcode)
    op.drop_constraint("nomenclature_barcode_key", "nomenclature", type_="unique")
    op.create_unique_constraint("uq_nomenclature_project_barcode", "nomenclature", ["project_id", "barcode"])

    # DutyRule: subject (unique column) → (project_id, subject)
    op.drop_constraint("duty_rules_subject_key", "duty_rules", type_="unique")
    op.create_unique_constraint("uq_duty_rule_project_subject", "duty_rules", ["project_id", "subject"])

    # CustomsTopup: topup_txn_id (unique column) → (project_id, topup_txn_id)
    op.drop_constraint("customs_topup_topup_txn_id_key", "customs_topup", type_="unique")
    op.create_unique_constraint("uq_customs_topup_project_txn", "customs_topup", ["project_id", "topup_txn_id"])


def downgrade() -> None:
    # ── Restore old unique constraints ──────────────────────────────────────

    op.drop_constraint("uq_customs_topup_project_txn", "customs_topup", type_="unique")
    op.create_unique_constraint("customs_topup_topup_txn_id_key", "customs_topup", ["topup_txn_id"])

    op.drop_constraint("uq_duty_rule_project_subject", "duty_rules", type_="unique")
    op.create_unique_constraint("duty_rules_subject_key", "duty_rules", ["subject"])

    op.drop_constraint("uq_nomenclature_project_barcode", "nomenclature", type_="unique")
    op.create_unique_constraint("nomenclature_barcode_key", "nomenclature", ["barcode"])

    op.drop_constraint("uq_lead_time_project_dir", "lead_time", type_="unique")
    op.create_unique_constraint("lead_time_direction_key", "lead_time", ["direction"])

    op.drop_constraint("uq_cat_ref", "category_ref", type_="unique")
    op.create_unique_constraint("uq_cat_ref", "category_ref", ["direction", "cat_lvl1", "cat_lvl2"])

    # ── Drop project_id columns ─────────────────────────────────────────────

    op.drop_column("customs_dt", "project_id")
    op.drop_column("customs_alloc", "project_id")
    op.drop_column("customs_topup", "project_id")
    op.drop_column("duty_rules", "project_id")
    op.drop_column("nomenclature", "project_id")
    op.drop_column("lead_time", "project_id")
    op.drop_column("category_ref", "project_id")
