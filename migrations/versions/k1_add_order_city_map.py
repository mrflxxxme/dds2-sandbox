"""add order_city_map table

Revision ID: k1_add_order_city_map
Revises: j1_add_wb_warehouse_stocks
Create Date: 2026-03-13 11:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'k1_add_order_city_map'
down_revision = 'j1_add_wb_warehouse_stocks'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'order_city_map',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('srid', sa.String(length=100), nullable=False),
        sa.Column('city', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_order_city_map_project_srid', 'order_city_map', ['project_id', 'srid'], unique=True)
    op.create_index('ix_order_city_map_project_id', 'order_city_map', ['project_id'])


def downgrade() -> None:
    op.drop_index('ix_order_city_map_project_id', table_name='order_city_map')
    op.drop_index('ix_order_city_map_project_srid', table_name='order_city_map')
    op.drop_table('order_city_map')
