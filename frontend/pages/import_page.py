"""
Import page: load bank statements.
"""

import streamlit as st
import pandas as pd
from frontend import api_client as api


SOURCE_TYPES = {
    "VTB_RUB_MAIN": ("VTB RUB Основной", "40702810400810052145"),
    "VTB_RUB_TRANSIT": ("VTB RUB Транзит", "42102810316110029573"),
    "VTB_CNY": ("VTB CNY", "40702156916110000346"),
    "WB_MAIN": ("WB RUB Основной", "40702810800000001893"),
    "WB_PAYOUT": ("WB RUB Транзит/Payout", "4070281050001001752"),
}


def render():
    st.title("📥 Импорт банковских выписок")

    st.markdown("""
    Загрузите файл выписки в формате **.xlsx** (Excel).  
    Система автоматически распознает операции и применит бизнес-логику (категории, внутренние переводы, FX, таможня).
    """)

    col1, col2 = st.columns([2, 1])

    with col1:
        source_label = st.selectbox(
            "Источник / тип выписки",
            list(SOURCE_TYPES.keys()),
            format_func=lambda k: f"{k} — {SOURCE_TYPES[k][0]}",
        )
        acc_name, default_acc = SOURCE_TYPES[source_label]
        account_no = st.text_input("Номер счёта", value=default_acc)

        uploaded = st.file_uploader(
            "Выберите файл выписки (.xlsx)",
            type=["xlsx"],
            help="Файл должен быть в стандартном формате выписки банка",
        )

    with col2:
        st.markdown("**Соответствие листов:**")
        for k, (name, acc) in SOURCE_TYPES.items():
            st.markdown(f"- `{k}` → {name}")

    if uploaded and st.button("📤 Импортировать", type="primary"):
        with st.spinner("Импортируем выписку..."):
            try:
                result = api.upload_statement(
                    file_bytes=uploaded.read(),
                    filename=uploaded.name,
                    source_type=source_label,
                    account_no=account_no,
                )
                st.success(f"""
                ✅ **Импорт завершён**
                - Считано строк: **{result.get('rows_raw', 0)}**
                - Новых операций: **{result.get('rows_inserted', 0)}**
                - Пропущено дублей: **{result.get('rows_skipped', 0)}**
                """)
            except Exception as e:
                st.error(f"❌ Ошибка импорта: {e}")

    st.markdown("---")
    st.subheader("📋 Журнал импортов")

    try:
        logs = api.get_import_logs()
        if logs:
            df = pd.DataFrame(logs)
            df["imported_at"] = pd.to_datetime(df["imported_at"]).dt.strftime("%d.%m.%Y %H:%M")
            status_emoji = {"OK": "✅", "ERROR": "❌"}
            df["status"] = df["status"].map(lambda s: f"{status_emoji.get(s, '?')} {s}")
            st.dataframe(
                df[["imported_at", "filename", "source_type", "rows_raw",
                    "rows_inserted", "rows_skipped", "status", "error_msg"]].rename(columns={
                    "imported_at": "Дата", "filename": "Файл",
                    "source_type": "Тип", "rows_raw": "Строк",
                    "rows_inserted": "Новых", "rows_skipped": "Дублей",
                    "status": "Статус", "error_msg": "Ошибка",
                }),
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("Импортов ещё не было.")
    except Exception as e:
        st.error(f"Ошибка загрузки журнала: {e}")
