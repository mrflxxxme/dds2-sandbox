"""vibe_authors + vibe_commits — статистика вайбкодинга по данным git

Revision ID: vibe01_vibecoding_stats
Revises: wbr01_wb_warehouse_remains
Create Date: 2026-07-17

Внутренняя телеметрия репозитория: что каждый вайбкодер выкатил на прод. Данные кладёт
CI после мёржа в main (git на проде недоступен — .dockerignore исключает .git).

Без project_id намеренно: это статистика по коду самого DDS2, а не по данным арендатора.
Доступ режется наличием строки в vibe_authors, не проектной ролью (клиент-селлер —
`owner` своего проекта и прошёл бы любую проверку по роли).
"""

import sqlalchemy as sa
from alembic import op

revision = "vibe01_vibecoding_stats"
down_revision = "wbr01_wb_warehouse_remains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vibe_authors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("git_email", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="vibe_authors_user_id_fkey"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("git_email", name="uq_vibe_authors_git_email"),
    )
    # FK обязан иметь индекс (canon).
    op.create_index("ix_vibe_authors_user_id", "vibe_authors", ["user_id"])

    op.create_table(
        "vibe_commits",
        sa.Column("sha", sa.String(length=40), nullable=False),
        sa.Column("author_email", sa.String(length=200), nullable=False),
        sa.Column("authored_on", sa.Date(), nullable=False),
        sa.Column("ctype", sa.String(length=20), nullable=False),
        sa.Column("scope", sa.String(length=50), server_default="", nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("added", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deleted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("files", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_product", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("ingested_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("sha"),
    )
    # Главный запрос вкладки: «мои поставки за период».
    op.create_index(
        "ix_vibe_commits_author_email_authored_on",
        "vibe_commits",
        ["author_email", "authored_on"],
    )

    # Файлы поставки: агрегата в vibe_commits не хватает для «Масштаба» — по нему не
    # посчитать уникальные файлы, новые файлы и разбивку по областям кода.
    op.create_table(
        "vibe_files",
        sa.Column("sha", sa.String(length=40), nullable=False),
        sa.Column("path", sa.String(length=500), nullable=False),
        sa.Column("added", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deleted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_new", sa.Boolean(), server_default="false", nullable=False),
        sa.ForeignKeyConstraint(
            ["sha"], ["vibe_commits.sha"], name="vibe_files_sha_fkey", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("sha", "path"),
    )
    # FK обязан иметь индекс; PK(sha, path) даёт префикс по sha — отдельный не нужен.


def downgrade() -> None:
    op.drop_table("vibe_files")
    op.drop_index("ix_vibe_commits_author_email_authored_on", table_name="vibe_commits")
    op.drop_table("vibe_commits")
    op.drop_index("ix_vibe_authors_user_id", table_name="vibe_authors")
    op.drop_table("vibe_authors")
