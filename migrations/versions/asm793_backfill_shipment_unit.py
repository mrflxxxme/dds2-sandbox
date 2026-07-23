"""merge heads + backfill outbound_shipments.shipped_as_boxes из заявки

Revision ID: asm793_backfill_shipment_unit
Revises: asm792_outbound_shipped_as_boxes, ffb01_ff_billing
Create Date: 2026-07-23

Merge двух голов (asm792 + ffb01) в одну. Заодно бэкфилл: выравниваем единицу
поставки на уже созданных заборах (`outbound_shipments.shipped_as_boxes`) по их
заявке (`assembly_requests.shipped_as_boxes`). Нужно для заявок, переключённых на
короба ПОСЛЕ отгрузки — их забор был снят как «паллеты» (дефолт), теперь синкается.
Идемпотентно (IS DISTINCT FROM) — трогает только реально расходящиеся строки.
"""

from alembic import op

revision = "asm793_backfill_shipment_unit"
down_revision = ("asm792_outbound_shipped_as_boxes", "ffb01_ff_billing")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE outbound_shipments os "
        "SET shipped_as_boxes = ar.shipped_as_boxes "
        "FROM assembly_requests ar "
        "WHERE os.assembly_request_id = ar.id "
        "AND os.shipped_as_boxes IS DISTINCT FROM ar.shipped_as_boxes"
    )


def downgrade() -> None:
    # Merge-миграция: откат делит граф обратно на две головы. Бэкфилл единицы —
    # выравнивание снимка по источнику, безопасно не откатывать (данные не теряются).
    pass
