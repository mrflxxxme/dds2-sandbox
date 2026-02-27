"""
API client for the FastAPI backend.
"""

import os
import requests
from typing import Any, Optional

API_URL = os.getenv("API_URL", "http://localhost:8000")


def _get(path: str, params: dict = None) -> Any:
    r = requests.get(f"{API_URL}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def _post(path: str, json: dict = None, files=None, data=None) -> Any:
    if files:
        r = requests.post(f"{API_URL}{path}", files=files, data=data, timeout=60)
    else:
        r = requests.post(f"{API_URL}{path}", json=json, timeout=30)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise Exception(f"{r.status_code}: {detail}")
    return r.json()


def _delete(path: str) -> Any:
    r = requests.delete(f"{API_URL}{path}", timeout=15)
    r.raise_for_status()
    return r.json()


# ─── Import ────────────────────────────────────────────────────────────────

def upload_statement(file_bytes: bytes, filename: str, source_type: str, account_no: str):
    return _post(
        "/api/import/upload",
        files={"file": (filename, file_bytes, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        data={"source_type": source_type, "account_no": account_no},
    )


def get_import_logs():
    return _get("/api/import/logs")


# ─── Transactions ──────────────────────────────────────────────────────────

def search_transactions(filters: dict):
    return _post("/api/transactions/search", json=filters)


def get_unassigned(limit: int = 200):
    return _get("/api/transactions/unassigned", {"limit": limit})


def assign_category(txn_id: str, cat_lvl1: str, cat_lvl2: str,
                    scope: str = "txn", comment: str = None, cp_key: str = None):
    return _post("/api/transactions/assign_category", json={
        "txn_id": txn_id,
        "cat_lvl1": cat_lvl1,
        "cat_lvl2": cat_lvl2,
        "scope": scope,
        "comment": comment,
        "cp_key": cp_key,
    })


# ─── Refs ──────────────────────────────────────────────────────────────────

def get_accounts():
    return _get("/api/refs/accounts")


def upsert_account(data: dict):
    return _post("/api/refs/accounts", json=data)


def delete_account(account_id: int):
    return _delete(f"/api/refs/accounts/{account_id}")


def get_cp_categories():
    return _get("/api/refs/cp_categories")


def upsert_cp_category(data: dict):
    return _post("/api/refs/cp_categories", json=data)


def delete_cp_category(cpc_id: int):
    return _delete(f"/api/refs/cp_categories/{cpc_id}")


def get_overrides():
    return _get("/api/refs/overrides")


def delete_override(override_id: int):
    return _delete(f"/api/refs/overrides/{override_id}")


def get_opening_balances():
    return _get("/api/refs/opening_balances")


def upsert_opening_balance(data: dict):
    return _post("/api/refs/opening_balances", json=data)


# ─── Reports ───────────────────────────────────────────────────────────────

def get_balance(as_of: str = None):
    params = {}
    if as_of:
        params["as_of"] = as_of
    return _get("/api/reports/balance", params)


def get_dds_month(year: int, month: int, currency: str = "RUB"):
    return _get("/api/reports/dds_month", {"year": year, "month": month, "currency": currency})


def get_fx_control(date_from: str = None, date_to: str = None):
    params = {}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    return _get("/api/reports/fx_control", params)


def get_customs_control(date_from: str = None, date_to: str = None):
    params = {}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    return _get("/api/reports/customs_control", params)


def get_balance_daily(account: str, currency: str, date_from: str = None, date_to: str = None):
    params = {"account": account, "currency": currency}
    if date_from:
        params["date_from"] = date_from
    if date_to:
        params["date_to"] = date_to
    return _get("/api/reports/balance_daily", params)


# ─── Planning ──────────────────────────────────────────────────────────────

def get_orders():
    return _get("/api/planning/orders")


def upsert_order(data: dict):
    return _post("/api/planning/orders", json=data)


def delete_order(order_id: int):
    return _delete(f"/api/planning/orders/{order_id}")


def get_lead_times():
    return _get("/api/planning/lead_times")


def upsert_lead_time(data: dict):
    return _post("/api/planning/lead_times", json=data)


def get_payments(order_no: int = None):
    params = {}
    if order_no:
        params["order_no"] = order_no
    return _get("/api/planning/payments", params)


def upsert_payment(data: dict):
    return _post("/api/planning/payments", json=data)


def delete_payment(payment_id: int):
    return _delete(f"/api/planning/payments/{payment_id}")


def mark_payment_paid(payment_id: int):
    return _post(f"/api/planning/payments/{payment_id}/mark_paid")


def get_incomes():
    return _get("/api/planning/incomes")


def upsert_income(data: dict):
    return _post("/api/planning/incomes", json=data)


def delete_income(income_id: int):
    return _delete(f"/api/planning/incomes/{income_id}")


def get_customs_topup():
    return _get("/api/planning/customs/topup")


def get_customs_alloc(topup_txn_id: str = None):
    params = {}
    if topup_txn_id:
        params["topup_txn_id"] = topup_txn_id
    return _get("/api/planning/customs/alloc", params)


def create_customs_alloc(data: dict):
    return _post("/api/planning/customs/alloc", json=data)


def delete_customs_alloc(alloc_id: int):
    return _delete(f"/api/planning/customs/alloc/{alloc_id}")


def get_cashflow_daily(days: int = 60, starting_balance: float = 0.0):
    return _get("/api/planning/cashflow_daily", {"days": days, "starting_balance": starting_balance})


def get_order_summary(order_no: int):
    return _get(f"/api/planning/orders/{order_no}/summary")


def seed_defaults():
    return _post("/api/seed")


def get_income_daily(year: int, month: int, currency: str = "RUB"):
    return _get("/api/reports/income_daily", {"year": year, "month": month, "currency": currency})


def get_income_by_category_daily(year: int, month: int, currency: str = "RUB"):
    return _get("/api/reports/income_by_category_daily", {"year": year, "month": month, "currency": currency})


# ─── Cost / Себестоимость ─────────────────────────────────────────────────────

def get_nomenclature():
    return _get("/api/cost/nomenclature") or []

def upload_nomenclature(file_bytes: bytes, filename: str):
    import requests
    url = f"{API_URL}/api/cost/nomenclature/upload"
    r = requests.post(url, files={"file": (filename, file_bytes)}, timeout=60)
    r.raise_for_status()
    return r.json()

def get_duty_rules():
    return _get("/api/cost/duty_rules") or []

def upsert_duty_rule(payload: dict):
    return _post("/api/cost/duty_rules", payload)

def delete_duty_rule(rule_id: int):
    import requests
    r = requests.delete(f"{API_URL}/api/cost/duty_rules/{rule_id}", timeout=30)
    r.raise_for_status()
    return r.json()

def get_cost_orders():
    return _get("/api/cost/orders") or []

def create_cost_order(payload: dict):
    return _post("/api/cost/orders", payload)

def delete_cost_order(order_no: str):
    import requests
    r = requests.delete(f"{API_URL}/api/cost/orders/{order_no}", timeout=30)
    r.raise_for_status()
    return r.json()

def get_cost_order_items(order_no: str):
    return _get(f"/api/cost/orders/{order_no}/items") or []

def upload_cost_order_items(order_no: str, file_bytes: bytes, filename: str):
    import requests
    url = f"{API_URL}/api/cost/orders/{order_no}/upload"
    r = requests.post(url, files={"file": (filename, file_bytes)}, timeout=60)
    r.raise_for_status()
    return r.json()


def generate_cost_plan(order_no: str):
    return _post(f"/api/cost/orders/{order_no}/generate_plan", {})

def update_cost_order(order_no: str, payload: dict):
    import requests
    r = requests.put(f"{API_URL}/api/cost/orders/{order_no}", json=payload, timeout=30)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise Exception(f"{r.status_code}: {detail}")
    return r.json()

def sync_plan_payments():
    return _post("/api/planning/sync_plan_payments", {})

def get_candidate_transactions(direction: str, account: str = None):
    params = f"direction={direction}"
    if account:
        params += f"&account={account}"
    return _get(f"/api/planning/candidate_transactions?{params}") or []

def get_accounts_list():
    return _get("/api/planning/accounts_list") or []

def get_fact_links(payment_id: int):
    return _get(f"/api/planning/fact_links/{payment_id}") or []

def create_fact_link(payload: dict):
    return _post("/api/planning/fact_links", payload)

def delete_fact_link(link_id: int):
    import requests
    r = requests.delete(f"{API_URL}/api/planning/fact_links/{link_id}", timeout=30)
    r.raise_for_status()
    return r.json()

# ─── Customs DT ───────────────────────────────────────────────────────────────

def upload_fts_report(file_bytes: bytes, filename: str):
    import requests
    r = requests.post(
        f"{API_URL}/api/planning/customs_dt/upload_fts",
        files={"file": (filename, file_bytes)}, timeout=60
    )
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except Exception:
            detail = r.text
        raise Exception(f"{r.status_code}: {detail}")
    return r.json()

def get_customs_dt_list():
    return _get("/api/planning/customs_dt") or []

def update_customs_dt(dt_id: int, payload: dict):
    import requests
    r = requests.put(f"{API_URL}/api/planning/customs_dt/{dt_id}", json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def delete_customs_dt(dt_id: int):
    import requests
    r = requests.delete(f"{API_URL}/api/planning/customs_dt/{dt_id}", timeout=30)
    r.raise_for_status()
    return r.json()

# ─── Bulk categorization ──────────────────────────────────────────────────────

def get_unassigned_grouped():
    return _get("/api/transactions/unassigned_grouped") or []

def assign_category_bulk(payload: dict):
    return _post("/api/transactions/assign_category_bulk", payload)

# ─── Category Reference ───────────────────────────────────────────────────────

def get_category_ref():
    return _get("/api/refs/categories") or []

def add_category_ref(payload: dict):
    return _post("/api/refs/categories", payload)

def delete_category_ref(cat_id: int):
    import requests
    r = requests.delete(f"{API_URL}/api/refs/categories/{cat_id}", timeout=30)
    r.raise_for_status()
    return r.json()
