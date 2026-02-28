"""
Dashboard page: собственник видит всё главное одним взглядом.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import date, datetime
from frontend import api_client as api


def render():
    st.title("🏠 Дашборд")

    today = date.today()
    col_date, col_month, col_year, col_curr = st.columns(4)
    with col_date:
        as_of = st.date_input("Остатки на дату", value=today, key="dash_date")
    with col_month:
        month = st.number_input("Месяц ДДС", min_value=1, max_value=12,
                                value=today.month, key="dash_month")
    with col_year:
        year = st.number_input("Год ДДС", min_value=2020, max_value=2030,
                               value=today.year, key="dash_year")
    with col_curr:
        currency = st.selectbox("Валюта ДДС", ["RUB", "CNY"], key="dash_curr")

    st.markdown("---")

    # ── Остатки ──────────────────────────────────────────────────────────────
    st.subheader("💰 Остатки на счетах")
    try:
        balances = api.get_balance(str(as_of))
        if balances:
            df_bal = pd.DataFrame(balances)
            # Summary by currency
            col1, col2 = st.columns(2)
            with col1:
                rub_total = df_bal[df_bal["currency"] == "RUB"]["balance"].sum()
                st.metric("RUB (всего)", f"{rub_total:,.0f} ₽")
            with col2:
                cny_total = df_bal[df_bal["currency"] == "CNY"]["balance"].sum()
                st.metric("CNY (всего)", f"{cny_total:,.2f} ¥")

            # Per account table
            df_bal["balance_fmt"] = df_bal.apply(
                lambda r: f"{r['balance']:,.2f} {'₽' if r['currency']=='RUB' else '¥'}",
                axis=1
            )
            st.dataframe(
                df_bal[["account_name", "bank", "currency", "balance_fmt"]].rename(columns={
                    "account_name": "Счёт", "bank": "Банк",
                    "currency": "Валюта", "balance_fmt": "Остаток"
                }),
                hide_index=True, use_container_width=True
            )
        else:
            st.info("Нет данных об остатках. Импортируйте выписки.")
    except Exception as e:
        st.error(f"Ошибка загрузки остатков: {e}")

    st.markdown("---")

    # ── ДДС за месяц ─────────────────────────────────────────────────────────
    st.subheader(f"📊 ДДС за {month:02d}/{year} ({currency})")
    try:
        dds = api.get_dds_month(year=int(year), month=int(month), currency=currency)
        if dds:
            df_dds = pd.DataFrame(dds)
            df_dds["cat_lvl1"] = df_dds["cat_lvl1"].fillna("Без категории")
            df_dds["cat_lvl2"] = df_dds["cat_lvl2"].fillna("")

            # Summary by cat_lvl1
            summary = df_dds.groupby("cat_lvl1")[["income", "expense", "net"]].sum().reset_index()
            summary = summary.sort_values("net")

            col_tbl, col_chart = st.columns([3, 2])
            with col_tbl:
                st.dataframe(
                    summary.rename(columns={
                        "cat_lvl1": "Категория", "income": "Приход",
                        "expense": "Расход", "net": "Нетто"
                    }).style.format({"Приход": "{:,.0f}", "Расход": "{:,.0f}", "Нетто": "{:,.0f}"}),
                    hide_index=True, use_container_width=True
                )
                net_total = summary["net"].sum()
                inc_total = summary["income"].sum()
                exp_total = summary["expense"].sum()
                st.markdown(f"**Итого:** Приход {inc_total:,.0f} | Расход {exp_total:,.0f} | **Нетто {net_total:,.0f}**")
            with col_chart:
                fig = px.bar(
                    summary, x="net", y="cat_lvl1", orientation="h",
                    color=summary["net"].apply(lambda x: "green" if x >= 0 else "red"),
                    color_discrete_map={"green": "#22c55e", "red": "#ef4444"},
                    title="Нетто по категориям"
                )
                fig.update_layout(showlegend=False, height=300, margin=dict(l=0, r=0, t=30, b=0))
                st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Нет данных за выбранный период.")
    except Exception as e:
        st.error(f"Ошибка загрузки ДДС: {e}")

    st.markdown("---")

    # ── Аналитика поступлений ─────────────────────────────────────────────────
    st.subheader(f"📥 Поступления за {month:02d}/{year} по категориям")
    try:
        # Сначала загружаем данные верхнего уровня для списка категорий
        income_top = api.get_income_by_category_daily(year=int(year), month=int(month), currency=currency)
        top_categories = sorted(set(r["category"] for r in income_top)) if income_top else []

        # Выбор категории для drill-down
        cat_options = ["Все категории"] + top_categories
        selected_cat = st.selectbox(
            "Категория прихода", cat_options, key="dash_income_cat"
        )

        cat_filter = None if selected_cat == "Все категории" else selected_cat
        income_data = api.get_income_by_category_daily(
            year=int(year), month=int(month), currency=currency, cat_lvl1=cat_filter
        ) if cat_filter else income_top

        if income_data:
            df_inc = pd.DataFrame(income_data)
            df_inc["date"] = pd.to_datetime(df_inc["date"])

            # Цвета по категориям
            cat_colors = {
                "Маркетплейсы": "#8b5cf6",
                "Займы": "#f59e0b",
                "Прочее": "#6b7280",
                "Без категории": "#d1d5db",
                "Без подкатегории": "#d1d5db",
            }

            if cat_filter:
                st.caption(f"Детализация: **{cat_filter}** → подкатегории")

            col_chart, col_pie = st.columns([3, 2])

            with col_chart:
                cats = df_inc["category"].unique()
                fig = go.Figure()
                for cat in cats:
                    df_c = df_inc[df_inc["category"] == cat]
                    daily = df_c.groupby("date")["income"].sum().reset_index()
                    fig.add_trace(go.Bar(
                        x=daily["date"], y=daily["income"],
                        name=cat,
                        marker_color=cat_colors.get(cat, "#94a3b8"),
                    ))
                fig.update_layout(
                    barmode="stack", height=300,
                    margin=dict(l=0, r=0, t=10, b=0),
                    xaxis_title="", yaxis_title="₽",
                    legend=dict(orientation="h", yanchor="bottom", y=1.02),
                )
                st.plotly_chart(fig, use_container_width=True)

            with col_pie:
                by_cat = df_inc.groupby("category")["income"].sum().reset_index()
                by_cat = by_cat.sort_values("income", ascending=False)
                fig2 = px.pie(
                    by_cat, values="income", names="category",
                    color="category",
                    color_discrete_map=cat_colors,
                    hole=0.45,
                )
                fig2.update_layout(
                    height=300, margin=dict(l=0, r=0, t=10, b=0),
                    showlegend=True,
                    legend=dict(orientation="v", x=1, y=0.5),
                )
                fig2.update_traces(textinfo="percent", textposition="inside")
                st.plotly_chart(fig2, use_container_width=True)

            # Метрики по категориям
            by_cat2 = df_inc.groupby("category")["income"].sum().reset_index().sort_values("income", ascending=False)
            cols_m = st.columns(min(len(by_cat2), 4))
            for i, row in by_cat2.iterrows():
                with cols_m[i % 4]:
                    st.metric(row["category"], f"{row['income']:,.0f} ₽")
        else:
            st.info("Нет данных о поступлениях. Разнесите операции по категориям в INBOX.")
    except Exception as e:
        st.error(f"Ошибка загрузки поступлений: {e}")

    st.markdown("---")


    # ── UNASSIGNED ────────────────────────────────────────────────────────────
    col_unass, col_cf = st.columns(2)

    with col_unass:
        st.subheader("🔴 Неразнесённые операции")
        try:
            unassigned = api.get_unassigned(limit=10)
            if unassigned:
                total_unass = len(unassigned)
                total_sum = sum(float(t.get("expense", 0)) or float(t.get("income", 0))
                                for t in unassigned)
                st.metric("Количество", total_unass)
                df_u = pd.DataFrame(unassigned)
                cols_show = ["date", "counterparty", "expense", "income", "currency"]
                cols_show = [c for c in cols_show if c in df_u.columns]
                st.dataframe(
                    df_u[cols_show].head(10).rename(columns={
                        "date": "Дата", "counterparty": "Контрагент",
                        "expense": "Расход", "income": "Приход", "currency": "Валюта"
                    }),
                    hide_index=True, use_container_width=True
                )
                st.caption("→ Перейдите в INBOX для разноски")
            else:
                st.success("✅ Все операции разнесены!")
        except Exception as e:
            st.error(f"Ошибка: {e}")

    with col_cf:
        st.subheader("📈 Кэшфлоу (60 дней)")
        try:
            # Get current balance as starting point
            balances = api.get_balance(str(today))
            start_bal = sum(
                float(b["balance"]) for b in balances
                if b["currency"] == "RUB"
            ) if balances else 0.0

            cf = api.get_cashflow_daily(days=60, starting_balance=start_bal)
            if cf:
                df_cf = pd.DataFrame(cf)
                df_cf["date"] = pd.to_datetime(df_cf["date"])

                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    x=df_cf["date"], y=df_cf["deficit_running"],
                    mode="lines", name="Баланс",
                    line=dict(color="#3b82f6", width=2),
                    fill="tozeroy",
                    fillcolor="rgba(59,130,246,0.1)",
                ))
                fig.add_hline(y=0, line_dash="dash", line_color="red", opacity=0.5)
                fig.update_layout(
                    height=300, margin=dict(l=0, r=0, t=10, b=0),
                    xaxis_title="", yaxis_title="₽",
                    showlegend=False
                )
                st.plotly_chart(fig, use_container_width=True)

                min_val = df_cf["deficit_running"].min()
                min_date = df_cf.loc[df_cf["deficit_running"].idxmin(), "date"]
                if min_val < 0:
                    st.warning(f"⚠️ Пиковый дефицит: {min_val:,.0f} ₽ — {min_date.strftime('%d.%m.%Y')}")
            else:
                st.info("Нет данных планирования.")
        except Exception as e:
            st.error(f"Ошибка: {e}")

    st.markdown("---")

    # ── Просроченные платежи ──────────────────────────────────────────────────
    st.subheader("⏰ Платежи сегодня и просроченные")
    try:
        payments = api.get_payments()
        if payments:
            df_p = pd.DataFrame(payments)
            df_p["pay_date"] = pd.to_datetime(df_p["pay_date"]).dt.date
            overdue = df_p[(df_p["pay_date"] < today) & (~df_p["is_paid"])]
            today_pay = df_p[(df_p["pay_date"] == today) & (~df_p["is_paid"])]

            col_over, col_today = st.columns(2)
            with col_over:
                st.markdown(f"**Просроченные ({len(overdue)}):**")
                if not overdue.empty:
                    st.dataframe(
                        overdue[["pay_date", "order_no", "direction", "amount_rub"]].rename(columns={
                            "pay_date": "Дата", "order_no": "Заказ",
                            "direction": "Направление", "amount_rub": "Сумма ₽"
                        }),
                        hide_index=True, use_container_width=True
                    )
                else:
                    st.success("Просроченных нет ✅")
            with col_today:
                st.markdown(f"**Сегодня ({len(today_pay)}):**")
                if not today_pay.empty:
                    st.dataframe(
                        today_pay[["pay_date", "order_no", "direction", "amount_rub"]].rename(columns={
                            "pay_date": "Дата", "order_no": "Заказ",
                            "direction": "Направление", "amount_rub": "Сумма ₽"
                        }),
                        hide_index=True, use_container_width=True
                    )
                else:
                    st.info("Платежей на сегодня нет")
        else:
            st.info("Плановые платежи не введены")
    except Exception as e:
        st.error(f"Ошибка: {e}")
