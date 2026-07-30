"""project_members.pages_updated_at — водяной знак каталога страниц

Нужен, чтобы отличать «раздела не существовало на момент настройки доступа»
(наследуем по секции) от «владелец снял галочку» (не наследуем).

Бэкфилл: водяной знак = ПОСЛЕДНЯЯ настройка доступов, а не первая. `joined_at`
как единственный источник врёт в опасную сторону — участник, которого владелец
перенастраивал уже после вступления, получил бы назад галочки, снятые при той
перенастройке (проверено на копии прода: правка участника 338 от 23.07.2026 —
ровно день появления ads-manager/ab-tests/barcode-labels в каталоге). Поэтому
берём GREATEST(joined_at, последний успешный PUT .../members/{user_id}/role из
audit_log). Аудит хранится ~5 недель, за его пределами остаётся joined_at —
там разделы новее настройки и так не существовали.

Revision ID: rbac01_pages_upd
Revises: payroll05_scope_matrix

⚠️ Цепочка: payroll05_scope_matrix → rbac01_pages_upd → fbsasm01_fbs_mirror.
Уже закоммиченная fbsasm01 (8bec1bcd) ссылается на ЭТУ ревизию, поэтому без
этого файла в индексе `alembic upgrade head` падает на любом чистом чекауте
(«Can't locate revision 'rbac01_pages_upd'») — локально зелено только потому,
что файл лежит в рабочем дереве. Цикл проверять как
`upgrade head && downgrade -2 && upgrade head`: `-1` откатит fbsasm01, а не эту.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "rbac01_pages_upd"
down_revision: str | None = "payroll05_scope_matrix"
branch_labels = None
depends_on = None

# Последняя явная настройка доступов участника по журналу мутаций.
_BACKFILL_WITH_AUDIT = """
UPDATE project_members pm
SET pages_updated_at = GREATEST(
        pm.joined_at,
        COALESCE(
            (
                SELECT max(a.created_at)
                FROM audit_log a
                WHERE a.project_id = pm.project_id
                  AND a.method = 'PUT'
                  AND a.status_code = 200
                  AND a.endpoint LIKE '%/members/' || pm.user_id::text || '/role'
            ),
            pm.joined_at
        )
    )
WHERE pm.pages_updated_at IS NULL
"""

_BACKFILL_PLAIN = "UPDATE project_members SET pages_updated_at = joined_at WHERE pages_updated_at IS NULL"


def upgrade() -> None:
    op.add_column("project_members", sa.Column("pages_updated_at", sa.DateTime(), nullable=True))
    conn = op.get_bind()
    has_audit = conn.execute(sa.text("SELECT to_regclass('public.audit_log') IS NOT NULL")).scalar()
    op.execute(_BACKFILL_WITH_AUDIT if has_audit else _BACKFILL_PLAIN)


def downgrade() -> None:
    op.drop_column("project_members", "pages_updated_at")
