"""
INBOX: неразнесённые операции — назначение категорий.
"""

import streamlit as st
import pandas as pd
from frontend import api_client as api


# Common categories (can be extended)
CATEGORIES = {
    "Банки": ["Комиссии банка", "Проценты", "Прочее"],
    "Маркетплейсы": ["Wildberries", "OZON", "Прочее"],
    "Логистика": ["Доставка по РФ", "Доставка из Китая", "Таможня"],
    "Поставщики": ["Оплата товара", "Депозит", "Прочее"],
    "Фулфилмент": ["Склад / упаковка", "Прочее"],
    "Зарплата": ["Сотрудники", "ИП", "Прочее"],
    "Налоги": ["НДС", "Прибыль", "Взносы", "Прочее"],
    "Внутренние переводы": ["Между счетами", "Между юрлицами", "Перевод собственнику / займы"],
    "Прочие расходы": ["Офис", "IT", "Реклама", "Прочее"],
    "Прочие доходы": ["Возврат", "Прочее"],
}


def render():
    st.title("🔴 INBOX — Неразнесённые операции")

    try:
        unassigned = api.get_unassigned(limit=500)
    except Exception as e:
        st.error(f"Ошибка загрузки: {e}")
        return

    if not unassigned:
        st.success("🎉 Все операции разнесены! INBOX пуст.")
        return

    df = pd.DataFrame(unassigned)
    st.markdown(f"**Неразнесённых операций: {len(df)}**")

    # Summary stats
    col1, col2 = st.columns(2)
    with col1:
        total_rub = df[df["currency"] == "RUB"]["expense"].astype(float).sum()
        st.metric("Сумма расходов (RUB)", f"{total_rub:,.0f} ₽")
    with col2:
        total_cny = df[df["currency"] == "CNY"]["expense"].astype(float).sum()
        st.metric("Сумма расходов (CNY)", f"{total_cny:,.2f} ¥")

    st.markdown("---")

    # Quick assignment form
    st.subheader("⚡ Быстрая разноска")

    # Select transaction
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
            st.markdown("**Детали операции:**")
            st.text(f"Дата: {str(selected_txn.get('date', ''))[:10]}")
            st.text(f"Банк: {selected_txn.get('bank', '')}")
            st.text(f"Контрагент: {selected_txn.get('counterparty', '')}")
            st.text(f"Счёт: {selected_txn.get('counterparty_account', '')}")
            st.text(f"ИНН: {selected_txn.get('inn', '')}")
            st.text(f"Приход: {selected_txn.get('income', 0)} {selected_txn.get('currency', '')}")
            st.text(f"Расход: {selected_txn.get('expense', 0)} {selected_txn.get('currency', '')}")
            st.text(f"Тег: {selected_txn.get('purpose_tag', '')}")
            with st.expander("Назначение платежа"):
                st.text(selected_txn.get("purpose", ""))

        with col_form:
            st.markdown("**Назначить категорию:**")
            scope = st.radio(
                "Область применения",
                ["txn", "cp"],
                format_func=lambda x: "Только для этой операции" if x == "txn" else "Для всего контрагента",
                key="inbox_scope",
            )

            cat_lvl1 = st.selectbox("Категория 1", list(CATEGORIES.keys()), key="inbox_cat1")
            cat_lvl2 = st.selectbox(
                "Категория 2",
                CATEGORIES.get(cat_lvl1, ["Прочее"]),
                key="inbox_cat2",
            )
            comment = st.text_input("Комментарий (опц.)", key="inbox_comment")

            if st.button("✅ Применить", type="primary", key="inbox_apply"):
                try:
                    api.assign_category(
                        txn_id=txn_id,
                        cat_lvl1=cat_lvl1,
                        cat_lvl2=cat_lvl2,
                        scope=scope,
                        comment=comment if comment else None,
                        cp_key=cp_key,
                    )
                    st.success("Категория назначена!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")

    st.markdown("---")
    st.subheader("📋 Полный список неразнесённых")

    # Full table
    display_cols = ["date", "bank", "currency", "counterparty", "expense", "income",
                    "purpose_tag", "cp_key"]
    display_cols = [c for c in display_cols if c in df.columns]

    df_show = df[display_cols].copy()
    df_show["date"] = pd.to_datetime(df_show["date"]).dt.strftime("%d.%m.%Y")
    for col in ["expense", "income"]:
        if col in df_show.columns:
            df_show[col] = pd.to_numeric(df_show[col], errors="coerce").fillna(0)

    st.dataframe(
        df_show.rename(columns={
            "date": "Дата", "bank": "Банк", "currency": "Валюта",
            "counterparty": "Контрагент", "expense": "Расход",
            "income": "Приход", "purpose_tag": "Тег", "cp_key": "CP Key",
        }),
        hide_index=True,
        use_container_width=True,
    )
