"""
Shared fixtures for DDS tests.
"""

import io
import pandas as pd
import pytest

# Load API test fixtures (client, auth_headers, db_session)
pytest_plugins = ["tests.conftest_api"]


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


@pytest.fixture
def vtb_multi_excel() -> bytes:
    """Generate a multi-sheet VTB bank statement (3 accounts in one file).
    
    Mimics real VTB format: each sheet = one account.
    Sheet name = account number.
    Row 1: ВЫПИСКА
    Row 2: Номер счета: <acc>, Валюта: <currency info>
    Row 3-6: metadata
    Row 7: column headers
    Row 8+: data
    """
    import openpyxl
    wb = openpyxl.Workbook()
    
    # Sheet 1: RUB deposit (will be skipped — usually not needed, but parsed)
    ws1 = wb.active
    ws1.title = "42102810316110029573"
    ws1.append(["ВЫПИСКА"])
    ws1.append(["Номер счета:", "42102810316110029573", "Валюта:", "Валюта 643, Российский рубль"])
    ws1.append(["Начальная дата: ", "01.02.2026", "Конечная дата: ", "06.03.2026"])
    ws1.append(["Входящий остаток RUB:", 0, "Исходящий остаток RUB:", 0])
    ws1.append([None])
    ws1.append([None])
    ws1.append(["Дата", "Номер", "Вид операции", "Контрагент", "ИНН контрагента",
                "БИК банка контрагента", "Счет контрагента", "Дебет, RUR", "Кредит, RUR", "Назначение"])
    ws1.append(["01.02.2026", "100", "01", "ООО Тест", "7701234567", "044525411",
                "40702810000000001234", 50000, 0, "Размещение депозита"])
    
    # Sheet 2: RUB main
    ws2 = wb.create_sheet("40702810400810052145")
    ws2.append(["ВЫПИСКА"])
    ws2.append(["Номер счета:", "40702810400810052145", "Валюта:", "Валюта 643, Российский рубль"])
    ws2.append(["Начальная дата: ", "01.02.2026", "Конечная дата: ", "06.03.2026"])
    ws2.append(["Входящий остаток RUB:", 44922.18, "Исходящий остаток RUB:", 310355.96])
    ws2.append([None])
    ws2.append([None])
    ws2.append(["Дата", "Номер", "Вид операции", "Контрагент", "ИНН контрагента",
                "БИК банка контрагента", "Счет контрагента", "Дебет, RUR", "Кредит, RUR", "Назначение"])
    ws2.append(["03.02.2026", "407", "01", "ООО Партнер", "1800027275", "044525450",
                "40702810800000001893", 0, 6900000, "Перевод собственных средств"])
    ws2.append(["04.02.2026", "537451", "01", "ООО Поставщик", "7704217370", "044525593",
                "40702810901300010687", 0, 286032.91, "Оплата по договору"])
    
    # Sheet 3: CNY
    ws3 = wb.create_sheet("40702156916110000346")
    ws3.append(["ВЫПИСКА"])
    ws3.append(["Номер счета:", "40702156916110000346", "Валюта:", "Валюта 156, Китайский юань"])
    ws3.append(["Начальная дата: ", "01.02.2026", "Конечная дата: ", "06.03.2026"])
    ws3.append(["Входящий остаток CNY:", 621.99, "Исходящий остаток CNY:", 58222.79])
    ws3.append(["                                     RUR:", 6760.35, "RUR:", 660747.15])
    ws3.append([None])
    ws3.append(["Дата", "Номер", "Вид операции", "Контрагент", "ИНН контрагента",
                "БИК банка контрагента", "Счет контрагента", "Дебет, CNY", "Кредит, CNY",
                "Дебет, RUR", "Кредит, RUR", "Назначение"])
    ws3.append(["05.02.2026", "18", "01", "БАНК ВТБ (ПАО)", "7702070139", "044525411",
                "30301156700810000001", 106000, 0, 1171459, 0, "Перевод CNY"])
    
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def wb_multi_xml_xls() -> bytes:
    """Generate a multi-account WB statement in XML SpreadsheetML format.
    
    Two accounts in one sheet, separated by ИТОГО row.
    Format: XML SpreadsheetML (.xls)
    """
    xml = '''<?xml version="1.0" encoding="utf-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Выписки">
  <Table>
   <Row><Cell><Data ss:Type="String">Выписка по счету 40702810500001001752 RUR (Пассивный)</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String">ООО "ВБ Банк" БИК 044525450</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String">ООО "ТЕСТ"</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">За период с 01.02.2026 по 06.03.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">Дата формирования информации 07.03.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">Дата последней операции 06.03.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">Входящий остаток:</Data></Cell><Cell><Data ss:Type="String">0.00 на 01.02.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell></Row>
   <Row>
    <Cell><Data ss:Type="String">Документ</Data></Cell>
    <Cell><Data ss:Type="String">Дата операции</Data></Cell>
    <Cell><Data ss:Type="String">Корреспондент</Data></Cell>
    <Cell><Data ss:Type="String">Вх.остаток</Data></Cell>
    <Cell><Data ss:Type="String">Оборот Дт</Data></Cell>
    <Cell><Data ss:Type="String">Оборот Кт</Data></Cell>
    <Cell><Data ss:Type="String">Назначение платежа</Data></Cell>
   </Row>
   <Row>
    <Cell><Data ss:Type="String">Наименование</Data></Cell>
    <Cell><Data ss:Type="String">ИНН</Data></Cell>
    <Cell><Data ss:Type="String">КПП</Data></Cell>
    <Cell><Data ss:Type="String">Счет</Data></Cell>
    <Cell><Data ss:Type="String">БИК</Data></Cell>
   </Row>
   <Row>
    <Cell><Data ss:Type="String">Пор. № 100</Data></Cell>
    <Cell><Data ss:Type="String">01.02.2026</Data></Cell>
    <Cell><Data ss:Type="String">РВБ ООО</Data></Cell>
    <Cell><Data ss:Type="String">9714053621</Data></Cell>
    <Cell><Data ss:Type="String">507401001</Data></Cell>
    <Cell><Data ss:Type="String">40702810825620001712</Data></Cell>
    <Cell><Data ss:Type="String">044525411</Data></Cell>
    <Cell><Data ss:Type="Number">0</Data></Cell>
    <Cell><Data ss:Type="String"></Data></Cell>
    <Cell><Data ss:Type="Number">500000</Data></Cell>
    <Cell><Data ss:Type="String">Оплата за товар</Data></Cell>
   </Row>
   <Row>
    <Cell><Data ss:Type="String">Пор. № 101</Data></Cell>
    <Cell><Data ss:Type="String">01.02.2026</Data></Cell>
    <Cell><Data ss:Type="String">ООО "ТЕСТ"</Data></Cell>
    <Cell><Data ss:Type="String">1800027275</Data></Cell>
    <Cell><Data ss:Type="String">370001001</Data></Cell>
    <Cell><Data ss:Type="String">40702810800000001893</Data></Cell>
    <Cell><Data ss:Type="String">044525450</Data></Cell>
    <Cell><Data ss:Type="Number">500000</Data></Cell>
    <Cell><Data ss:Type="Number">495000</Data></Cell>
    <Cell><Data ss:Type="String"></Data></Cell>
    <Cell><Data ss:Type="String">Перевод денежных средств</Data></Cell>
   </Row>
   <Row><Cell><Data ss:Type="String">ИТОГО:</Data></Cell><Cell><Data ss:Type="Number">495000</Data></Cell><Cell><Data ss:Type="Number">500000</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">Исходящий остаток:</Data></Cell><Cell><Data ss:Type="String">5000.00 на 06.03.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell></Row>
   <Row><Cell><Data ss:Type="String">Выписка по счету 40702810800000001893 RUR (Пассивный)</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String">ООО "ВБ Банк" БИК 044525450</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String">ООО "ТЕСТ"</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">За период с 01.02.2026 по 06.03.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">Дата формирования информации 07.03.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">Дата последней операции 06.03.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">Входящий остаток:</Data></Cell><Cell><Data ss:Type="String">100000.00 на 01.02.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell></Row>
   <Row>
    <Cell><Data ss:Type="String">Документ</Data></Cell>
    <Cell><Data ss:Type="String">Дата операции</Data></Cell>
    <Cell><Data ss:Type="String">Корреспондент</Data></Cell>
    <Cell><Data ss:Type="String">Вх.остаток</Data></Cell>
    <Cell><Data ss:Type="String">Оборот Дт</Data></Cell>
    <Cell><Data ss:Type="String">Оборот Кт</Data></Cell>
    <Cell><Data ss:Type="String">Назначение платежа</Data></Cell>
   </Row>
   <Row>
    <Cell><Data ss:Type="String">Наименование</Data></Cell>
    <Cell><Data ss:Type="String">ИНН</Data></Cell>
    <Cell><Data ss:Type="String">КПП</Data></Cell>
    <Cell><Data ss:Type="String">Счет</Data></Cell>
    <Cell><Data ss:Type="String">БИК</Data></Cell>
   </Row>
   <Row>
    <Cell><Data ss:Type="String">Пор. № 200</Data></Cell>
    <Cell><Data ss:Type="String">03.02.2026</Data></Cell>
    <Cell><Data ss:Type="String">ООО "ТЕСТ"</Data></Cell>
    <Cell><Data ss:Type="String">1800027275</Data></Cell>
    <Cell><Data ss:Type="String">370001001</Data></Cell>
    <Cell><Data ss:Type="String">40702810500001001752</Data></Cell>
    <Cell><Data ss:Type="String">044525450</Data></Cell>
    <Cell><Data ss:Type="Number">100000</Data></Cell>
    <Cell><Data ss:Type="String"></Data></Cell>
    <Cell><Data ss:Type="Number">75000</Data></Cell>
    <Cell><Data ss:Type="String">Перевод входящий</Data></Cell>
   </Row>
   <Row><Cell><Data ss:Type="String">ИТОГО:</Data></Cell><Cell><Data ss:Type="Number">0</Data></Cell><Cell><Data ss:Type="Number">75000</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell><Cell><Data ss:Type="String">Исходящий остаток:</Data></Cell><Cell><Data ss:Type="String">175000.00 на 06.03.2026</Data></Cell></Row>
   <Row><Cell><Data ss:Type="String"></Data></Cell></Row>
  </Table>
 </Worksheet>
</Workbook>'''
    return xml.encode('utf-8')


