# ruff: noqa: RUF002, RUF003
"""Совместная FBO-поставка: уникальность сборки по (поставка, склад-источник)

Revision ID: jf01_joint_fbo_supply
Revises: mf01_migfull_shipment_orders
Create Date: 2026-06-27

Раньше: одна WB FBO-поставка ↔ ОДНА сборка (partial unique по
project_id + wb_fbo_supply_id). Теперь одна поставка («Совместный номер»)
может нести несколько сборок — по одной на каждый ФФ-источник (напр. wms + wms2).
Уникальность переезжает на (project_id, wb_fbo_supply_id, warehouse_id): двух
активных сборок С ОДНОГО склада на одну поставку по-прежнему быть не может.
Тот же partial-предикат (не удалённые, не CANCELLED, поставка задана).
"""

from alembic import op

revision = "jf01_joint_fbo_supply"
down_revision = "mf01_migfull_shipment_orders"
branch_labels = None
depends_on = None

_PARTIAL_WHERE = "is_deleted = false AND status != 'CANCELLED' AND wb_fbo_supply_id IS NOT NULL"


def upgrade() -> None:
    # 1:1 (поставка) → 1:N (поставка, склад-источник).
    op.drop_index("ix_assembly_requests_fbo_unique", table_name="assembly_requests")
    op.create_index(
        "ix_assembly_requests_fbo_wh_unique",
        "assembly_requests",
        ["project_id", "wb_fbo_supply_id", "warehouse_id"],
        unique=True,
        postgresql_where=_PARTIAL_WHERE,
    )


def downgrade() -> None:
    # Внимание: откат восстанавливает 1:1 — упадёт, если к моменту отката уже
    # существуют совместные поставки (≥2 активных сборок на одну поставку).
    op.drop_index("ix_assembly_requests_fbo_wh_unique", table_name="assembly_requests")
    op.create_index(
        "ix_assembly_requests_fbo_unique",
        "assembly_requests",
        ["project_id", "wb_fbo_supply_id"],
        unique=True,
        postgresql_where=_PARTIAL_WHERE,
    )
