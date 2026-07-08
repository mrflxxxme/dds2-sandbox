"""assembly_wb_supply: 1:1 связь заявки-сборки с FBW-поставкой WB (реплей портала)

Revision ID: wbp01_assembly_wb_supply
Revises: dh01_assembly_draft_events
Create Date: 2026-07-08

Новая таблица под интеграцию заявки-сборки с личным кабинетом Wildberries:
хранит идентификаторы WB-поставки по стадиям (draft → preorder → supply),
локально-редактируемую раскладку коробов (JSONB) и данные пропуска
(водитель/авто/паллеты). 1:1 к assembly_requests (unique-индекс), CASCADE.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "wbp01_assembly_wb_supply"
down_revision = "dh01_assembly_draft_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assembly_wb_supply",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("assembly_request_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id_wb", sa.Integer(), nullable=True),
        sa.Column("box_type_id", sa.Integer(), nullable=True),
        sa.Column(
            "is_box_on_pallet",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("draft_id", sa.String(length=64), nullable=True),
        sa.Column("preorder_id", sa.BigInteger(), nullable=True),
        sa.Column("supply_id", sa.BigInteger(), nullable=True),
        sa.Column("barcode_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "sync_status",
            sa.String(length=20),
            server_default="NONE",
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column(
            "boxes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("pass_driver_first", sa.String(length=100), nullable=True),
        sa.Column("pass_driver_last", sa.String(length=100), nullable=True),
        sa.Column("pass_driver_phone", sa.String(length=30), nullable=True),
        sa.Column("pass_car_model", sa.String(length=100), nullable=True),
        sa.Column("pass_car_number", sa.String(length=30), nullable=True),
        sa.Column("pass_pallets", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["assembly_request_id"], ["assembly_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_assembly_wb_supply_project_id", "assembly_wb_supply", ["project_id"]
    )
    op.create_index(
        "ix_assembly_wb_supply_assembly_request_id",
        "assembly_wb_supply",
        ["assembly_request_id"],
        unique=True,
    )
    op.create_index(
        "ix_assembly_wb_supply_supply_id", "assembly_wb_supply", ["supply_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_assembly_wb_supply_supply_id", table_name="assembly_wb_supply")
    op.drop_index(
        "ix_assembly_wb_supply_assembly_request_id", table_name="assembly_wb_supply"
    )
    op.drop_index("ix_assembly_wb_supply_project_id", table_name="assembly_wb_supply")
    op.drop_table("assembly_wb_supply")
