"""
Reference data: accounts, cp_categories, overrides, opening balances.
"""

import streamlit as st
import pandas as pd
from frontend import api_client as api


def render():
    st.title("⚙️ Справочники")

    tab1, tab2, tab3, tab4 = st.tabs([
        "Счета", "Категории контрагентов", "Переопределения", "Начальные остатки"
    ])

    # ── Accounts ──────────────────────────────────────────────────────────────
    with tab1:
        st.subheader("Счета (REF_ACCOUNTS)")
        try:
            accounts = api.get_accounts()
        except Exception as e:
            st.error(f"Ошибка: {e}")
            accounts = []

        if accounts:
            df = pd.DataFrame(accounts)
            st.dataframe(
                df[["id", "account", "bank", "currency", "account_type",
                    "is_our_account", "account_name", "is_customs_payee"]].rename(columns={
                    "id": "ID", "account": "Счёт", "bank": "Банк", "currency": "Валюта",
                    "account_type": "Тип", "is_our_account": "Наш",
                    "account_name": "Название", "is_customs_payee": "Таможня",
                }),
                hide_index=True, use_container_width=True
            )

            delete_id = st.number_input("Удалить счёт (ID)", 0, key="del_acc_id")
            if st.button("🗑 Удалить", key="del_acc_btn") and delete_id:
                try:
                    api.delete_account(int(delete_id))
                    st.success("Удалено!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")

        with st.expander("➕ Добавить / изменить счёт"):
            col1, col2, col3 = st.columns(3)
            with col1:
                acc_no = st.text_input("Номер счёта*", key="acc_no")
                acc_bank = st.text_input("Банк (VTB/WB/CUSTOMS)", key="acc_bank")
                acc_curr = st.selectbox("Валюта", ["RUB", "CNY"], key="acc_curr")
            with col2:
                acc_type = st.selectbox("Тип", ["OPER", "TRANSIT", "CUSTOMS_PAYEE"], key="acc_type")
                acc_name = st.text_input("Название (читаемое)", key="acc_name")
            with col3:
                acc_our = st.checkbox("Наш счёт", value=True, key="acc_our")
                acc_customs = st.checkbox("Таможенный получатель", value=False, key="acc_customs")

            if st.button("💾 Сохранить счёт", key="acc_save") and acc_no:
                try:
                    api.upsert_account({
                        "account": acc_no,
                        "bank": acc_bank,
                        "currency": acc_curr,
                        "account_type": acc_type,
                        "account_name": acc_name,
                        "is_our_account": acc_our,
                        "is_customs_payee": acc_customs,
                    })
                    st.success("Сохранено!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")

    # ── CP Categories ─────────────────────────────────────────────────────────
    with tab2:
        st.subheader("Категории контрагентов (REF_CP_CATEGORY)")
        try:
            cats = api.get_cp_categories()
        except Exception as e:
            st.error(f"Ошибка: {e}")
            cats = []

        if cats:
            df = pd.DataFrame(cats)
            st.dataframe(
                df[["id", "cp_key", "cp_name", "cat_lvl1", "cat_lvl2", "note"]].rename(columns={
                    "id": "ID", "cp_key": "CP Key", "cp_name": "Имя",
                    "cat_lvl1": "Категория 1", "cat_lvl2": "Категория 2", "note": "Примечание",
                }),
                hide_index=True, use_container_width=True
            )
            st.caption(f"Всего: {len(cats)} контрагентов")

            del_id = st.number_input("Удалить (ID)", 0, key="del_cp_id")
            if st.button("🗑 Удалить", key="del_cp_btn") and del_id:
                try:
                    api.delete_cp_category(int(del_id))
                    st.success("Удалено!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")

        with st.expander("➕ Добавить категорию контрагента"):
            col1, col2, col3 = st.columns(3)
            with col1:
                cpc_key = st.text_input("CP Key (ИНН или имя lower)", key="cpc_key")
                cpc_name = st.text_input("Название контрагента", key="cpc_name")
            with col2:
                cpc_cat1 = st.text_input("Категория 1", key="cpc_cat1")
                cpc_cat2 = st.text_input("Категория 2", key="cpc_cat2")
            with col3:
                cpc_note = st.text_input("Примечание", key="cpc_note")

            if st.button("💾 Сохранить", key="cpc_save") and cpc_key:
                try:
                    api.upsert_cp_category({
                        "cp_key": cpc_key,
                        "cp_name": cpc_name,
                        "cat_lvl1": cpc_cat1,
                        "cat_lvl2": cpc_cat2,
                        "note": cpc_note,
                    })
                    st.success("Сохранено!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")

    # ── Overrides ─────────────────────────────────────────────────────────────
    with tab3:
        st.subheader("Переопределения категорий (REF_OVERRIDE)")
        try:
            ovrs = api.get_overrides()
        except Exception as e:
            st.error(f"Ошибка: {e}")
            ovrs = []

        if ovrs:
            df = pd.DataFrame(ovrs)
            if "updated_at" in df.columns:
                df["updated_at"] = pd.to_datetime(df["updated_at"]).dt.strftime("%d.%m.%Y %H:%M")
            st.dataframe(
                df[["id", "txn_id", "cat_lvl1", "cat_lvl2", "comment", "updated_at"]].rename(columns={
                    "id": "ID", "txn_id": "TXN ID",
                    "cat_lvl1": "Категория 1", "cat_lvl2": "Категория 2",
                    "comment": "Комментарий", "updated_at": "Изменено",
                }),
                hide_index=True, use_container_width=True
            )

            del_id = st.number_input("Удалить override (ID)", 0, key="del_ovr_id")
            if st.button("🗑 Удалить", key="del_ovr_btn") and del_id:
                try:
                    api.delete_override(int(del_id))
                    st.success("Удалено!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")
        else:
            st.info("Переопределений нет.")

    # ── Opening Balances ──────────────────────────────────────────────────────
    with tab4:
        st.subheader("Начальные остатки")
        try:
            obs = api.get_opening_balances()
        except Exception as e:
            st.error(f"Ошибка: {e}")
            obs = []

        if obs:
            df = pd.DataFrame(obs)
            st.dataframe(df, hide_index=True, use_container_width=True)

        with st.expander("➕ Добавить / изменить начальный остаток"):
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                ob_date = st.date_input("Дата остатка", key="ob_date")
            with col2:
                ob_acc = st.text_input("Номер счёта", key="ob_acc")
            with col3:
                ob_curr = st.selectbox("Валюта", ["RUB", "CNY"], key="ob_curr")
            with col4:
                ob_bal = st.number_input("Остаток", 0.0, key="ob_bal")

            if st.button("💾 Сохранить", key="ob_save") and ob_acc:
                try:
                    api.upsert_opening_balance({
                        "date_open": str(ob_date),
                        "account": ob_acc,
                        "currency": ob_curr,
                        "opening_balance": float(ob_bal),
                    })
                    st.success("Сохранено!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Ошибка: {e}")
