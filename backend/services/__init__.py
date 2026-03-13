"""
Services package — business logic layer.

Routers handle HTTP concerns (request/response, validation).
Services handle business logic (data processing, API calls, calculations).

NOTE: Do NOT add eager imports here. Each module should be imported directly:
    from backend.services.refs_service import ...
    from backend.services.funnel.wb_api_client import ...
Eager imports cause scheduler/Redis to start, hanging docker exec scripts.
"""
