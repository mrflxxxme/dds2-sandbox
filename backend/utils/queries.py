"""
Project-scoped query helpers for DDS.

WHY THIS EXISTS:
- Forgetting .where(Model.project_id == project_id) causes data leaks
  between tenants (multi-tenant bug).
- These helpers make it IMPOSSIBLE to forget project_id.

RULE: All new code MUST use project_select() instead of raw select()
      for any model that has a project_id column.
"""

from sqlalchemy import select, Select


def project_select(model, project_id: int) -> Select:
    """SELECT with mandatory project_id filter.

    Use this INSTEAD of select(Model) for any model with project_id.
    Makes it impossible to forget tenant isolation.

    Usage:
        from backend.utils.queries import project_select

        # Instead of: select(WbPayout).where(WbPayout.project_id == project_id)
        # Write:      project_select(WbPayout, project_id)

        result = await db.execute(project_select(WbPayout, project_id))

    Raises:
        AttributeError: if the model doesn't have a project_id column.
    """
    if not hasattr(model, "project_id"):
        raise AttributeError(
            f"{model.__name__} has no 'project_id' column. "
            f"Use regular select() for non-tenant models, "
            f"or add project_id to the model."
        )
    return select(model).where(model.project_id == project_id)


def project_select_active(model, project_id: int) -> Select:
    """SELECT with project_id + is_deleted=False filter.

    For models that use SoftDeleteMixin. Automatically excludes
    soft-deleted rows.

    Usage:
        from backend.utils.queries import project_select_active

        result = await db.execute(project_select_active(Order, project_id))
    """
    q = project_select(model, project_id)
    if hasattr(model, "is_deleted"):
        q = q.where(model.is_deleted == False)  # noqa: E712 — SQLAlchemy requires ==
    return q
