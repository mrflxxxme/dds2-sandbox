# ruff: noqa: RUF002, RUF003
"""design: цветные метки и универсальный справочник реквизитов

Revision ID: dsn05_design_labels_attributes
Revises: dsn04_design_number_len
Create Date: 2026-08-20

Волна C v2 (Р20, Р26–Р31, Р33). Пять таблиц, все project_id-scoped, изоляция
детей — составными FK на (id, project_id), как во всём модуле.

Ключевое про partial-unique: условие включает И is_deleted, И is_archived.
Пользовательское «Удалить» здесь — архивирование (Р30), оно НЕ ставит
is_deleted; индекс только по is_deleted держал бы имя архивной записи занятым
навсегда, и попытка завести запись с тем же именем падала бы IntegrityError
(500) вместо контрактного 400. Гвард уникальности в сервисе проверяет ровно
тот же набор — активные записи.

Индексы зеркалятся в backend/models/design.py: расхождение даёт autogenerate-дрейф.

Сид (Р33): каждому проекту, где УЖЕ есть задачи дизайна, заводятся два поля —
«Кабинет ВБ» (без значений) и «Бренд» с шестью брендами со скриншота заказчика.
Меток не создаём: цвета команда заводит под себя.
"""

import sqlalchemy as sa
from alembic import op

revision = "dsn05_design_labels_attributes"
down_revision = "dsn04_design_number_len"
branch_labels = None
depends_on = None

_SEED_CABINET = "Кабинет ВБ"
_SEED_BRAND = "Бренд"
_SEED_BRANDS = ["АРТСПЕЙС", "Меллори", "НУ-НУ", "СамПоклей", "Уютопия", "Redmi"]


def upgrade() -> None:
    op.create_table(
        "design_labels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("color", sa.String(length=20), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "project_id", name="uq_design_labels_id_project"),
    )
    op.create_index(
        "uq_design_labels_project_name",
        "design_labels",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND is_archived = false"),
    )
    op.create_index(
        "ix_design_labels_project_sort",
        "design_labels",
        ["project_id", "sort_order"],
        postgresql_where=sa.text("is_deleted = false"),
    )

    op.create_table(
        "design_task_labels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("label_id", sa.Integer(), nullable=False),
        sa.Column("attached_at", sa.DateTime(), nullable=False),
        sa.Column("attached_by", sa.String(length=100), nullable=True),
        sa.Column("removed_at", sa.DateTime(), nullable=True),
        sa.Column("removed_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["label_id", "project_id"],
            ["design_labels.id", "design_labels.project_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_design_task_labels_active",
        "design_task_labels",
        ["task_id", "label_id"],
        unique=True,
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_index(
        "ix_design_task_labels_project_task",
        "design_task_labels",
        ["project_id", "task_id"],
        postgresql_where=sa.text("removed_at IS NULL"),
    )
    op.create_index(
        "ix_design_task_labels_project_label", "design_task_labels", ["project_id", "label_id"]
    )
    #  FK ⇒ индекс: составные FK ведут по task_id и label_id, а индексы выше
    #  начинаются с project_id и каскадных проверок не покрывают (та же правка,
    #  что dsn03 делала для материалов и версий).
    op.create_index("ix_design_task_labels_task_id", "design_task_labels", ["task_id"])
    op.create_index("ix_design_task_labels_label_id", "design_task_labels", ["label_id"])

    op.create_table(
        "design_attributes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("is_multi", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "project_id", name="uq_design_attributes_id_project"),
    )
    op.create_index(
        "uq_design_attributes_project_name",
        "design_attributes",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND is_archived = false"),
    )
    op.create_index(
        "ix_design_attributes_project_sort",
        "design_attributes",
        ["project_id", "sort_order"],
        postgresql_where=sa.text("is_deleted = false"),
    )

    op.create_table(
        "design_attribute_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("attribute_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=120), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["attribute_id", "project_id"],
            ["design_attributes.id", "design_attributes.project_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "project_id", name="uq_design_attribute_values_id_project"),
    )
    op.create_index(
        "uq_design_attribute_values_unique",
        "design_attribute_values",
        ["project_id", "attribute_id", "value"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false AND is_archived = false"),
    )
    #  Без postgresql_where: партиальный индекс не применим к RI-проверке
    #  каскада, а строк с is_deleted = true у справочника не бывает вовсе.
    op.create_index(
        "ix_design_attribute_values_project_attribute",
        "design_attribute_values",
        ["project_id", "attribute_id"],
    )
    op.create_index(
        "ix_design_attribute_values_attribute_id", "design_attribute_values", ["attribute_id"]
    )

    op.create_table(
        "design_task_attribute_values",
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("value_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["value_id", "project_id"],
            ["design_attribute_values.id", "design_attribute_values.project_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "value_id"),
    )
    op.create_index(
        "ix_design_task_attr_values_project_task",
        "design_task_attribute_values",
        ["project_id", "task_id"],
    )
    op.create_index(
        "ix_design_task_attr_values_project_value",
        "design_task_attribute_values",
        ["project_id", "value_id"],
    )
    #  PK (task_id, value_id) покрывает поиск по task_id, а по value_id — нет.
    op.create_index(
        "ix_design_task_attr_values_value_id", "design_task_attribute_values", ["value_id"]
    )

    seed_refs(op.get_bind())


def seed_refs(conn) -> None:  # noqa: ANN001  (alembic Connection, тип не импортируем)
    """Два поля-реквизита каждому проекту, где уже есть задачи дизайна (Р33).

    Идемпотентно по имени: повторный прогон на проекте, где поле уже заведено
    вручную, ничего не задваивает (иначе partial-unique дал бы IntegrityError).

    Соединение принимается параметром, а не берётся из `op`, ровно чтобы сид
    можно было прогнать тестом: на пустой БД миграция отрабатывает вхолостую и
    никакой ошибки в нём не поймает.
    """
    project_ids = [
        row[0]
        for row in conn.execute(
            sa.text("SELECT DISTINCT project_id FROM design_tasks WHERE is_deleted = false")
        )
    ]
    for pid in project_ids:
        for order, name in enumerate((_SEED_CABINET, _SEED_BRAND)):
            existing = conn.execute(
                sa.text(
                    "SELECT id FROM design_attributes "
                    "WHERE project_id = :pid AND name = :name "
                    "AND is_deleted = false AND is_archived = false"
                ),
                {"pid": pid, "name": name},
            ).scalar()
            if existing is not None:
                attribute_id = existing
            else:
                attribute_id = conn.execute(
                    sa.text(
                        "INSERT INTO design_attributes "
                        "(project_id, name, is_multi, sort_order, is_archived, is_deleted, created_at) "
                        "VALUES (:pid, :name, false, :sort, false, false, NOW()) RETURNING id"
                    ),
                    {"pid": pid, "name": name, "sort": order},
                ).scalar()
            if name != _SEED_BRAND:
                continue
            for value_order, brand in enumerate(_SEED_BRANDS):
                conn.execute(
                    sa.text(
                        "INSERT INTO design_attribute_values "
                        "(project_id, attribute_id, value, sort_order, is_archived, is_deleted) "
                        "SELECT :pid, :aid, :value, :sort, false, false "
                        "WHERE NOT EXISTS ("
                        "  SELECT 1 FROM design_attribute_values "
                        "  WHERE project_id = :pid AND attribute_id = :aid AND value = :value "
                        "  AND is_deleted = false AND is_archived = false)"
                    ),
                    {"pid": pid, "aid": attribute_id, "value": brand, "sort": value_order},
                )


def downgrade() -> None:
    # ⚠️ Откат уничтожает ИСТОРИЮ МЕТОК (Р20) безвозвратно: счётчик «была с
    # меткой N раз» живёт только в design_task_labels и ниоткуда не выводится.
    # Перед откатом на живом проекте — pg_dump -t design_task_labels.
    op.drop_table("design_task_attribute_values")
    op.drop_table("design_attribute_values")
    op.drop_table("design_attributes")
    op.drop_table("design_task_labels")
    op.drop_table("design_labels")
