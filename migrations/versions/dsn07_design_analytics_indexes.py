# ruff: noqa: RUF002, RUF003
"""design: индексы под окно периода аналитики и под LEAD-проход по журналу

Revision ID: dsn07_design_analytics_indexes
Revises: dsn06_design_dashboard_layout
Create Date: 2026-08-20

Найдено ревью волны D. Все индексы `design_tasks` из dsn01 начинаются с
`project_id`, но продолжаются `status`/`assignee_user_id`/`due_date` — ни один
не содержит `created_at`. Волна D добавила пять запросов, которые фильтруют
именно по окну `created_at`: три разреза аналитики и два в выгрузке. Без
индекса каждый — скан всех живых задач проекта, и дашборд дёргает их пачкой.

Второй индекс — под воронку. Она строит оконную функцию LEAD с
`PARTITION BY task_id ORDER BY changed_at` поверх событий проекта. Существующий
`ix_design_task_events_project_task (project_id, task_id)` даёт префикс, но не
порядок — на каждый вызов ложится сортировка. Новый индекс его ПОГЛОЩАЕТ
(та же пара колонок в том же порядке плюс третья), поэтому старый дропается:
число объектов не растёт, а сортировка внутри партиции уходит совсем.

Это важно на вырост: `design_task_events` append-only и без ретенции, а волна C
добавила запись события на каждую смену меток и реквизитов — включая каждую
задачу в массовой операции. Журнал теперь растёт кратно быстрее самих задач.

Таблицы модуля молодые и малые, поэтому обычный CREATE INDEX без CONCURRENTLY:
блокировка на запись измеряется миллисекундами (та же логика, что в dsn03).
"""

import sqlalchemy as sa
from alembic import op

revision = "dsn07_design_analytics_indexes"
down_revision = "dsn06_design_dashboard_layout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_design_tasks_project_created",
        "design_tasks",
        ["project_id", "created_at"],
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_design_task_events_project_task_changed",
        "design_task_events",
        ["project_id", "task_id", "changed_at"],
    )
    #  Поглощён индексом выше — держать оба смысла нет.
    op.drop_index("ix_design_task_events_project_task", table_name="design_task_events")


def downgrade() -> None:
    op.create_index(
        "ix_design_task_events_project_task", "design_task_events", ["project_id", "task_id"]
    )
    op.drop_index("ix_design_task_events_project_task_changed", table_name="design_task_events")
    op.drop_index("ix_design_tasks_project_created", table_name="design_tasks")
