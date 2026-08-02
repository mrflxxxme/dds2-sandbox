# ruff: noqa: RUF002, RUF003
"""design domain: задачи на дизайн карточек + материалы, сдачи, комментарии, журнал

Модуль «Дизайн карточек» (Ф0): канбан задач на инфографику. 6 новых таблиц:
design_tasks (заявка/задача, DES-N внутри проекта, статусная машина String),
design_materials (исходные материалы: файл/ссылка/артикул-пример),
design_submissions + design_submission_files (версии сдач с вердиктом),
design_task_comments, design_task_events (append-only журнал переходов).

Аддитивно, без переноса данных. Partial-unique номера — WHERE is_deleted = false
(повторный DES-N после soft-delete не падает, донор pay01).

Revision ID: dsn01_design_tasks
Revises: spp03_price_probes
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op

revision = "dsn01_design_tasks"
down_revision = "spp03_price_probes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ─── design_tasks ──────────────────────────────────────────────────────
    op.create_table(
        "design_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("sheet_url", sa.String(length=1000), nullable=False),
        sa.Column("nm_id", sa.BigInteger(), nullable=True),  # без FK (Р2)
        sa.Column("work_type", sa.String(length=20), server_default="FULL_SET", nullable=False),
        sa.Column("complexity", sa.String(length=1), server_default="M", nullable=False),
        sa.Column("is_urgent", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="NEW", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("author_user_id", sa.Integer(), nullable=False),
        sa.Column("assignee_user_id", sa.Integer(), nullable=True),
        sa.Column("is_outsourced", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("viewed_by_lead_at", sa.DateTime(), nullable=True),
        sa.Column("held_from_status", sa.String(length=20), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        # DB-гарантия изоляции: дети ссылаются составным FK на (id, project_id).
        sa.UniqueConstraint("id", "project_id", name="uq_design_tasks_id_project"),
    )
    # Partial-индексы: доска/список/календарь всегда фильтруют is_deleted = false.
    op.create_index(
        "ix_design_tasks_project_status_sort",
        "design_tasks",
        ["project_id", "status", "sort_order"],
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_design_tasks_project_assignee",
        "design_tasks",
        ["project_id", "assignee_user_id", "status"],
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index(
        "ix_design_tasks_project_due",
        "design_tasks",
        ["project_id", "due_date"],
        postgresql_where=sa.text("is_deleted = false"),
    )
    op.create_index("ix_design_tasks_author", "design_tasks", ["project_id", "author_user_id"])
    # Partial-unique — WHERE is_deleted = false (иначе soft-delete занимает слот номера).
    op.create_index(
        "uq_design_tasks_project_number",
        "design_tasks",
        ["project_id", "number"],
        unique=True,
        postgresql_where=sa.text("is_deleted = false"),
    )

    # ─── design_materials («Исходные материалы», Р12) ──────────────────────
    op.create_table(
        "design_materials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("minio_path", sa.String(length=500), nullable=True),
        sa.Column("original_filename", sa.String(length=500), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column("ref_nm_id", sa.BigInteger(), nullable=True),
        sa.Column("caption", sa.String(length=300), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(kind = 'FILE' AND minio_path IS NOT NULL AND url IS NULL AND ref_nm_id IS NULL) OR "
            "(kind = 'LINK' AND url IS NOT NULL AND minio_path IS NULL AND ref_nm_id IS NULL) OR "
            "(kind = 'NM' AND ref_nm_id IS NOT NULL AND minio_path IS NULL AND url IS NULL)",
            name="ck_design_material_payload",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_design_materials_project_task", "design_materials", ["project_id", "task_id"])
    op.create_index("ix_design_materials_task_id", "design_materials", ["task_id"])

    # ─── design_submissions (версии сдач; версии не удаляются) ─────────────
    op.create_table(
        "design_submissions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("verdict", sa.String(length=10), server_default="PENDING", nullable=False),
        sa.Column("verdict_comment", sa.Text(), nullable=True),
        sa.Column("verdict_by_user_id", sa.Integer(), nullable=True),
        sa.Column("verdict_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["submitted_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["verdict_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "version_no", name="uq_design_submissions_task_version"),
        # DB-гарантия изоляции файлов сдач: составной FK детей на (id, project_id).
        sa.UniqueConstraint("id", "project_id", name="uq_design_submissions_id_project"),
    )
    op.create_index("ix_design_submissions_project_task", "design_submissions", ["project_id", "task_id"])
    op.create_index("ix_design_submissions_task_id", "design_submissions", ["task_id"])

    # ─── design_submission_files ───────────────────────────────────────────
    op.create_table(
        "design_submission_files",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("submission_id", sa.Integer(), nullable=False),
        sa.Column("minio_path", sa.String(length=500), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=True),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["submission_id", "project_id"],
            ["design_submissions.id", "design_submissions.project_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_design_submission_files_project_id", "design_submission_files", ["project_id"]
    )
    op.create_index(
        "ix_design_submission_files_submission_id", "design_submission_files", ["submission_id"]
    )

    # ─── design_task_comments ──────────────────────────────────────────────
    op.create_table(
        "design_task_comments",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("author_user_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("minio_path", sa.String(length=500), nullable=True),
        sa.Column("original_filename", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["author_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_design_task_comments_project_task", "design_task_comments", ["project_id", "task_id"]
    )
    op.create_index("ix_design_task_comments_task_id", "design_task_comments", ["task_id"])

    # ─── design_task_events (append-only журнал переходов) ─────────────────
    op.create_table(
        "design_task_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("old_status", sa.String(length=20), nullable=True),
        sa.Column("new_status", sa.String(length=20), nullable=False),
        sa.Column("changed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("changed_by", sa.String(length=100), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(
            ["task_id", "project_id"],
            ["design_tasks.id", "design_tasks.project_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_design_task_events_project_task", "design_task_events", ["project_id", "task_id"]
    )
    op.create_index("ix_design_task_events_task_id", "design_task_events", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_design_task_events_task_id", table_name="design_task_events")
    op.drop_index("ix_design_task_events_project_task", table_name="design_task_events")
    op.drop_table("design_task_events")

    op.drop_index("ix_design_task_comments_task_id", table_name="design_task_comments")
    op.drop_index("ix_design_task_comments_project_task", table_name="design_task_comments")
    op.drop_table("design_task_comments")

    op.drop_index("ix_design_submission_files_submission_id", table_name="design_submission_files")
    op.drop_index("ix_design_submission_files_project_id", table_name="design_submission_files")
    op.drop_table("design_submission_files")

    op.drop_index("ix_design_submissions_task_id", table_name="design_submissions")
    op.drop_index("ix_design_submissions_project_task", table_name="design_submissions")
    op.drop_table("design_submissions")

    op.drop_index("ix_design_materials_task_id", table_name="design_materials")
    op.drop_index("ix_design_materials_project_task", table_name="design_materials")
    op.drop_table("design_materials")

    op.drop_index("uq_design_tasks_project_number", table_name="design_tasks")
    op.drop_index("ix_design_tasks_author", table_name="design_tasks")
    op.drop_index("ix_design_tasks_project_due", table_name="design_tasks")
    op.drop_index("ix_design_tasks_project_assignee", table_name="design_tasks")
    op.drop_index("ix_design_tasks_project_status_sort", table_name="design_tasks")
    op.drop_table("design_tasks")
