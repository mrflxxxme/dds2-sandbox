# ruff: noqa: RUF002, RUF003
"""design: индексы на FK-колонки пользователей (правило «FK ⇒ индекс»)

Revision ID: dsn03_design_fk_indexes
Revises: dsn02_design_notify_flag
Create Date: 2026-08-03

Аудит Ф7-подготовки: четыре FK на users.id остались без индексов (dsn01 покрыл
только project/task-навигацию). Без них каскадные проверки при удалении
пользователя и выборки «версии/материалы/комментарии этого пользователя»
идут seq scan'ом.

Аддитивно, только индексы — данные не трогаются, downgrade полный. Таблицы
модуля молодые и малые, поэтому обычный CREATE INDEX (без CONCURRENTLY):
блокировка на запись измеряется миллисекундами.
"""

from alembic import op

revision = "dsn03_design_fk_indexes"
down_revision = "dsn02_design_notify_flag"
branch_labels = None
depends_on = None

# (index_name, table, column) — зеркало Index(...) в backend/models/design.py
# (дублирование обязательно, иначе autogenerate-дрейф).
_INDEXES = [
    ("ix_design_materials_created_by_user_id", "design_materials", "created_by_user_id"),
    ("ix_design_submissions_submitted_by_user_id", "design_submissions", "submitted_by_user_id"),
    ("ix_design_submissions_verdict_by_user_id", "design_submissions", "verdict_by_user_id"),
    ("ix_design_task_comments_author_user_id", "design_task_comments", "author_user_id"),
]


def upgrade() -> None:
    for name, table, column in _INDEXES:
        op.create_index(name, table, [column])


def downgrade() -> None:
    for name, table, _column in reversed(_INDEXES):
        op.drop_index(name, table_name=table)
