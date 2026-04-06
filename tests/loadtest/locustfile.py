"""
DDS Load Tests — locust
Сценарии: concurrent reports, dashboard, import simulation, WB sync.

Запуск:
  pip install locust
  locust -f tests/loadtest/locustfile.py --host=http://localhost

  # headless (10 users, 60s):
  locust -f tests/loadtest/locustfile.py --host=http://localhost \
    --users 10 --spawn-rate 2 --run-time 60s --headless
"""

import os
import random

import requests
from locust import HttpUser, between, events, task

# ─── Config ──────────────────────────────────────────────────────
USERNAME = os.environ.get("LOAD_TEST_USER", "admin")
PASSWORD = os.environ.get("LOAD_TEST_PASS", "admin")
PROJECT_ID = os.environ.get("LOAD_TEST_PROJECT_ID", "auto")
HOST = os.environ.get("LOAD_TEST_HOST", "http://localhost")

# ─── Shared token (login once, share across all users) ───────────
# Avoids nginx rate limit (5r/m on /api/v1/auth/)
_shared_token = None  # type: str | None
_project_id = PROJECT_ID


def get_shared_token() -> str:
    global _shared_token
    if _shared_token is None:
        resp = requests.post(
            f"{HOST}/api/v1/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
            timeout=10,
        )
        resp.raise_for_status()
        _shared_token = resp.json()["access_token"]
    return _shared_token


def get_project_id() -> str:
    global _project_id
    if _project_id == "auto":
        token = get_shared_token()
        resp = requests.get(
            f"{HOST}/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        resp.raise_for_status()
        projects = resp.json()
        if projects:
            _project_id = str(projects[0]["id"])
        else:
            raise RuntimeError("No projects found — create one first")
    return _project_id


@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Pre-authenticate and resolve project before spawning users."""
    get_shared_token()
    pid = get_project_id()
    print(f"[load-test] Authenticated as {USERNAME}, project_id={pid}")


class DdsUser(HttpUser):
    """Simulates a typical DDS user: views dashboard, reports, transactions."""

    wait_time = between(1, 3)
    weight = 3

    def on_start(self):
        self.headers = {
            "Authorization": f"Bearer {get_shared_token()}",
            "X-Project-Id": get_project_id(),
        }

    # ─── Scenario 1: Dashboard & Reports (high frequency) ────────

    @task(5)
    def dashboard_summary(self):
        """GET /reports/dashboard_summary — main dashboard KPIs."""
        self.client.get(
            "/api/v1/reports/dashboard_summary",
            params={"date_from": "2024-01-01", "date_to": "2024-12-31"},
            headers=self.headers,
            name="/reports/dashboard_summary",
        )

    @task(3)
    def balance(self):
        """GET /reports/balance — account balances."""
        self.client.get(
            "/api/v1/reports/balance",
            headers=self.headers,
            name="/reports/balance",
        )

    @task(2)
    def dds_month(self):
        """GET /reports/dds_month — monthly cash flow report."""
        month = random.randint(1, 12)  # noqa: S311
        self.client.get(
            "/api/v1/reports/dds_month",
            params={"year": 2024, "month": month, "currency": "RUB"},
            headers=self.headers,
            name="/reports/dds_month",
        )

    @task(2)
    def transactions_search(self):
        """POST /transactions/search — paginated transaction list."""
        self.client.post(
            "/api/v1/transactions/search",
            json={"date_from": "2024-01-01", "date_to": "2024-12-31", "limit": 50},
            headers=self.headers,
            name="/transactions/search",
        )

    # ─── Scenario 2: Report generation (medium frequency) ────────

    @task(1)
    def wb_bdr(self):
        """GET /reports/wb_bdr — WB P&L report (slow, complex query)."""
        self.client.get(
            "/api/v1/reports/wb_bdr",
            params={
                "date_from": "2024-01-01",
                "date_to": "2024-03-31",
                "group_by": "article",
            },
            headers=self.headers,
            name="/reports/wb_bdr",
            timeout=70,
        )

    @task(1)
    def balance_daily(self):
        """GET /reports/balance_daily — daily balance trend."""
        self.client.get(
            "/api/v1/reports/balance_daily",
            params={"date_from": "2024-01-01", "date_to": "2024-03-31", "currency": "RUB"},
            headers=self.headers,
            name="/reports/balance_daily",
        )

    @task(1)
    def fx_control(self):
        """GET /reports/fx_control — currency control report."""
        self.client.get(
            "/api/v1/reports/fx_control",
            params={"date_from": "2024-01-01", "date_to": "2024-03-31"},
            headers=self.headers,
            name="/reports/fx_control",
        )

    # ─── Scenario 3: Import simulation (low frequency) ───────────

    @task(1)
    def import_logs(self):
        """GET /import/logs — list recent imports."""
        self.client.get(
            "/api/v1/import/logs",
            headers=self.headers,
            name="/import/logs",
        )


class WbSyncUser(HttpUser):
    """Simulates WB sync monitoring — lower frequency."""

    wait_time = between(5, 15)
    weight = 1

    def on_start(self):
        self.headers = {
            "Authorization": f"Bearer {get_shared_token()}",
            "X-Project-Id": get_project_id(),
        }

    @task(2)
    def sync_status(self):
        """GET /integrations/sync_log — check sync status."""
        self.client.get(
            "/api/v1/integrations/sync_log",
            headers=self.headers,
            name="/integrations/sync_log",
        )

    @task(1)
    def stock_analytics(self):
        """GET /reports/stock_analytics — warehouse analytics."""
        self.client.get(
            "/api/v1/reports/stock_analytics",
            params={"trend_days": 7},
            headers=self.headers,
            name="/reports/stock_analytics",
        )

    @task(1)
    def funnel_summary(self):
        """GET /reports/funnel_summary — sales funnel."""
        self.client.get(
            "/api/v1/reports/funnel_summary",
            params={"date_from": "2024-01-01", "date_to": "2024-03-31"},
            headers=self.headers,
            name="/reports/funnel_summary",
        )
