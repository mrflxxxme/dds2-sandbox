"""
DDS — Система управленческого учёта
Streamlit multi-page app
"""

import sys
import os
sys.path.insert(0, "/app")

import streamlit as st

st.set_page_config(
    page_title="ДДС — Управленческий учёт",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Sidebar navigation
st.sidebar.title("💼 ДДС Система")
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
    from frontend.api_client import seed_defaults
    try:
        seed_defaults()
        st.sidebar.success("✅ Базовые данные загружены")
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
