"""
Seed script: imports reference data and transactions from the original Excel files.
Run once after docker-compose up:
  python scripts/seed_from_excel.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pandas as pd
from decimal import Decimal
from datetime import datetime
import requests

API_URL = os.getenv("API_URL", "http://localhost:8000")


def post(path, data):
    r = requests.post(f"{API_URL}{path}", json=data)
    if not r.ok:
        print(f"  ERROR {r.status_code}: {r.text[:200]}")
    return r


def seed_defaults():
    print("Seeding default accounts and lead times...")
    r = requests.post(f"{API_URL}/api/seed")
    print(f"  → {r.json()}")


def seed_from_excel(pipeline_file: str):
    print(f"\nLoading reference data from {pipeline_file}...")
    xl = pd.ExcelFile(pipeline_file)

    # ── REF_ACCOUNTS ──────────────────────────────────────────────────────────
    print("  Uploading REF_ACCOUNTS...")
    df_acc = pd.read_excel(pipeline_file, sheet_name="REF_ACCOUNTS")
    for _, row in df_acc.iterrows():
        acc = str(row.get("account", "")).strip()
        if not acc:
            continue
        data = {
            "account": acc,
            "bank": str(row.get("bank", "")),
            "currency": str(row.get("currency", "RUB")),
            "account_type": str(row.get("account_type", "OPER")) if pd.notna(row.get("account_type")) else None,
            "is_our_account": bool(row.get("is_our_account", True)),
            "account_name": str(row.get("account_name", "")) if pd.notna(row.get("account_name")) else None,
            "is_customs_payee": str(row.get("account_type", "")).strip() == "CUSTOMS_PAYEE",
        }
        post("/api/refs/accounts", data)
    print("    Done.")

    # ── REF_CP_CATEGORY ───────────────────────────────────────────────────────
    print("  Uploading REF_CP_CATEGORY...")
    df_cp = pd.read_excel(pipeline_file, sheet_name="REF_CP_CATEGORY")
    for _, row in df_cp.iterrows():
        key = str(row.get("cp_key", "")).strip()
        if not key or key == "nan":
            continue
        data = {
            "cp_key": key,
            "cp_name": str(row.get("cp_name", "")) if pd.notna(row.get("cp_name")) else None,
            "cat_lvl1": str(row.get("cat_lvl1", "")) if pd.notna(row.get("cat_lvl1")) else None,
            "cat_lvl2": str(row.get("cat_lvl2", "")) if pd.notna(row.get("cat_lvl2")) else None,
            "note": str(row.get("note", "")) if pd.notna(row.get("note")) else None,
        }
        post("/api/refs/cp_categories", data)
    print("    Done.")


def seed_planning(planning_file: str):
    print(f"\nLoading planning data from {planning_file}...")

    # ── Orders (отправки) ────────────────────────────────────────────────────
    print("  Uploading orders (отправки)...")
    df_orders = pd.read_excel(planning_file, sheet_name="отправки")
    for _, row in df_orders.iterrows():
        order_no = row.get("Номер")
        if pd.isna(order_no):
            continue

        def safe_date(v):
            if pd.isna(v) or v is None:
                return None
            try:
                return str(pd.to_datetime(v).date())
            except Exception:
                return None

        def safe_float(v):
            if pd.isna(v) or v is None:
                return None
            try:
                return float(v)
            except Exception:
                return None

        data = {
            "order_name": str(row.get("Название заказа ", "")).strip() if pd.notna(row.get("Название заказа ")) else None,
            "category": str(row.get("категория", "")).strip() if pd.notna(row.get("категория")) else None,
            "transport_type": str(row.get("тип \nтранспорта", "")).strip() if pd.notna(row.get("тип \nтранспорта")) else None,
            "order_no": int(order_no),
            "supplier": str(row.get("Поставщик", "")).strip() if pd.notna(row.get("Поставщик")) else None,
            "planned_ship_date": safe_date(row.get("План отгрузки")),
            "actual_ship_date": safe_date(row.get("Факт отгрузки")),
            "order_amount": safe_float(row.get("Сумма заказа")),
            "deposit": safe_float(row.get("депозит")),
            "logistics_cny": safe_float(row.get("Доставка - юань")),
            "customs_rub": safe_float(row.get("Таможня - РУБ")),
        }
        post("/api/planning/orders", data)
    print("    Done.")

    # ── Lead Times ───────────────────────────────────────────────────────────
    print("  Uploading lead times (Справочник)...")
    df_lt = pd.read_excel(planning_file, sheet_name="Справочник")
    direction_map = {"заказ": "ORDER", "авто": "AUTO", "контейнер": "CONTAINER", "таможня": "CUSTOMS"}
    for _, row in df_lt.iterrows():
        direction = str(row.get("Направление", "")).strip().lower()
        days = row.get("дней")
        if not direction or pd.isna(days):
            continue
        mapped = direction_map.get(direction, direction.upper())
        post("/api/planning/lead_times", {"direction": mapped, "days": int(days)})
    print("    Done.")

    # ── Planned Payments (движение) ──────────────────────────────────────────
    print("  Uploading planned payments (движение)...")
    df_pay = pd.read_excel(planning_file, sheet_name="движение")
    for _, row in df_pay.iterrows():
        pay_date = row.get("Дата оплаты")
        if pd.isna(pay_date):
            continue
        try:
            pay_date_str = str(pd.to_datetime(pay_date).date())
        except Exception:
            continue

        order_no = row.get("Номер заказа")
        direction = row.get("Направление", "")
        amount = row.get("Сумма оплаты")
        currency = row.get("Валюта (опц)")
        fx = row.get("курс")
        amount_rub = row.get("сумма_в_рублях")

        data = {
            "pay_date": pay_date_str,
            "order_no": int(order_no) if pd.notna(order_no) else None,
            "direction": str(direction) if pd.notna(direction) else None,
            "amount": float(amount) if pd.notna(amount) else None,
            "currency": str(currency) if pd.notna(currency) else None,
            "fx_rate": float(fx) if pd.notna(fx) else None,
            "amount_rub": float(amount_rub) if pd.notna(amount_rub) else None,
        }
        post("/api/planning/payments", data)
    print("    Done.")

    # ── WB Incomes ──────────────────────────────────────────────────────────
    print("  Uploading WB incomes (поступление вб)...")
    df_inc = pd.read_excel(planning_file, sheet_name="поступление вб")
    for _, row in df_inc.iterrows():
        d = row.get("Дата")
        amt = row.get("поступление")
        if pd.isna(d) or pd.isna(amt):
            continue
        try:
            date_str = str(pd.to_datetime(d).date())
        except Exception:
            continue
        post("/api/planning/incomes", {"date": date_str, "amount_rub": float(amt), "source": "WB"})
    print("    Done.")


def seed_customs(dashboard_file: str):
    print(f"\nLoading customs data from {dashboard_file}...")

    # ── CUSTOMS_TOPUP → need to be seeded via transactions, but we can also direct-insert
    # Actually these come from transaction imports. But we can seed allocs directly.
    print("  CUSTOMS_TOPUP is auto-populated from imported transactions.")
    print("  CUSTOMS_ALLOC needs to be entered manually or imported separately.")


if __name__ == "__main__":
    import time

    # Wait for backend
    for i in range(10):
        try:
            r = requests.get(f"{API_URL}/health", timeout=3)
            if r.ok:
                print("✅ Backend is up!")
                break
        except Exception:
            pass
        print(f"Waiting for backend... ({i+1}/10)")
        time.sleep(3)

    pipeline_file = os.path.join(os.path.dirname(__file__), "..", "data", "DDS_01_PIPELINE.xlsx")
    planning_file = os.path.join(os.path.dirname(__file__), "..", "data", "задолжность.xlsx")

    seed_defaults()

    if os.path.exists(pipeline_file):
        seed_from_excel(pipeline_file)
    else:
        print(f"\n⚠️  Pipeline file not found at {pipeline_file}")
        print("   Copy your Excel files to the data/ folder and re-run.")

    if os.path.exists(planning_file):
        seed_planning(planning_file)
    else:
        print(f"\n⚠️  Planning file not found at {planning_file}")

    print("\n✅ Seed complete! Open http://localhost:8501 to use the app.")
