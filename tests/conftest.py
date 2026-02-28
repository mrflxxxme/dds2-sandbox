"""
Shared fixtures for DDS tests.
"""

import io
import pandas as pd
import pytest


@pytest.fixture
def vtb_rub_excel() -> bytes:
    """Generate a minimal VTB RUB bank statement Excel file."""
    data = {
        "Дата": ["01.02.2024", "02.02.2024", "ИТОГО"],
        "Номер": ["1", "2", ""],
        "Вид операции": ["Оплата", "Поступление", ""],
        "Контрагент": ["ООО Ромашка", "ИП Иванов", ""],
        "ИНН контрагента": ["7701234567", "770987654321", ""],
        "БИК банка контрагента": ["044525225", "044525226", ""],
        "Счет контрагента": ["40702810000000001234", "40702810000000005678", ""],
        "Дебет RUR": [50000.00, 0, 50000.00],
        "Кредит RUR": [0, 100000.00, 100000.00],
        "Назначение": [
            "Оплата по договору №1 за товар",
            "Оплата по INVOICE INV-2024-001 за услуги",
            "",
        ],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def vtb_rub_excel_bad_dates() -> bytes:
    """VTB RUB statement with some unparseable dates."""
    data = {
        "Дата": ["01.02.2024", "NOT_A_DATE", "03.02.2024"],
        "Номер": ["1", "2", "3"],
        "Вид операции": ["Оплата", "Оплата", "Оплата"],
        "Контрагент": ["ООО A", "ООО B", "ООО C"],
        "ИНН контрагента": ["111", "222", "333"],
        "БИК банка контрагента": ["000", "000", "000"],
        "Счет контрагента": ["40702810000000001111", "40702810000000002222", "40702810000000003333"],
        "Дебет RUR": [1000, 2000, 3000],
        "Кредит RUR": [0, 0, 0],
        "Назначение": ["p1", "p2", "p3"],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def vtb_cny_excel() -> bytes:
    """Generate a minimal VTB CNY bank statement."""
    data = {
        "Дата": ["15.03.2024"],
        "Номер": ["1"],
        "Вид операции": ["Перевод"],
        "Контрагент": ["SUPPLIER CO LTD"],
        "ИНН контрагента": [""],
        "БИК банка контрагента": [""],
        "Счет контрагента": ["CN123456789"],
        "Дебет CNY": [5000.00],
        "Кредит CNY": [0],
        "Дебет RUR": [100000.00],
        "Кредит RUR": [0],
        "Назначение": ["Payment ANNEX #5 according to contract"],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


@pytest.fixture
def wb_excel() -> bytes:
    """Generate a minimal WB bank statement."""
    data = {
        "Документ": ["ПП-1", "Наименование", "ПП-2"],
        "Дата операции": ["10.01.2024", "Корреспондент", "11.01.2024"],
        "Корреспондент": ["ООО Вайлдберриз", "Наименование", "ООО Логистик"],
        "ИНН": ["7721546864", "", "1234567890"],
        "КПП": ["", "", ""],
        "Счет": ["40702810000000009999", "", "40702810000000008888"],
        "БИК": ["", "", ""],
        "Вх.остаток": [0, 0, 0],
        "Оборот Дт": [0, 0, 15000],
        "Оборот Кт": [500000, 0, 0],
        "Назначение платежа": [
            "Перечисление по реестру Wildberries",
            "",
            "Комиссия за банковское обслуживание",
        ],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()
