"""
API client for the FastAPI backend.
"""

import os
import requests
from typing import Any, Optional

API_URL = os.getenv("API_URL", "http://localhost:8000")

# Token storage: set by Streamlit app via set_token()
_token: Optional[str] = None


def set_token(token: Optional[str]):
    global _token
    _token = token


def _get_headers() -> dict:
    headers = {}
    if _token:
        headers["Authorization"] = f"Bearer {_token}"
    return headers


def _get(path: str, params: dict = None) -> Any:
    r = requests.get(f"{API_URL}{path}", params=params, headers=_get_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, json: dict = None, files=None, data=None) -> Any:
    if files:
        r = requests.post(f"{API_URL}{path}", files=files, data=data, headers=_get_headers(), timeout=60)
    else:
        r = requests.post(f"{API_URL}{path}", json=json, headers=_get_headers(), timeout=30)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise Exception(f"{r.status_code}: {detail}")
    return r.json()


def _put(path: str, json: dict = None) -> Any:
    r = requests.put(f"{API_URL}{path}", json=json, headers=_get_headers(), timeout=30)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise Exception(f"{r.status_code}: {detail}")
    return r.json()


def _delete(path: str) -> Any:
    r = requests.delete(f"{API_URL}{path}", headers=_get_headers(), timeout=15)
    r.raise_for_status()
    return r.json()


# ─── Auth ─────────────────────────────────────────────────────────────────

def login(username: str, password: str) -> dict:
    """Login and return {access_token, token_type}."""
    r = requests.post(f"{API_URL}/api/v1/auth/login", json={
        "username": username,
        "password": password,
    }, timeout=15)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise Exception(detail)
    return r.json()


# ─── Import ────────────────────────────────────────────────────────────────

def upload_statement(file_bytes: bytes, filename: str, source_type: str, account_no: str):
    return _post(
        "/api/v1/import/upload",
        files={"file": (filename, file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"source_type": source_type, "account_no": account_no},
    )


def get_import_logs():
    return _get("/api/v1/import/logs")


# ─── Transactions ──────────────────────────────────────────────────────────

def search_transactions(filters: dict):
    return _post("/api/v1/transactions/search", json=filters)


def get_unassigned(limit: int = 200):
    return _get("/api/v1/transactions/unassigned", {"limit": limit})


def assign_category(txn_id: str, cat_lvl1: str, cat_lvl2: str,
                    scope: str = "txn", comment: str = None, cp_key: str = None):
    return _post("/api/v1/transactions/assign_category", json={
        "txn_id": txn_id,
        "cat_lvl1": cat_lvl1,
        "cat_lvl2": cat_lvl2,
        "scope": scope,
        "comment": comment,
        "cp_key": cp_key,
    })


# ─── Refs ──────────────────────────────────────────────────────────────────

def get_accounts():
    return _get("/api/v1/refs/accounts")


def upsert_account(data: dict):
    return _post("/api/v1/refs/accounts", json=data)


def delete_account(account_id: int):
    return _delete(f"/api/v1/refs/accounts/{account_id}")


def get_cp_categories():
    return _get("/api/v1/refs/cp_categories")


def upsert_cp_category(data: dict):
    return _post("/api/v1/refs/cp_categories", json=data)


def delete_cp_category(cpc_id: int):
    return _delete(f"/api/v1/refs/cp_categories/{cpc_id}")


def get_overrides():
    return _get("/api/v1/refs/overrides")


def delete_override(override_id: int):
    return _delete(f"/api/v1/refs/overrides/{override_id}")


def get_opening_balances():
    return _get("/api/v1/refs/opening_balances")


def upsert_opening_balance(data: dict):
    return _post("/api/v1/refs/opening_balances", json=data)


# ─── Reports ───────────────────────────────────────────────────────────────

def get_balance(as_of: str = None):
    params = {}
    if as_of:
        params["as_of"] = as_of
    return _get("/api/v1/reports/balance", params)


def get_dds_month(year: int, month: int, currency: str = "RUB"):
    return _get("/api/v1/reports/dds_month", {"year": year, "month": month, "currency": currency})


def get_fx_control(date_from: str = None, date_to: str = None):
    params = {}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    return _get("/api/v1/reports/fx_control", params)


def get_customs_control(date_from: str = None, date_to: str = None):
    params = {}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    return _get("/api/v1/reports/customs_control", params)


def get_balance_daily(account: str, currency: str, date_from: str = None, date_to: str = None):
    params = {"account": account, "currency": currency}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    return _get("/api/v1/reports/balance_daily", params)


# ─── Planning ──────────────────────────────────────────────────────────────

def get_orders():
    return _get("/api/v1/planning/orders")


def upsert_order(data: dict):
    return _post("/api/v1/planning/orders", json=data)


def delete_order(order_id: int):
    return _delete(f"/api/v1/planning/orders/{order_id}")


def get_lead_times():
    return _get("/api/v1/planning/lead_times")


def upsert_lead_time(data: dict):
    return _post("/api/v1/planning/lead_times", json=data)


def get_payments(order_no: int = None):
    params = {}
    if order_no:
        params["order_no"] = order_no
    return _get("/api/v1/planning/payments", params)


def upsert_payment(data: dict):
    return _post("/api/v1/planning/payments", json=data)


def delete_payment(payment_id: int):
    return _delete(f"/api/v1/planning/payments/{payment_id}")


def mark_payment_paid(payment_id: int):
    return _post(f"/api/v1/planning/payments/{payment_id}/mark_paid")


def get_incomes():
    return _get("/api/v1/planning/incomes")


def upsert_income(data: dict):
    return _post("/api/v1/planning/incomes", json=data)


def delete_income(income_id: int):
    return _delete(f"/api/v1/planning/incomes/{income_id}")


def get_customs_topup():
    return _get("/api/v1/planning/customs/topup")


def get_customs_alloc(topup_txn_id: str = None):
    params = {}
    if topup_txn_id:
        params["topup_txn_id"] = topup_txn_id
    return _get("/api/v1/planning/customs/alloc", params)


def create_customs_alloc(data: dict):
    return _post("/api/v1/planning/customs/alloc", json=data)


def delete_customs_alloc(alloc_id: int):
    return _delete(f"/api/v1/planning/customs/alloc/{alloc_id}")


def get_cashflow_daily(days: int = 60, starting_balance: float = 0.0):
    return _get("/api/v1/planning/cashflow_daily", {"days": days, "starting_balance": starting_balance})


def get_order_summary(order_no: int):
    return _get(f"/api/v1/planning/orders/{order_no}/summary")


def seed_defaults():
    return _post("/api/v1/seed")


def get_income_daily(year: int, month: int, currency: str = "RUB"):
    return _get("/api/v1/reports/income_daily", {"year": year, "month": month, "currency": currency})


def get_income_by_category_daily(year: int, month: int, currency: str = "RUB", cat_lvl1: str = None):
    params = {"year": year, "month": month, "currency": currency}
    if cat_lvl1:
        params["cat_lvl1"] = cat_lvl1
    return _get("/api/v1/reports/income_by_category_daily", params)


# ─── Cost / Себестоимость ─────────────────────────────────────────────────────

def get_nomenclature():
    return _get("/api/v1/cost/nomenclature") or []

def upload_nomenclature(file_bytes: bytes, filename: str):
    return _post(
        "/api/v1/cost/nomenclature/upload",
        files={"file": (filename, file_bytes)},
    )

def get_duty_rules():
    return _get("/api/v1/cost/duty_rules") or []

def upsert_duty_rule(payload: dict):
    return _post("/api/v1/cost/duty_rules", payload)

def delete_duty_rule(rule_id: int):
    return _delete(f"/api/v1/cost/duty_rules/{rule_id}")

def get_cost_orders():
    return _get("/api/v1/cost/orders") or []

def create_cost_order(payload: dict):
    return _post("/api/v1/cost/orders", payload)

def delete_cost_order(order_no: str):
    return _delete(f"/api/v1/cost/orders/{order_no}")

def get_cost_order_items(order_no: str):
    return _get(f"/api/v1/cost/orders/{order_no}/items") or []

def upload_cost_order_items(order_no: str, file_bytes: bytes, filename: str):
    return _post(
        f"/api/v1/cost/orders/{order_no}/upload",
        files={"file": (filename, file_bytes)},
    )

def generate_cost_plan(order_no: str):
    return _post(f"/api/v1/cost/orders/{order_no}/generate_plan", {})

def update_cost_order(order_no: str, payload: dict):
    return _put(f"/api/v1/cost/orders/{order_no}", json=payload)

def sync_plan_payments():
    return _post("/api/v1/planning/sync_plan_payments", {})

def get_candidate_transactions(direction: str, account: str = None):
    params = {"direction": direction}
    if account:
        params["account"] = account
    return _get("/api/v1/planning/candidate_transactions", params) or []

def get_accounts_list():
    return _get("/api/v1/planning/accounts_list") or []

def get_fact_links(payment_id: int):
    return _get(f"/api/v1/planning/fact_links/{payment_id}") or []

def create_fact_link(payload: dict):
    return _post("/api/v1/planning/fact_links", payload)

def delete_fact_link(link_id: int):
    return _delete(f"/api/v1/planning/fact_links/{link_id}")

# ─── Customs DT ───────────────────────────────────────────────────────────────

def upload_fts_report(file_bytes: bytes, filename: str):
    return _post(
        "/api/v1/planning/customs_dt/upload_fts",
        files={"file": (filename, file_bytes)},
    )

def get_customs_dt_list():
    return _get("/api/v1/planning/customs_dt") or []

def update_customs_dt(dt_id: int, payload: dict):
    return _put(f"/api/v1/planning/customs_dt/{dt_id}", json=payload)

def delete_customs_dt(dt_id: int):
    return _delete(f"/api/v1/planning/customs_dt/{dt_id}")

# ─── WB Payouts ──────────────────────────────────────────────────────────────

def upload_wb_payouts(file_bytes: bytes, filename: str):
    return _post(
        "/api/v1/planning/wb_payouts/upload",
        files={"file": (filename, file_bytes,
               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

def get_wb_payouts(status: str = None):
    params = {}
    if status:
        params["status"] = status
    return _get("/api/v1/planning/wb_payouts", params) or []

def delete_wb_payout(payout_id: int):
    return _delete(f"/api/v1/planning/wb_payouts/{payout_id}")

def manual_reconcile_wb(payout_id: int, txn_id: str):
    return _post(f"/api/v1/planning/wb_payouts/{payout_id}/reconcile",
                 json={"txn_id": txn_id})

def refresh_wb_forecast():
    return _post("/api/v1/planning/wb_forecast/refresh", json={})


# ─── Bulk categorization ──────────────────────────────────────────────────────

def get_unassigned_grouped():
    return _get("/api/v1/transactions/unassigned_grouped") or []

def assign_category_bulk(payload: dict):
    return _post("/api/v1/transactions/assign_category_bulk", payload)

def assign_category_by_ids(payload: dict):
    return _post("/api/v1/transactions/assign_category_by_ids", payload)

# ─── Category Reference ───────────────────────────────────────────────────────

def get_category_ref():
    return _get("/api/v1/refs/categories") or []

def add_category_ref(payload: dict):
    return _post("/api/v1/refs/categories", payload)

def delete_category_ref(cat_id: int):
    return _delete(f"/api/v1/refs/categories/{cat_id}")
