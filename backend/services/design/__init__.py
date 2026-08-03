# ruff: noqa: RUF002, RUF003
"""Сервис-ядро модуля «Дизайн карточек» (Ф1).

Модули: common (общие хелперы: скоуп задачи, роли, имена), state (переходы под
FOR UPDATE), permissions (матрица PRD §7), crud, queries (read-side), board
(Р3/Р4), files (MinIO), workload, stats, notify (личные TG + утренняя сводка,
Ф4), ab_bridge (мост в АБ-тесты, Ф6).
HTTP-слой — Ф2; роутер и фронт опираются ТОЛЬКО на permissions (инвариант §6.9).

Соглашение об ошибках (единое для пакета, маппинг в HTTP — Ф2):
- ValueError — невалидная операция/не найдено в скоупе проекта (→ 400/404);
- PermissionError — матрица прав запрещает действие (→ 403);
- fastapi.HTTPException — только в files.py (валидация файлов/MinIO, донор
  payment_request_documents).
"""

from backend.services.design import (  # noqa: F401
    ab_bridge,
    board,
    common,
    crud,
    files,
    notify,
    permissions,
    queries,
    state,
    stats,
    workload,
)
