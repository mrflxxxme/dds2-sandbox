"""
DEPRECATED: Use backend.project_context instead.

This module re-exports get_project_id for backward compatibility.
The actual implementation is in project_context.py.
"""

from backend.project_context import get_current_project
from backend.models import Project
from fastapi import Depends


async def get_project_id(
    project: Project = Depends(get_current_project),
) -> int:
    """
    Extract and validate project_id from request.
    Delegates to get_current_project and returns just the ID.

    DEPRECATED: Use get_current_project() from project_context.py directly.
    """
    return project.id

