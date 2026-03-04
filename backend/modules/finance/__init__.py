"""
Finance module — DDS reports, balance, cashflow.

This module will gradually absorb endpoints from:
- backend/routers/reports.py
- Related report services from funnel_service.py

Migration pattern:
1. Create service.py with business logic extracted from router
2. Create router.py importing the service
3. Register in main.py
4. Deprecate old endpoints with redirect
"""
