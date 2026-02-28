"""
Planning page: orders, payments, WB incomes, lead times, customs, cashflow.
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date
from frontend import api_client as api


def render():
    st.title("📦 Планирование")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "Заказы", "Платежи", "Поступления WB",
        "Кэшфлоу", "Таможня", "Lead Times"
    ])

    # ── Заказы ────────────────────────────────────────────────────────────────
    with tab1:
        st.subheader("Заказы (из Себестоимости)")
        try:
            cost_orders_list = api.get_cost_orders()
        except Exception as e:
            st.error(f"Ошибка: {e}")
            cost_orders_list = []

        if cost_orders_list:
            df_orders = pd.DataFrame(cost_orders_list)

            # Format dates
            for col in ["ship_date", "actual_arrival_date"]:
                if col in df_orders.columns:
                    df_orders[col] = pd.to_datetime(df_orders[col], errors="coerce").dt.strftime("%d.%m.%Y").fillna("—")

            # Numeric
            for col in ["delivery_cost_cny", "rate_cny", "total_cost_rub", "total_delivery_rub",
                         "total_duty_rub", "total_vat_rub", "total_rub", "total_qty"]:
                if col in df_orders.columns:
                    df_orders[col] = pd.to_numeric(df_orders[col], errors="coerce").fillna(0)

            display_cols = {
                "order_no": "№", "invoice_no": "Инвойс",
                "transport_type": "Транспорт", "ship_date": "Отгрузка",
                "actual_arrival_date": "Прибытие",
                "total_qty": "Кол-во", "total_cost_rub": "Товар ₽",
                "delivery_cost_cny": "Доставка ¥", "total_delivery_rub": "Доставка ₽",
                "total_duty_rub": "Пошлина ₽", "total_vat_rub": "НДС ₽",
                "total_rub": "Итого ₽", "rate_cny": "Курс ¥",
            }
            avail_cols = [c for c in display_cols if c in df_orders.columns]
            st.dataframe(
                df_orders[avail_cols].rename(columns=display_cols),
                hide_index=True, use_container_width=True,
            )

            st.caption("✏️ Редактирование заказов — в модуле **Себестоимость**")
        else:
            st.info("Нет заказов. Создайте заказ в модуле **Себестоимость**.")

        # Order summary (plan vs fact)
        st.markdown("---")
        st.subheader("📊 Сводка по заказу (план vs факт)")
        if cost_orders_list:
            order_nos = sorted(set(o.get("order_no") for o in cost_orders_list if o.get("order_no")))
            selected_no = st.selectbox("Выберите заказ", order_nos, key="o_sum_no")
            if st.button("Показать сводку", key="o_sum_btn"):
                try:
                    summary = api.get_order_summary(selected_no)
                    order_data = summary.get("order", {})
                    totals = summary.get("totals", {})

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("План заказ", f"{totals.get('plan_order', 0):,.0f} ₽")
                        st.metric("Факт заказ", f"{totals.get('fact_order', 0):,.0f} ₽")
                    with col2:
                        st.metric("План логистика", f"{totals.get('plan_logistics_cny', 0):,.0f} ¥")
                    with col3:
                        st.metric("План таможня", f"{totals.get('plan_customs_rub', 0):,.0f} ₽")
                        st.metric("Факт таможня", f"{totals.get('fact_customs', 0):,.0f} ₽")

                    # Fact order payments
                    fact_orders = summary.get("fact_order_payments", [])
                    if fact_orders:
                        st.markdown("**Факт оплаты заказа:**")
                        st.dataframe(pd.DataFrame(fact_orders)[["date", "counterparty", "expense", "currency", "annex_id"]], hide_index=True)

                    # Customs allocs
                    customs = summary.get("fact_customs_allocs", [])
                    if customs:
                        st.markdown("**Таможня (распределено):**")
                        st.dataframe(pd.DataFrame(customs)[["pay_date", "alloc_amount", "comment"]], hide_index=True)
                except Exception as e:
                    st.error(f"Ошибка: {e}")

    # ── Платежи ───────────────────────────────────────────────────────────────
    with tab2:
        st.subheader("Плановые платежи")

        # Sync button
        if st.button("🔄 Синхронизировать факт с выписками", key="btn_sync_plan"):
            try:
                api.sync_plan_payments()
                st.success("✅ Факт синхронизирован")
                st.rerun()
            except Exception as e:
                st.error(f"Ошибка: {e}")

        try:
            payments = api.get_payments()
        except Exception as e:
            st.error(f"Ошибка: {e}")
            payments = []

        if payments:
            df_p = pd.DataFrame(payments)
            if "pay_date" in df_p.columns:
                df_p["pay_date"] = pd.to_datetime(df_p["pay_date"]).dt.strftime("%d.%m.%Y")
            today = date.today()

            # Ensure paid_rub column
            if "paid_rub" not in df_p.columns:
                df_p["paid_rub"] = 0.0
            df_p["paid_rub"] = pd.to_numeric(df_p["paid_rub"], errors="coerce").fillna(0)
            df_p["amount_rub"] = pd.to_numeric(df_p["amount_rub"], errors="coerce").fillna(0)

            # Status column with partial payment support
            def _status(row):
                amt = float(row.get("amount_rub", 0) or 0)
                paid = float(row.get("paid_rub", 0) or 0)
                if row.get("is_paid") or (amt > 0 and paid >= amt):
                    return "✅ Оплачено"
                if paid > 0 and amt > 0:
                    pct = paid / amt * 100
                    return f"🟠 Частично ({pct:.0f}%)"
                d = row.get("pay_date", "")
                try:
                    dt = pd.to_datetime(d, dayfirst=True).date()
                    if dt < today:
                        return "🔴 Просрочено"
                    if dt == today:
                        return "🟡 Сегодня"
                    return "🔵 Будущее"
                except Exception:
                    return "—"

            df_p["Статус"] = df_p.apply(_status, axis=1)

            # Format columns
            df_p["amount_rub_fmt"] = df_p["amount_rub"].apply(lambda x: f"{x:,.0f} ₽")
            df_p["paid_rub_fmt"] = df_p["paid_rub"].apply(lambda x: f"{x:,.0f} ₽" if x > 0 else "—")
            df_p["остаток"] = (df_p["amount_rub"] - df_p["paid_rub"]).apply(
                lambda x: f"{x:,.0f} ₽" if x > 0 else "—"
            )

            st.dataframe(
                df_p[["id", "pay_date", "order_no", "direction",
                       "amount", "currency", "amount_rub_fmt", "paid_rub_fmt",
                       "остаток", "Статус"]].rename(columns={
                    "id": "ID", "pay_date": "Дата", "order_no": "Заказ",
                    "direction": "Направление", "amount": "Сумма",
                    "currency": "Вал.", "amount_rub_fmt": "План ₽",
                    "paid_rub_fmt": "Факт ₽", "остаток": "Остаток",
                }),
                hide_index=True, use_container_width=True,
            )

            # Summary metrics
            total_plan = df_p["amount_rub"].sum()
            total_paid = df_p["paid_rub"].sum()
            total_remain = total_plan - total_paid
            cm1, cm2, cm3, cm4 = st.columns(4)
            cm1.metric("Всего план", f"{total_plan:,.0f} ₽")
            cm2.metric("Оплачено", f"{total_paid:,.0f} ₽")
            cm3.metric("Остаток", f"{total_remain:,.0f} ₽")
            cm4.metric("Прогресс", f"{total_paid / total_plan * 100:.0f}%" if total_plan > 0 else "—")

            # Mark as paid
            col_id, col_btn = st.columns([2, 1])
            with col_id:
                pay_id_to_mark = st.number_input("ID платежа для отметки оплаты", 0, key="pay_mark_id")
            with col_btn:
                if st.button("✅ Отметить оплаченным", key="pay_mark_btn") and pay_id_to_mark:
                    try:
                        api.mark_payment_paid(int(pay_id_to_mark))
                        st.success("Отмечено!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка: {e}")

        # Add payment form
        with st.expander("➕ Добавить платёж"):
            col1, col2, col3 = st.columns(3)
            with col1:
                p_date = st.date_input("Дата оплаты", key="p_date")
                p_order_no = st.number_input("Номер заказа", 0, key="p_order_no")
                p_direction = st.selectbox("Направление", ["Заказ", "Логистика", "Таможня", "Депозит", "Другое"], key="p_dir")
            with col2:
                p_amount = st.number_input("Сумма", 0.0, key="p_amt")
                p_currency = st.selectbox("Валюта", ["RUB", "CNY", "USD"], key="p_curr")
                p_fx = st.number_input("Курс (если не RUB)", 0.0, key="p_fx")
            with col3:
                p_rub = st.number_input("Сумма ₽ (итого)", 0.0, key="p_rub")

            if st.button("💾 Добавить платёж", key="p_save"):
                try:
                    api.upsert_payment({
                        "pay_date": str(p_date),
                        "order_no": int(p_order_no) if p_order_no else None,
                        "direction": p_direction,
                        "amount": float(p_amount) if p_amount else None,
                        "currency": p_currency,
                        "fx_rate": float(p_fx) if p_fx else None,
                        "amount_rub": float(p_rub) if p_rub else None,
                    })
                    st.success("Сохранено!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")

        # Manual fact matching
        with st.expander("🔗 Привязать транзакцию из выписки к платежу"):
            if payments:
                # Load accounts for filter
                try:
                    accounts = api.get_accounts_list()
                except Exception:
                    accounts = []

                # ── Row 1: Payment + Account filter ──
                lnk_col1, lnk_col2 = st.columns(2)
                with lnk_col1:
                    pay_options = {}
                    for p in payments:
                        if p.get("is_paid"):
                            continue
                        cur = p.get("currency", "RUB")
                        amt_orig = float(p.get("amount", 0) or 0)
                        amt_rub = float(p.get("amount_rub", 0) or 0)
                        if cur != "RUB" and amt_orig > 0:
                            label = f"ID:{p['id']} | №{p.get('order_no','')} | {p.get('direction','')} | {amt_orig:,.0f} {cur} ({amt_rub:,.0f}₽)"
                        else:
                            label = f"ID:{p['id']} | №{p.get('order_no','')} | {p.get('direction','')} | {amt_rub:,.0f} ₽"
                        pay_options[label] = p["id"]

                    if pay_options:
                        sel_pay = st.selectbox("Платёж", list(pay_options.keys()), key="lnk_pay")
                        sel_pay_id = pay_options[sel_pay]
                        sel_payment = next((p for p in payments if p["id"] == sel_pay_id), {})
                        sel_direction = (sel_payment.get("direction") or "ЗАКАЗ").upper()
                    else:
                        st.info("Нет неоплаченных платежей")
                        sel_pay_id = None
                        sel_direction = "ЗАКАЗ"

                with lnk_col2:
                    # Account filter
                    if accounts:
                        acc_options = {"Все счета": None}
                        for a in accounts:
                            label = f"{a['currency']} | {a['bank']} | ...{a['account'][-6:]}"
                            acc_options[label] = a["account"]
                        sel_acc_label = st.selectbox("Счёт", list(acc_options.keys()), key="lnk_acc")
                        sel_account = acc_options[sel_acc_label]
                    else:
                        sel_account = None

                # ── Row 2: Transaction selection ──
                sel_txn = None
                lnk_amount = 0.0
                candidates = []
                if pay_options and sel_pay_id:
                    try:
                        candidates = api.get_candidate_transactions(sel_direction, sel_account)
                    except Exception:
                        candidates = []
                    if candidates:
                        cand_options = {}
                        for c in candidates:
                            d = c["date"][:10] if c.get("date") else "?"
                            cur = c.get("currency", "RUB")
                            cp = c.get("counterparty", "")[:30]
                            amt = c["expense"]
                            label = f"{d} | {cur} {amt:,.0f} | {cp}"
                            cand_options[label] = c
                        sel_cand = st.selectbox("Транзакция из выписки", list(cand_options.keys()), key="lnk_cand")
                        sel_txn = cand_options[sel_cand]

                        # Show full purpose below
                        full_purpose = sel_txn.get("purpose", "—")
                        st.caption(f"📄 Назначение: {full_purpose}")

                        lnk_col3, lnk_col4 = st.columns(2)
                        with lnk_col3:
                            lnk_amount = st.number_input(
                                f"Сумма привязки ({sel_txn.get('currency', 'RUB')})",
                                value=float(sel_txn["expense"]),
                                min_value=0.0, step=100.0, key="lnk_amt")
                        with lnk_col4:
                            st.write("")  # spacer
                    else:
                        st.info("Нет транзакций по выбранному фильтру")

                if pay_options and sel_pay_id and sel_txn:
                    if st.button("🔗 Привязать", key="btn_link_fact"):
                        try:
                            api.create_fact_link({
                                "payment_id": sel_pay_id,
                                "txn_id": sel_txn["txn_id"],
                                "amount_rub": lnk_amount,
                            })
                            st.success("✅ Привязано!")
                            api.sync_plan_payments()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Ошибка: {e}")
            else:
                st.info("Сначала создайте план оплат в Себестоимости")

    # ── Поступления WB ────────────────────────────────────────────────────────
    with tab3:
        st.subheader("Поступления WB")

        # ── Upload WB Payout Excel ──
        with st.expander("📤 Загрузить выписку из кабинета WB"):
            wb_file = st.file_uploader("Excel из кабинета WB", type=["xlsx"], key="wb_upload")
            if wb_file and st.button("📊 Загрузить", key="btn_wb_upload"):
                try:
                    result = api.upload_wb_payouts(wb_file.read(), wb_file.name)
                    st.success(
                        f"Создано: {result['created']}, обновлено: {result['updated']}, "
                        f"пропущено: {result['skipped']}"
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка загрузки: {e}")

        # ── WB Payouts Table ──
        try:
            payouts = api.get_wb_payouts()
        except Exception as e:
            st.error(f"Ошибка: {e}")
            payouts = []

        if payouts:
            status_emoji = {
                "PENDING": "⏳ В очереди",
                "PROCESSING": "🔄 Обрабатывается",
                "TRANSIT": "✈️ В пути",
                "RECEIVED": "✅ Получено",
            }
            df_wp = pd.DataFrame(payouts)
            df_wp["Статус"] = df_wp["status"].map(lambda s: status_emoji.get(s, s))
            df_wp["Дата"] = pd.to_datetime(df_wp["created_at"]).dt.strftime("%d.%m.%Y")
            df_wp["Сумма"] = df_wp["amount_rub"].apply(lambda x: f"{float(x):,.0f} ₽")

            st.dataframe(
                df_wp[["Дата", "request_id", "Сумма", "Статус", "matched_txn_id"]].rename(columns={
                    "request_id": "ID заявки", "matched_txn_id": "Сверено с",
                }),
                hide_index=True, use_container_width=True,
            )

            # Metrics
            transit_sum = sum(float(p["amount_rub"]) for p in payouts
                             if p["status"] in ("TRANSIT", "PROCESSING", "PENDING"))
            received_sum = sum(float(p["amount_rub"]) for p in payouts
                              if p["status"] == "RECEIVED")
            c1, c2, c3 = st.columns(3)
            c1.metric("В пути / ожидает", f"{transit_sum:,.0f} ₽")
            c2.metric("Получено", f"{received_sum:,.0f} ₽")
            c3.metric("Всего записей", len(payouts))
        else:
            st.info("Нет данных. Загрузите выписку из кабинета WB.")

        # ── Manual Reconciliation ──
        with st.expander("🔗 Ручная сверка"):
            unmatched = [p for p in payouts if p["status"] != "RECEIVED"] if payouts else []
            if unmatched:
                sel_payout = st.selectbox(
                    "Выплата WB",
                    options=unmatched,
                    format_func=lambda p: f"{p['request_id']} — {float(p['amount_rub']):,.0f} ₽ ({p['status']})",
                    key="wb_reconcile_sel",
                )
                txn_id_input = st.text_input("txn_id банковской транзакции", key="wb_reconcile_txn")
                if st.button("Привязать", key="btn_wb_reconcile") and sel_payout and txn_id_input:
                    try:
                        api.manual_reconcile_wb(sel_payout["id"], txn_id_input)
                        st.success("Сверено!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ошибка: {e}")
            else:
                st.success("Все выплаты сверены!")

        # ── Auto Forecast ──
        st.markdown("---")
        st.subheader("📈 Автопрогноз WB поступлений")

        if st.button("🔄 Обновить прогноз (среднее за 7 дней → 30 дней вперёд)", key="btn_wb_forecast"):
            try:
                result = api.refresh_wb_forecast()
                st.session_state["wb_forecast_msg"] = (
                    f"Среднедневной доход WB: {result['daily_avg']:,.0f} ₽, "
                    f"создано записей: {result['created']}"
                )
                st.rerun()
            except Exception as e:
                st.error(f"Ошибка: {e}")

        if "wb_forecast_msg" in st.session_state:
            st.success(st.session_state.pop("wb_forecast_msg"))

        # Show current forecast
        try:
            incomes_all = api.get_incomes()
            wb_forecast = [i for i in incomes_all if i.get("source") == "WB_AUTO"]
            if wb_forecast:
                df_fc = pd.DataFrame(wb_forecast)
                df_fc["Дата"] = pd.to_datetime(df_fc["date"]).dt.strftime("%d.%m.%Y")
                df_fc["Сумма"] = df_fc["amount_rub"].apply(lambda x: f"{float(x):,.0f} ₽")
                st.dataframe(
                    df_fc[["Дата", "Сумма"]].rename(columns={"Дата": "Дата", "Сумма": "Прогноз"}),
                    hide_index=True, use_container_width=True,
                )
                total_fc = sum(float(i["amount_rub"]) for i in wb_forecast)
                st.metric("Итого прогноз на 30 дней", f"{total_fc:,.0f} ₽")
            else:
                st.info("Прогноз не сформирован. Нажмите кнопку выше.")
        except Exception:
            pass

        # ── Manual PlannedIncome (legacy) ──
        with st.expander("📝 Ручные записи поступлений"):
            try:
                incomes = api.get_incomes()
            except Exception as e:
                st.error(f"Ошибка: {e}")
                incomes = []

            if incomes:
                df_i = pd.DataFrame(incomes)
                if "date" in df_i.columns:
                    df_i["date"] = pd.to_datetime(df_i["date"]).dt.strftime("%d.%m.%Y")
                st.dataframe(df_i, hide_index=True, use_container_width=True)
                total = sum(float(i.get("amount_rub", 0)) for i in incomes)
                st.metric("Итого поступлений", f"{total:,.0f} ₽")

            col1, col2, col3 = st.columns(3)
            with col1:
                i_id = st.number_input("ID (0 = новое)", 0, key="i_id")
            with col2:
                i_date = st.date_input("Дата", key="i_date")
            with col3:
                i_amount = st.number_input("Сумма ₽", 0.0, key="i_amt")

            if st.button("💾 Сохранить", key="i_save"):
                try:
                    api.upsert_income({
                        "id": int(i_id) if i_id else None,
                        "date": str(i_date),
                        "amount_rub": float(i_amount),
                        "source": "WB",
                    })
                    st.success("Сохранено!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")

    # ── Кэшфлоу ───────────────────────────────────────────────────────────────
    with tab4:
        st.subheader("📈 Прогноз кэшфлоу")
        col1, col2 = st.columns(2)
        with col1:
            cf_days = st.slider("Горизонт (дней)", 7, 180, 60, key="cf_days")
        with col2:
            cf_start = st.number_input("Стартовый остаток (RUB)", 0.0, key="cf_start")

        if st.button("Рассчитать", key="cf_calc", type="primary"):
            try:
                cf = api.get_cashflow_daily(int(cf_days), float(cf_start))
                if cf:
                    df_cf = pd.DataFrame(cf)
                    df_cf["date"] = pd.to_datetime(df_cf["date"])

                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=df_cf["date"], y=df_cf["deficit_running"],
                        name="Баланс (RUB)", mode="lines",
                        line=dict(color="#3b82f6", width=2),
                        fill="tozeroy",
                        fillcolor="rgba(59,130,246,0.1)",
                    ))
                    fig.add_trace(go.Bar(
                        x=df_cf["date"], y=df_cf["planned_income"],
                        name="Поступления", marker_color="#22c55e", opacity=0.7
                    ))
                    fig.add_trace(go.Bar(
                        x=df_cf["date"], y=[-v for v in df_cf["planned_expense"]],
                        name="Расходы", marker_color="#ef4444", opacity=0.7
                    ))
                    fig.add_hline(y=0, line_dash="dash", line_color="red", opacity=0.5)
                    fig.update_layout(
                        height=450, title="Прогноз кэшфлоу",
                        barmode="relative"
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # Key metrics
                    min_val = df_cf["deficit_running"].min()
                    min_date = df_cf.loc[df_cf["deficit_running"].idxmin(), "date"]
                    max_val = df_cf["deficit_running"].max()

                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Минимальный баланс", f"{min_val:,.0f} ₽",
                                  delta="⚠️ ДЕФИЦИТ" if min_val < 0 else "✅ OK")
                    with col2:
                        st.metric("Дата пика дефицита", min_date.strftime("%d.%m.%Y"))
                    with col3:
                        st.metric("Максимальный баланс", f"{max_val:,.0f} ₽")

                    # Table
                    with st.expander("Детальная таблица"):
                        df_show = df_cf.copy()
                        df_show["date"] = df_show["date"].dt.strftime("%d.%m.%Y")
                        st.dataframe(df_show.rename(columns={
                            "date": "Дата", "planned_income": "Поступления",
                            "planned_expense": "Расходы", "net": "Нетто",
                            "deficit_running": "Баланс нарастающим",
                        }), hide_index=True, use_container_width=True)
            except Exception as e:
                st.error(f"Ошибка: {e}")

    # ── Таможня (TOPUP / ALLOC) ───────────────────────────────────────────────
    with tab5:
        st.subheader("Таможня: Авансы и распределение")

        # Get orders for dropdowns
        try:
            cost_orders = api.get_cost_orders()
            order_choices = ["—"] + sorted(set(str(o["order_no"]) for o in cost_orders))
        except Exception:
            cost_orders = []
            order_choices = ["—"]

        try:
            topups = api.get_customs_topup()
        except Exception as e:
            st.error(f"Ошибка: {e}")
            topups = []

        try:
            dt_list = api.get_customs_dt_list()
        except Exception:
            dt_list = []

        # ── Balance ──────────────────────────────────────────────────────────
        total_topups = sum(float(t.get("amount_rub", 0)) for t in topups)
        total_dt_spent = sum(float(d.get("amount_rub", 0)) for d in dt_list)

        # Initial balance stored in session / setting
        if "customs_initial_balance" not in st.session_state:
            st.session_state["customs_initial_balance"] = 535597.60  # default from PDF

        bal_col1, bal_col2, bal_col3, bal_col4 = st.columns(4)
        with bal_col1:
            init_bal = st.number_input("Вх. остаток ₽",
                value=st.session_state["customs_initial_balance"],
                step=1000.0, format="%.2f", key="cust_init_bal")
            st.session_state["customs_initial_balance"] = init_bal
        current_balance = init_bal + total_topups - total_dt_spent
        bal_col2.metric("Пополнения", f"{total_topups:,.0f} ₽")
        bal_col3.metric("Списано (ДТ)", f"{total_dt_spent:,.0f} ₽")
        bal_col4.metric("💰 Остаток", f"{current_balance:,.0f} ₽",
                        delta=f"{current_balance - init_bal:+,.0f}")

        # ── Topups table ─────────────────────────────────────────────────────
        if topups:
            with st.expander(f"📥 Пополнения ({len(topups)})", expanded=False):
                df_t = pd.DataFrame(topups)
                for col in ["amount_rub", "allocated", "remaining"]:
                    if col in df_t.columns:
                        df_t[col] = pd.to_numeric(df_t[col], errors="coerce").fillna(0)
                st.dataframe(
                    df_t[["date", "amount_rub", "allocated", "remaining", "purpose"]].rename(columns={
                        "date": "Дата", "amount_rub": "Сумма ₽",
                        "allocated": "Распределено", "remaining": "Остаток", "purpose": "Назначение",
                    }),
                    hide_index=True, use_container_width=True
                )

        # ── ДТ декларации (ФТС) ──────────────────────────────────────────────
        st.markdown("---")
        st.subheader("📋 Декларации (ДТ) из отчёта ФТС")

        # Upload PDF
        with st.expander("📤 Загрузить отчёт ФТС (PDF)"):
            fts_file = st.file_uploader("PDF отчёт ФТС", type=["pdf"], key="fts_upload")
            if fts_file and st.button("📊 Загрузить и разобрать", key="btn_fts_parse"):
                try:
                    result = api.upload_fts_report(fts_file.read(), fts_file.name)
                    st.success(f"✅ Найдено ДТ: {len(result.get('parsed', []))}, "
                               f"создано: {result['created']}, пропущено (дубли): {result['skipped']}")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")

        if dt_list:
            dt_df = pd.DataFrame(dt_list)
            dt_df["dt_date"] = pd.to_datetime(dt_df["dt_date"]).dt.strftime("%d.%m.%Y")
            dt_df["amount_rub"] = pd.to_numeric(dt_df["amount_rub"], errors="coerce")
            dt_df["Заказ"] = dt_df["order_no"].apply(lambda x: f"№{x}" if x else "—")

            st.dataframe(
                dt_df[["dt_date", "dt_number", "amount_rub", "Заказ", "note"]].rename(columns={
                    "dt_date": "Дата", "dt_number": "Номер ДТ",
                    "amount_rub": "Сумма ₽", "note": "Примечание",
                }),
                hide_index=True, use_container_width=True
            )

            total_dt = dt_df["amount_rub"].sum()
            assigned = dt_df[dt_df["order_no"].notna() & (dt_df["order_no"] != 0)]["amount_rub"].sum()
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Всего по ДТ", f"{total_dt:,.0f} ₽")
            mc2.metric("Привязано", f"{assigned:,.0f} ₽")
            mc3.metric("Не привязано", f"{total_dt - assigned:,.0f} ₽")

            # Assign order to DT
            with st.expander("🔗 Привязать ДТ к заказу"):
                dt_options = {
                    f"{d['dt_number']} | {d['dt_date']} | {d['amount_rub']:,.0f} ₽ | {'№'+str(d['order_no']) if d.get('order_no') else '—'}": d
                    for d in dt_list
                }
                sel_dt_label = st.selectbox("Декларация", list(dt_options.keys()), key="dt_sel")
                sel_dt = dt_options[sel_dt_label]

                dc1, dc2 = st.columns(2)
                with dc1:
                    # Order dropdown
                    current_order = str(sel_dt["order_no"]) if sel_dt.get("order_no") else "—"
                    idx = order_choices.index(current_order) if current_order in order_choices else 0
                    sel_order = st.selectbox("Заказ", order_choices, index=idx, key="dt_order_sel")
                    dt_order = int(sel_order) if sel_order != "—" else None
                with dc2:
                    dt_note = st.text_input("Примечание",
                        value=sel_dt.get("note") or "", key="dt_note")

                bc1, bc2 = st.columns(2)
                with bc1:
                    if st.button("💾 Сохранить", key="btn_dt_save"):
                        try:
                            api.update_customs_dt(sel_dt["id"], {
                                "order_no": dt_order,
                                "note": dt_note or None,
                            })
                            st.success("✅ Сохранено!")
                            api.sync_plan_payments()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Ошибка: {e}")
                with bc2:
                    if st.button("🗑 Удалить ДТ", key="btn_dt_del"):
                        try:
                            api.delete_customs_dt(sel_dt["id"])
                            st.success("Удалено")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Ошибка: {e}")
        else:
            st.info("Нет ДТ деклараций. Загрузите отчёт ФТС выше.")

        # Old alloc section
        st.markdown("---")
        st.subheader("Ручное распределение авансов")
        if topups:
            topup_labels = {
                f"{t.get('date')} | {float(t.get('amount_rub') or 0):,.0f} ₽": t.get("topup_txn_id")
                for t in topups
            }
            selected_topup_label = st.selectbox("Аванс таможни", list(topup_labels.keys()), key="ta_sel")
            selected_topup_id = topup_labels.get(selected_topup_label)

            if selected_topup_id:
                try:
                    allocs = api.get_customs_alloc(selected_topup_id)
                    if allocs:
                        st.dataframe(pd.DataFrame(allocs), hide_index=True, use_container_width=True)
                except Exception as e:
                    st.error(f"Ошибка: {e}")

                with st.expander("➕ Добавить распределение"):
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        a_date = st.date_input("Дата платежа", key="a_date")
                    with col2:
                        a_pay_amt = st.number_input("Сумма платежа", 0.0, key="a_pay_amt")
                    with col3:
                        a_order_no = st.number_input("Номер заказа", 0, key="a_order_no")
                    with col4:
                        a_alloc_amt = st.number_input("Сумма на заказ", 0.0, key="a_alloc_amt")
                    a_comment = st.text_input("Комментарий", key="a_comment")

                    if st.button("💾 Добавить", key="a_save"):
                        try:
                            api.create_customs_alloc({
                                "topup_txn_id": selected_topup_id,
                                "pay_date": str(a_date),
                                "pay_amount": float(a_pay_amt) if a_pay_amt else None,
                                "order_no": int(a_order_no) if a_order_no else None,
                                "alloc_amount": float(a_alloc_amt) if a_alloc_amt else None,
                                "comment": a_comment or None,
                            })
                            st.success("Сохранено!")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Ошибка: {e}")

    # ── Lead Times ────────────────────────────────────────────────────────────
    with tab6:
        st.subheader("⏱ Lead Times (дней)")
        try:
            lts = api.get_lead_times()
        except Exception as e:
            st.error(f"Ошибка: {e}")
            lts = []

        if lts:
            for lt in lts:
                col1, col2 = st.columns([2, 1])
                with col1:
                    st.text(f"{lt['direction']}: {lt['days']} дней")
                with col2:
                    new_days = st.number_input(
                        f"Изменить {lt['direction']}",
                        value=lt["days"], min_value=1, max_value=365,
                        key=f"lt_{lt['direction']}"
                    )
                    if st.button(f"Сохранить", key=f"lt_save_{lt['direction']}"):
                        try:
                            api.upsert_lead_time({"direction": lt["direction"], "days": new_days})
                            st.success("✅")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Ошибка: {e}")
        else:
            st.info("Нажмите 'Инициализация данных' в боковом меню для загрузки дефолтных значений")
