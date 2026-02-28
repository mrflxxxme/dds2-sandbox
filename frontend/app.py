"""
DDS — Система управленческого учёта
Streamlit multi-page app
"""

import sys
import os
sys.path.insert(0, "/app")

import streamlit as st
from frontend import api_client as api

st.set_page_config(
    page_title="ДДС — Управленческий учёт",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─── Login gate ───────────────────────────────────────────────────────────────

def _show_login():
    st.markdown("## 💼 ДДС — Вход в систему")
    with st.form("login_form"):
        username = st.text_input("Логин")
        password = st.text_input("Пароль", type="password")
        submitted = st.form_submit_button("Войти", use_container_width=True)

    if submitted:
        if not username or not password:
            st.error("Введите логин и пароль")
            return
        try:
            result = api.login(username, password)
            st.session_state["token"] = result["access_token"]
            st.session_state["username"] = username
            api.set_token(result["access_token"])
            st.rerun()
        except Exception as e:
            st.error(f"Ошибка входа: {e}")


# Check authentication
if "token" not in st.session_state:
    _show_login()
    st.stop()

# Set token for API calls
api.set_token(st.session_state["token"])


# ─── Main app ─────────────────────────────────────────────────────────────────

# Sidebar navigation
st.sidebar.title("💼 ДДС Система")
st.sidebar.caption(f"Пользователь: {st.session_state.get('username', '—')}")
if st.sidebar.button("🚪 Выйти"):
    for key in ["token", "username"]:
        st.session_state.pop(key, None)
    api.set_token(None)
    st.rerun()

st.sidebar.markdown("---")

pages = {
    "🏠 Дашборд": "dashboard",
    "📥 Импорт выписок": "import_page",
    "📋 Операции": "transactions",
    "🔴 INBOX / Неразнесённые": "inbox",
    "📊 Отчёты": "reports",
    "📦 Планирование": "planning",
    "🏷 Себестоимость": "cost",
    "⚙️ Справочники": "refs",
}

selected = st.sidebar.radio("Навигация", list(pages.keys()), label_visibility="collapsed")
page_key = pages[selected]

st.sidebar.markdown("---")

# Quick seed button
if st.sidebar.button("🔧 Инициализация данных"):
    try:
        api.seed_defaults()
        st.sidebar.success("Базовые данные загружены")
    except Exception as e:
        st.sidebar.error(f"Ошибка: {e}")

# Route to page
if page_key == "dashboard":
    from frontend.pages import dashboard
    dashboard.render()
elif page_key == "import_page":
    from frontend.pages import import_page
    import_page.render()
elif page_key == "transactions":
    from frontend.pages import transactions
    transactions.render()
elif page_key == "inbox":
    from frontend.pages import inbox
    inbox.render()
elif page_key == "reports":
    from frontend.pages import reports
    reports.render()
elif page_key == "planning":
    from frontend.pages import planning
    planning.render()
elif page_key == "cost":
    from frontend.pages import cost
    cost.render()
elif page_key == "refs":
    from frontend.pages import refs
    refs.render()
