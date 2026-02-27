"""
Transactions page: search and view all operations.
"""

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from frontend import api_client as api


def render():
    st.title("📋 Операции")

    with st.expander("🔍 Фильтры", expanded=True):
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            date_from = st.date_input("С", value=date.today() - timedelta(days=30), key="txn_from")
        with col2:
            date_to = st.date_input("По", value=date.today(), key="txn_to")
        with col3:
            currency = st.selectbox("Валюта", ["", "RUB", "CNY"], key="txn_curr")
        with col4:
            status = st.selectbox("Статус", ["", "OK", "UNASSIGNED", "NO_CASHFLOW"], key="txn_status")

        col5, col6, col7 = st.columns(3)
        with col5:
            counterparty = st.text_input("Контрагент (содержит)", key="txn_cp")
        with col6:
            cat_lvl1 = st.text_input("Категория 1", key="txn_cat1")
        with col7:
            is_cf = st.selectbox("is_cashflow2", ["", "1", "0"], key="txn_cf")

        limit = st.slider("Количество строк", 50, 1000, 200, key="txn_limit")

    if st.button("🔎 Найти", type="primary"):
        filters = {
            "date_from": str(date_from),
            "date_to": str(date_to),
            "limit": limit,
            "offset": 0,
        }
        if currency:
            filters["currency"] = currency
        if status:
            filters["status"] = status
        if counterparty:
            filters["counterparty"] = counterparty
        if cat_lvl1:
            filters["cat_lvl1_2"] = cat_lvl1
        if is_cf:
            filters["is_cashflow2"] = int(is_cf)

        try:
            with st.spinner("Загрузка..."):
                txns = api.search_transactions(filters)

            if txns:
                df = pd.DataFrame(txns)
                df["date"] = pd.to_datetime(df["date"]).dt.strftime("%d.%m.%Y")

                # Format amounts
                for col in ["income", "expense", "net"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

                st.markdown(f"**Найдено: {len(df)} операций**")

                # Display columns
                display_cols = [
                    "date", "bank", "currency", "counterparty",
                    "income", "expense", "net",
                    "cat_lvl1_2", "cat_lvl2_2",
                    "status", "purpose_tag", "invoice_id", "annex_id",
                ]
                display_cols = [c for c in display_cols if c in df.columns]

                st.dataframe(
                    df[display_cols].rename(columns={
                        "date": "Дата", "bank": "Банк", "currency": "Валюта",
                        "counterparty": "Контрагент",
                        "income": "Приход", "expense": "Расход", "net": "Нетто",
                        "cat_lvl1_2": "Категория 1", "cat_lvl2_2": "Категория 2",
                        "status": "Статус",
                        "purpose_tag": "Тег", "invoice_id": "Invoice", "annex_id": "Annex",
                    }),
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Приход": st.column_config.NumberColumn(format="%.2f"),
                        "Расход": st.column_config.NumberColumn(format="%.2f"),
                        "Нетто": st.column_config.NumberColumn(format="%.2f"),
                    }
                )

                # Stats
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Суммарный приход", f"{df['income'].sum():,.0f}")
                with col2:
                    st.metric("Суммарный расход", f"{df['expense'].sum():,.0f}")
                with col3:
                    st.metric("Нетто", f"{df['net'].sum():,.0f}")
            else:
                st.info("Операции не найдены.")
        except Exception as e:
            st.error(f"Ошибка: {e}")
