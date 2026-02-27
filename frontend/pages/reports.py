"""
Reports page: DDS month, balance daily, FX, customs.
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import date
from frontend import api_client as api


def render():
    st.title("📊 Отчёты")

    tab1, tab2, tab3, tab4 = st.tabs([
        "ДДС за месяц", "Баланс по дням", "FX Контроль", "Таможня"
    ])

    # ── ДДС за месяц ──────────────────────────────────────────────────────────
    with tab1:
        st.subheader("ДДС за месяц")
        col1, col2, col3 = st.columns(3)
        with col1:
            year = st.number_input("Год", 2020, 2030, date.today().year, key="r_year")
        with col2:
            month = st.number_input("Месяц", 1, 12, date.today().month, key="r_month")
        with col3:
            currency = st.selectbox("Валюта", ["RUB", "CNY"], key="r_curr")

        if st.button("Загрузить ДДС", key="r_load_dds"):
            try:
                dds = api.get_dds_month(int(year), int(month), currency)
                if dds:
                    df = pd.DataFrame(dds)
                    df["cat_lvl1"] = df["cat_lvl1"].fillna("—")
                    df["cat_lvl2"] = df["cat_lvl2"].fillna("—")

                    # Pivot: cat_lvl2 rows
                    st.dataframe(
                        df.rename(columns={
                            "cat_lvl1": "Ур. 1", "cat_lvl2": "Ур. 2",
                            "income": "Приход", "expense": "Расход", "net": "Нетто",
                        }).style.format({"Приход": "{:,.0f}", "Расход": "{:,.0f}", "Нетто": "{:,.0f}"}),
                        hide_index=True, use_container_width=True
                    )

                    # Subtotals by lvl1
                    summary = df.groupby("cat_lvl1")[["income", "expense", "net"]].sum()
                    st.markdown("**Итого по категориям:**")
                    st.dataframe(
                        summary.style.format("{:,.0f}"),
                        use_container_width=True
                    )
                else:
                    st.info("Нет данных за период.")
            except Exception as e:
                st.error(f"Ошибка: {e}")

    # ── Баланс по дням ────────────────────────────────────────────────────────
    with tab2:
        st.subheader("Движение баланса по дням")
        try:
            accounts = api.get_accounts()
            if accounts:
                our_accs = [a for a in accounts if a.get("is_our_account")]
                acc_options = {
                    f"{a['account_name']} ({a['currency']})": (a["account"], a["currency"])
                    for a in our_accs
                }
                col1, col2, col3 = st.columns(3)
                with col1:
                    acc_label = st.selectbox("Счёт", list(acc_options.keys()), key="bal_acc")
                with col2:
                    date_from = st.date_input("С", value=date(date.today().year, 1, 1), key="bal_from")
                with col3:
                    date_to = st.date_input("По", value=date.today(), key="bal_to")

                if st.button("Показать баланс", key="bal_load"):
                    acc_no, curr = acc_options[acc_label]
                    data = api.get_balance_daily(acc_no, curr, str(date_from), str(date_to))
                    if data:
                        df_b = pd.DataFrame(data)
                        df_b["date"] = pd.to_datetime(df_b["date"])
                        fig = go.Figure()
                        fig.add_trace(go.Scatter(
                            x=df_b["date"], y=df_b["balance"],
                            mode="lines+markers", name="Баланс",
                            line=dict(color="#3b82f6"),
                        ))
                        fig.add_trace(go.Bar(
                            x=df_b["date"], y=df_b["daily_net"],
                            name="Нетто за день",
                            marker_color=df_b["daily_net"].apply(
                                lambda x: "#22c55e" if x >= 0 else "#ef4444"
                            )
                        ))
                        fig.update_layout(height=400, title=f"Баланс: {acc_label}")
                        st.plotly_chart(fig, use_container_width=True)
                        st.dataframe(
                            df_b.rename(columns={"date": "Дата", "daily_net": "Нетто", "balance": "Баланс"}),
                            hide_index=True, use_container_width=True
                        )
            else:
                st.info("Сначала настройте счета в Справочниках")
        except Exception as e:
            st.error(f"Ошибка: {e}")

    # ── FX Контроль ────────────────────────────────────────────────────────────
    with tab3:
        st.subheader("FX — Валютные операции")
        col1, col2 = st.columns(2)
        with col1:
            fx_from = st.date_input("С", value=date(date.today().year, 1, 1), key="fx_from")
        with col2:
            fx_to = st.date_input("По", value=date.today(), key="fx_to")

        if st.button("Загрузить FX", key="fx_load"):
            try:
                data = api.get_fx_control(str(fx_from), str(fx_to))
                if data:
                    df = pd.DataFrame(data)
                    if "date" in df.columns:
                        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%d.%m.%Y")
                    for col in ["income", "expense", "net"]:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
                    st.dataframe(df, hide_index=True, use_container_width=True)
                    st.metric("Всего FX операций", len(df))
                else:
                    st.info("FX операций не найдено.")
            except Exception as e:
                st.error(f"Ошибка: {e}")

    # ── Таможня ────────────────────────────────────────────────────────────────
    with tab4:
        st.subheader("Таможня — Контроль платежей")
        col1, col2 = st.columns(2)
        with col1:
            cust_from = st.date_input("С", value=date(date.today().year, 1, 1), key="cust_from")
        with col2:
            cust_to = st.date_input("По", value=date.today(), key="cust_to")

        if st.button("Загрузить таможню", key="cust_load"):
            try:
                data = api.get_customs_control(str(cust_from), str(cust_to))
                if data:
                    df = pd.DataFrame(data)
                    if "date" in df.columns:
                        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%d.%m.%Y")
                    for col in ["income", "expense", "net"]:
                        if col in df.columns:
                            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

                    total = df["expense"].sum() if "expense" in df.columns else 0
                    st.metric("Итого оплачено таможне", f"{total:,.0f} ₽")
                    st.dataframe(df, hide_index=True, use_container_width=True)
                else:
                    st.info("Таможенных платежей не найдено.")
            except Exception as e:
                st.error(f"Ошибка: {e}")
