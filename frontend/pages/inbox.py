"""
INBOX: неразнесённые операции — назначение категорий.
"""

import streamlit as st
import pandas as pd
from frontend import api_client as api


def _load_categories():
    """Load categories from DB, split by direction."""
    try:
        cats = api.get_category_ref()
    except Exception:
        cats = []

    income_cats = {}
    expense_cats = {}
    for c in cats:
        d = income_cats if c["direction"] == "income" else expense_cats
        lvl1 = c["cat_lvl1"]
        if lvl1 not in d:
            d[lvl1] = []
        if c["cat_lvl2"] not in d[lvl1]:
            d[lvl1].append(c["cat_lvl2"])

    # Fallback if DB empty
    if not income_cats:
        income_cats = {
            "Маркетплейсы": ["Wildberries", "OZON", "Прочее"],
            "Банки": ["Проценты"],
            "Прочие доходы": ["Возврат", "Прочее"],
        }
    if not expense_cats:
        expense_cats = {
            "Поставщики": ["Оплата товара", "Депозит", "Прочее"],
            "Логистика": ["Доставка по РФ", "Доставка из Китая", "Таможня"],
            "Банки": ["Комиссии банка"],
            "Фулфилмент": ["Склад / упаковка", "Прочее"],
            "Прочие расходы": ["Офис", "IT", "Реклама", "Прочее"],
        }
    return income_cats, expense_cats


def render():
    st.title("🔴 INBOX — Неразнесённые операции")

    income_cats, expense_cats = _load_categories()
    all_cats = {**income_cats, **expense_cats}  # merged for single-txn mode
    all_cat1_list = list(dict.fromkeys(list(income_cats.keys()) + list(expense_cats.keys())))

    # ── Grouped by counterparty ──────────────────────────────────────────────
    try:
        grouped = api.get_unassigned_grouped()
    except Exception as e:
        st.error(f"Ошибка: {e}")
        grouped = []

    if not grouped:
        st.success("🎉 Все операции разнесены! INBOX пуст.")
        return

    df_g = pd.DataFrame(grouped)
    for col in ["total_income", "total_expense"]:
        df_g[col] = pd.to_numeric(df_g[col], errors="coerce").fillna(0)

    total_ops = df_g["count"].sum()
    total_inc = df_g["total_income"].sum()
    total_exp = df_g["total_expense"].sum()

    m1, m2, m3 = st.columns(3)
    m1.metric("Неразнесённых операций", f"{int(total_ops)}")
    m2.metric("Поступления", f"{total_inc:,.0f} ₽")
    m3.metric("Расходы", f"{total_exp:,.0f} ₽")

    st.markdown("---")

    # ── Bulk assignment by counterparty ───────────────────────────────────────
    st.subheader("⚡ Быстрая разноска по контрагентам")

    # Split into income and expense groups
    df_inc = df_g[df_g["total_income"] > 0].sort_values("total_income", ascending=False)
    df_exp = df_g[df_g["total_expense"] > 0].sort_values("total_expense", ascending=False)

    # Load all unassigned for detail views
    try:
        all_unassigned = api.get_unassigned(limit=1000)
        df_all = pd.DataFrame(all_unassigned) if all_unassigned else pd.DataFrame()
    except Exception:
        df_all = pd.DataFrame()

    tab_inc, tab_exp, tab_single = st.tabs(["📥 Поступления", "📤 Расходы", "🔍 По одной операции"])

    with tab_inc:
        if len(df_inc) == 0:
            st.success("Все поступления разнесены!")
        else:
            st.markdown(f"**{len(df_inc)} контрагент(ов) с поступлениями**")

            for i, row in df_inc.iterrows():
                cp_name = row["counterparty"] or "—"
                cp_key = row["cp_key"] or ""
                currency = row["currency"]
                total = row["total_income"]
                cnt = int(row["count"])

                with st.expander(
                    f"{'💰' if total > 1_000_000 else '💵'} {cp_name[:50]} — "
                    f"{total:,.0f} {currency} ({cnt} операц.)"
                ):
                    st.text(f"CP Key: {cp_key}")

                    # Show transactions for this counterparty
                    if len(df_all) > 0 and cp_key:
                        cp_txns = df_all[df_all["cp_key"] == cp_key].copy()
                        if len(cp_txns) > 0:
                            cp_txns["date"] = pd.to_datetime(cp_txns["date"]).dt.strftime("%d.%m.%Y")
                            for nc in ["income", "expense"]:
                                if nc in cp_txns.columns:
                                    cp_txns[nc] = pd.to_numeric(cp_txns[nc], errors="coerce").fillna(0)
                            show_cols = ["date", "income", "expense", "currency"]
                            if "purpose" in cp_txns.columns:
                                cp_txns["purpose_short"] = cp_txns["purpose"].astype(str).str[:80]
                                show_cols.append("purpose_short")
                            st.dataframe(
                                cp_txns[show_cols].rename(columns={
                                    "date": "Дата", "income": "Приход", "expense": "Расход",
                                    "currency": "Валюта", "purpose_short": "Назначение",
                                }),
                                hide_index=True, use_container_width=True
                            )

                    c1, c2 = st.columns(2)
                    with c1:
                        cat1 = st.selectbox("Категория", list(income_cats.keys()), key=f"inc_cat1_{i}")
                    with c2:
                        sub_cats = income_cats.get(cat1, ["Прочее"])
                        cat2 = st.selectbox("Подкатегория", sub_cats, key=f"inc_cat2_{i}")

                    direction = st.radio(
                        "Применить к:",
                        ["income", "expense", "all"],
                        format_func=lambda x: {"income": "📥 Только доходы", "expense": "📤 Только расходы", "all": "📦 Все операции"}[x],
                        key=f"inc_dir_{i}", horizontal=True,
                    )

                    if st.button("✅ Применить", key=f"inc_apply_{i}", type="primary"):
                        try:
                            result = api.assign_category_bulk({
                                "cp_key": cp_key,
                                "counterparty": cp_name,
                                "cat_lvl1": cat1,
                                "cat_lvl2": cat2,
                                "direction": direction,
                            })
                            st.success(f"✅ Обновлено {result.get('updated', 0)} операций → {cat1} / {cat2}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Ошибка: {e}")

    with tab_exp:
        if len(df_exp) == 0:
            st.success("Все расходы разнесены!")
        else:
            st.markdown(f"**{len(df_exp)} контрагент(ов) с расходами**")

            for i, row in df_exp.iterrows():
                cp_name = row["counterparty"] or "—"
                cp_key = row["cp_key"] or ""
                currency = row["currency"]
                total = row["total_expense"]
                cnt = int(row["count"])

                with st.expander(
                    f"{'🔴' if total > 1_000_000 else '🟡'} {cp_name[:50]} — "
                    f"{total:,.0f} {currency} ({cnt} операц.)"
                ):
                    st.text(f"CP Key: {cp_key}")

                    # Show transactions for this counterparty
                    if len(df_all) > 0 and cp_key:
                        cp_txns = df_all[df_all["cp_key"] == cp_key].copy()
                        if len(cp_txns) > 0:
                            cp_txns["date"] = pd.to_datetime(cp_txns["date"]).dt.strftime("%d.%m.%Y")
                            for nc in ["income", "expense"]:
                                if nc in cp_txns.columns:
                                    cp_txns[nc] = pd.to_numeric(cp_txns[nc], errors="coerce").fillna(0)
                            show_cols = ["date", "income", "expense", "currency"]
                            if "purpose" in cp_txns.columns:
                                cp_txns["purpose_short"] = cp_txns["purpose"].astype(str).str[:80]
                                show_cols.append("purpose_short")
                            st.dataframe(
                                cp_txns[show_cols].rename(columns={
                                    "date": "Дата", "income": "Приход", "expense": "Расход",
                                    "currency": "Валюта", "purpose_short": "Назначение",
                                }),
                                hide_index=True, use_container_width=True
                            )

                    c1, c2 = st.columns(2)
                    with c1:
                        cat1 = st.selectbox("Категория", list(expense_cats.keys()), key=f"exp_cat1_{i}")
                    with c2:
                        sub_cats = expense_cats.get(cat1, ["Прочее"])
                        cat2 = st.selectbox("Подкатегория", sub_cats, key=f"exp_cat2_{i}")

                    direction = st.radio(
                        "Применить к:",
                        ["expense", "income", "all"],
                        format_func=lambda x: {"income": "📥 Только доходы", "expense": "📤 Только расходы", "all": "📦 Все операции"}[x],
                        key=f"exp_dir_{i}", horizontal=True,
                    )

                    if st.button("✅ Применить", key=f"exp_apply_{i}", type="primary"):
                        try:
                            result = api.assign_category_bulk({
                                "cp_key": cp_key,
                                "counterparty": cp_name,
                                "cat_lvl1": cat1,
                                "cat_lvl2": cat2,
                                "direction": direction,
                            })
                            st.success(f"✅ Обновлено {result.get('updated', 0)} операций → {cat1} / {cat2}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Ошибка: {e}")

    with tab_single:
        _render_single_assignment()


def _render_single_assignment():
    """Old-style single transaction assignment."""
    try:
        unassigned = api.get_unassigned(limit=200)
    except Exception as e:
        st.error(f"Ошибка: {e}")
        return

    if not unassigned:
        st.success("Все разнесены!")
        return

    df = pd.DataFrame(unassigned)

    txn_options = {}
    for _, row in df.iterrows():
        cp = row.get("counterparty", "")[:40] if row.get("counterparty") else "—"
        date_str = str(row.get("date", ""))[:10]
        exp = float(row.get("expense", 0) or 0)
        inc = float(row.get("income", 0) or 0)
        amt = exp if exp else inc
        curr = row.get("currency", "")
        label = f"{date_str} | {cp} | {amt:,.0f} {curr}"
        txn_options[label] = row

    selected_label = st.selectbox("Выберите операцию", list(txn_options.keys()), key="inbox_sel")
    selected_txn = txn_options.get(selected_label)

    if selected_txn is not None:
        txn_id = selected_txn.get("txn_id", "")
        cp_key = selected_txn.get("cp_key", "")

        col_info, col_form = st.columns([2, 2])

        with col_info:
            st.markdown("**Детали:**")
            st.text(f"Дата: {str(selected_txn.get('date', ''))[:10]}")
            st.text(f"Контрагент: {selected_txn.get('counterparty', '')}")
            st.text(f"Приход: {selected_txn.get('income', 0)} / Расход: {selected_txn.get('expense', 0)}")
            with st.expander("Назначение"):
                st.text(selected_txn.get("purpose", ""))

        with col_form:
            scope = st.radio(
                "Область",
                ["txn", "cp"],
                format_func=lambda x: "Только эта операция" if x == "txn" else "Весь контрагент",
                key="inbox_scope",
            )
            cat_lvl1 = st.selectbox("Категория 1", all_cat1_list, key="inbox_cat1")
            cat_lvl2 = st.selectbox("Категория 2", all_cats.get(cat_lvl1, ["Прочее"]), key="inbox_cat2")

            if st.button("✅ Применить", type="primary", key="inbox_apply"):
                try:
                    api.assign_category(
                        txn_id=txn_id, cat_lvl1=cat_lvl1, cat_lvl2=cat_lvl2,
                        scope=scope, cp_key=cp_key,
                    )
                    st.success("Категория назначена!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")
