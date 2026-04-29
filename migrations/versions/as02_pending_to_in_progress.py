# ruff: noqa: RUF002, RUF003 — русские комментарии
"""assembly: миграция PENDING -> IN_PROGRESS

Убираем промежуточный статус «Ожидает сборки» (PENDING) из жизненного цикла
заявок на сборку. Новые заявки создаются сразу в IN_PROGRESS, а существующие
PENDING-заявки переводятся в IN_PROGRESS этой миграцией.

PENDING остаётся в enum для исторической совместимости (status_history может
содержать переходы из/в PENDING), но активных заявок в этом статусе быть не
должно.

Revision ID: as02_pending_to_in_progress
Revises: wh07_wb_returns_archived_at
Create Date: 2026-04-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "as02_pending_to_in_progress"
down_revision: str | None = "wh07_wb_returns_archived_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Обновляем активные заявки. Soft-deleted не трогаем — там данные «как было».
    op.execute(
        """
        UPDATE assembly_requests
        SET status = 'IN_PROGRESS'
        WHERE status = 'PENDING' AND is_deleted = false
        """
    )


def downgrade() -> None:
    # Откат не возвращает PENDING — без точной информации о времени старта это
    # потеря данных, и обратное направление нежелательно.
    pass
