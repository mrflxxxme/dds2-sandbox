# ruff: noqa: RUF001, RUF002, RUF003
"""fbsasm01: учётные заявки на сборку FBS (зеркало сборки ФФ)

- assembly_requests.kind ('fbo' дефолт / 'fbs') — тип заявки: операционная
  логиста или учётное зеркало сборки, которую ведёт сам ФФ.
- assembly_requests.fbs_supply_id — поставка FBS (WB-GI-…), из которой джоб
  собрал зеркало; partial unique (project_id, fbs_supply_id): одна поставка —
  максимум одна заявка, идемпотентность джоба.
- wb_fbs_warehouses.auto_assembly — тумблер «WMS провайдера сам ведёт сборку
  FBS → мы ведём учёт». Data-часть включает его складам, чья активная привязка
  смотрит на наш склад с активным WMS-ключом (skladbot/wmscelicom/migfull) —
  решение владельца 30.07.2026: все три WMS-склада сразу.

⚠️ Цепочка: payroll05_scope_matrix → rbac01_pages_upd → fbsasm01_fbs_mirror —
ЛОКАЛЬНЫЙ хвост ветки feat/main-tree-port (ни одна не на origin/dev).
rbac01 закоммичен ВМЕСТЕ с этой миграцией (ревью: ссылка на незакоммиченную
ревизию роняла чистый чекаут). Пушить только вместе; при перецепке
payroll/rbac-хвоста перецепить fbsasm01 следом.
"""

import sqlalchemy as sa
from alembic import op

revision = "fbsasm01_fbs_mirror"
down_revision = "rbac01_pages_upd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assembly_requests",
        sa.Column("kind", sa.String(10), nullable=False, server_default="fbo"),
    )
    op.add_column(
        "assembly_requests",
        sa.Column("fbs_supply_id", sa.String(50), nullable=True),
    )
    op.create_index(
        "uq_assembly_requests_fbs_supply",
        "assembly_requests",
        ["project_id", "fbs_supply_id"],
        unique=True,
        postgresql_where=sa.text("fbs_supply_id IS NOT NULL"),
    )
    op.add_column(
        "wb_fbs_warehouses",
        sa.Column("auto_assembly", sa.Boolean(), nullable=False, server_default="false"),
    )
    # Включаем авто-учёт складам, которые кормятся нашим складом с живым
    # WMS-ключом: ровно там ФФ уже сам заводит заявки в своей WMS.
    op.execute(
        sa.text(
            """
            UPDATE wb_fbs_warehouses w
            SET auto_assembly = true
            WHERE EXISTS (
                SELECT 1
                FROM wb_fbs_warehouse_links l
                JOIN integration_keys k
                  ON k.warehouse_id = l.warehouse_id
                 AND k.project_id = l.project_id
                 AND k.is_deleted = false
                 AND k.is_active = true
                 AND k.service IN ('skladbot', 'wmscelicom', 'migfull')
                WHERE l.project_id = w.project_id
                  AND l.wb_warehouse_id = w.wb_warehouse_id
                  AND l.is_active = true
            )
            """
        )
    )


def downgrade() -> None:
    op.drop_column("wb_fbs_warehouses", "auto_assembly")
    op.drop_index("uq_assembly_requests_fbs_supply", table_name="assembly_requests")
    op.drop_column("assembly_requests", "fbs_supply_id")
    op.drop_column("assembly_requests", "kind")
