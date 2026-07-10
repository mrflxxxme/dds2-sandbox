"""add wb measurements tables

Замеры складов WB (warehouse-measurements) + удержания за занижение габаритов
(measurement-penalties). Источник — WB Analytics API.

Revision ID: e0551748b04f
Revises: wbp02_wb_supply_state
Create Date: 2026-07-09 12:50:02.896710

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e0551748b04f'
down_revision: Union[str, None] = 'wbp02_wb_supply_state'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'wb_warehouse_measurements',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('dim_id', sa.BigInteger(), nullable=False),
        sa.Column('nm_id', sa.BigInteger(), nullable=False),
        sa.Column('subject_name', sa.String(length=300), nullable=True),
        sa.Column('length', sa.Integer(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('volume', sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column('photo_urls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('measured_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'dim_id', name='uq_wb_wh_measurement_project_dim'),
    )
    op.create_index('ix_wb_wh_measurement_project_measured', 'wb_warehouse_measurements', ['project_id', 'measured_at'], unique=False)
    op.create_index('ix_wb_wh_measurement_project_nm', 'wb_warehouse_measurements', ['project_id', 'nm_id'], unique=False)

    op.create_table(
        'wb_measurement_penalties',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('dim_id', sa.BigInteger(), nullable=False),
        sa.Column('nm_id', sa.BigInteger(), nullable=False),
        sa.Column('subject_name', sa.String(length=300), nullable=True),
        sa.Column('prc_over', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('act_length', sa.Integer(), nullable=True),
        sa.Column('act_width', sa.Integer(), nullable=True),
        sa.Column('act_height', sa.Integer(), nullable=True),
        sa.Column('act_volume', sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column('dec_length', sa.Integer(), nullable=True),
        sa.Column('dec_width', sa.Integer(), nullable=True),
        sa.Column('dec_height', sa.Integer(), nullable=True),
        sa.Column('dec_volume', sa.Numeric(precision=12, scale=3), nullable=True),
        sa.Column('penalty_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('reversal_amount', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('units_count', sa.Integer(), nullable=True),
        sa.Column('is_valid', sa.Boolean(), nullable=True),
        sa.Column('is_valid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('penalty_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('photo_urls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('project_id', 'dim_id', 'penalty_date', name='uq_wb_measurement_penalty_project_dim_date'),
    )
    op.create_index('ix_wb_measurement_penalty_project_date', 'wb_measurement_penalties', ['project_id', 'penalty_date'], unique=False)
    op.create_index('ix_wb_measurement_penalty_project_nm', 'wb_measurement_penalties', ['project_id', 'nm_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_wb_measurement_penalty_project_nm', table_name='wb_measurement_penalties')
    op.drop_index('ix_wb_measurement_penalty_project_date', table_name='wb_measurement_penalties')
    op.drop_table('wb_measurement_penalties')

    op.drop_index('ix_wb_wh_measurement_project_nm', table_name='wb_warehouse_measurements')
    op.drop_index('ix_wb_wh_measurement_project_measured', table_name='wb_warehouse_measurements')
    op.drop_table('wb_warehouse_measurements')
