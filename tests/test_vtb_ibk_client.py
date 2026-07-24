"""
Unit-тесты билдеров запросов VTB ИБК (read-only выписка).

Чистые функции — без БД/сети/КриптоПро. Проверяют, что XML-конверты и объект
подписи строятся строго по «Инструкции по подключению к ИБК ВТБ Бизнес»
(Приложения 1, 2, 3, 7, 10) и команды csptest/cprocsp-curl собираются верно.
"""

from backend.integrations import vtb_ibk_client as c


# ─── Объект подписи (Приложение 10) ──────────────────────────────────────────

def test_sign_object_has_fields_in_order_and_leading_space():
    obj = c.build_statement_sign_object(
        doc_date="16.06.2023", doc_number="995", custid="00000000",
        account="00000000000000000000", date_from="10.06.2023",
        date_to="16.06.2023", kbopid="818",
    )
    # объект — для windows-1251, с XML-декларацией
    assert 'encoding="windows-1251"' in obj
    assert "<Body>" in obj and "</Body>" in obj
    # §5.1.d: строки <Field…> начинаются с пробела
    for line in obj.splitlines():
        if line.lstrip().startswith("<Field"):
            assert line.startswith(" "), f"нет ведущего пробела: {line!r}"
    # порядок полей = STATEMENT_SIGN_FIELDS
    positions = [obj.index(f'FieldName="{f}"') for f in c.STATEMENT_SIGN_FIELDS]
    assert positions == sorted(positions)
    # типы данных из Приложения 10
    assert '<Field FieldName="DOCUMENTDATE" DataType="DATE">16.06.2023</Field>' in obj
    assert '<Field FieldName="CUSTID" DataType="INTEGER">00000000</Field>' in obj
    assert '<Field FieldName="ACCOUNT" DataType="STRING"><![CDATA[00000000000000000000]]></Field>' in obj


def test_sign_dcm_fields_constant_matches_spec():
    # Приложение 1: SignDCMFields для StatementQuery
    assert c.STATEMENT_SIGN_FIELDS == [
        "DOCUMENTDATE", "DOCUMENTNUMBER", "CUSTID", "SENDEROFFICIALS",
        "STATEMENTTYPE", "ACCOUNT", "DATEFROM", "DATETO", "KBOPID",
    ]


# ─── Предзаказ выписки: ImportSignedDocument (Приложение 1) ───────────────────

def test_statement_preorder_envelope_structure():
    xml = c.build_statement_preorder(
        custid="12345678", doc_date="16.06.2023", doc_number="995",
        account="40702810000000000001", date_from="10.06.2023",
        date_to="16.06.2023", kbopid="818", sign_b64="BASE64SIGN==", uid="SER1AL",
    )
    assert 'encoding="windows-1251"' in xml
    assert "urn:WSImportSignedDocumentIntf-WSImportSignedDocument" in xml
    assert ">ImportSignedDocument" in xml or ":ImportSignedDocument" in xml
    assert "<DocScheme xsi:type=\"xsd:string\">StatementQuery</DocScheme>" in xml
    assert "<DocVersion xsi:type=\"xsd:integer\">3</DocVersion>" in xml
    # DocData несёт BSDocument с нашими значениями (в CDATA)
    assert "<![CDATA[" in xml
    assert "<ACCOUNT>40702810000000000001</ACCOUNT>" in xml
    assert "<DATEFROM>10.06.2023</DATEFROM>" in xml
    assert "<KBOPID>818</KBOPID>" in xml
    # SignData несёт подпись и UID
    assert "BASE64SIGN==" in xml
    assert 'UID="SER1AL"' in xml
    assert "DOCUMENTDATE|DOCUMENTNUMBER|CUSTID" in xml  # SignDCMFields


# ─── Статус документа: GetDocStatus (Приложение 2) ───────────────────────────

def test_get_doc_status_envelope():
    xml = c.build_get_doc_status(custid="12345678", record_id="REC123", doc_scheme="StatementQuery")
    assert "urn:WSGetDocStatusIntf-WSGetDocStatus" in xml
    assert ":GetDocStatus" in xml
    assert "<RecordID xsi:type=\"xsd:int\">REC123</RecordID>" in xml
    assert "<DocScheme xsi:type=\"xsd:string\">StatementQuery</DocScheme>" in xml


# ─── Получение выписки: GetStatement (Приложение 3) ──────────────────────────

def test_get_statement_envelope_uses_iso_date():
    xml = c.build_get_statement(
        custid="12345678", account="40702810000000000001",
        bic="044525411", statement_date="2023-06-11",
    )
    assert "urn:WSGetStatementIntf-WSGetStatement" in xml
    assert ":GetStatement" in xml
    assert "40702810000000000001" in xml
    assert "044525411" in xml
    # дата выписки — ISO YYYY-MM-DD (в отличие от DD.MM.YYYY в предзаказе)
    assert "2023-06-11" in xml


# ─── Список счетов: GetAccountsList (Приложение 7) ───────────────────────────

def test_get_accounts_list_envelope():
    xml = c.build_get_accounts_list(custid="12345678")
    assert "urn:WSGetCustomerIntf-GetAccountsList" in xml
    assert ":GetAccountsList" in xml
    assert "<CustID xsi:type=\"xsd:int\">12345678</CustID>" in xml


# ─── Команды csptest / cprocsp-curl ──────────────────────────────────────────

def test_csptest_sign_argv_matches_spec():
    # §5.7: csptest -sfsign -sign -in <in> -out <out> -my <thumb> -base64 -alg GOST12_256 -add -detached
    argv = c.csptest_sign_argv(in_path="/t/obj.txt", out_path="/t/obj.sig", thumbprint="AA11BB")
    assert argv[0].endswith("csptest")
    joined = " ".join(argv)
    assert "-sfsign -sign" in joined
    assert "-in /t/obj.txt" in joined
    assert "-out /t/obj.sig" in joined
    assert "-my AA11BB" in joined
    assert "-base64" in joined
    assert "-alg GOST12_256" in joined
    assert "-add" in joined and "-detached" in joined


def test_curl_post_argv_uses_gost_curl_and_cert():
    argv = c.curl_post_argv(
        url="https://h2h.db.vtb.ru/bss/s/bsi.dll?soap/",
        body_path="/t/req.xml", thumbprint="AA11BB",
    )
    joined = " ".join(argv)
    assert "/opt/cprocsp/bin/amd64/curl" in argv[0]
    assert "--cert" in joined and "AA11BB" in joined
    assert "@/t/req.xml" in joined
    assert "Content-Type: text/xml" in joined
    assert "https://h2h.db.vtb.ru/bss/s/bsi.dll?soap/" in joined


def test_strip_sig_whitespace():
    # §5.8: из .sig убрать \r и \n
    raw = "MIIF\r\nHwYJ\nKoZ==\r\n"
    assert c.strip_sig_whitespace(raw) == "MIIFHwYJKoZ=="


def test_endpoints_known():
    assert c.ENDPOINT_PROD == "https://h2h.db.vtb.ru/bss/s/bsi.dll?soap/"
    assert "db-test.vtb.ru" in c.ENDPOINT_TEST


def test_encode_cp1251_roundtrip_cyrillic():
    data = c.encode_cp1251("Платёж 100")
    assert isinstance(data, bytes)
    assert data.decode("cp1251") == "Платёж 100"


# ─── Разбор ответов ──────────────────────────────────────────────────────────

def test_parse_record_id_from_response():
    resp = '<SOAP-ENV:Body><NS1:Resp><RecordID xsi:type="xsd:string">REF-42</RecordID></NS1:Resp></SOAP-ENV:Body>'
    assert c.parse_record_id(resp) == "REF-42"


def test_parse_doc_status_from_response():
    resp = "<Resp><DocStatus>17043</DocStatus></Resp>"
    assert c.parse_doc_status(resp) == c.STATUS_READY


def test_extract_tag_handles_namespace_and_cdata():
    assert c._extract_tag("<ns2:Foo><![CDATA[hello]]></ns2:Foo>", "Foo") == "hello"
    assert c._extract_tag("<Bar>x</Bar>", "Missing") is None


def test_extract_statement_payload_fallback_to_whole_response():
    # нет <StatementDoc> → возвращает весь ответ (best-effort до companion-спеки)
    assert c.extract_statement_payload("<Resp>raw</Resp>") == "<Resp>raw</Resp>"


def test_statement_parser_is_seam_returns_empty():
    rows, skipped = c.parse_statement_to_norm("<any/>")
    assert rows == [] and skipped == 0


def test_status_codes():
    assert (c.STATUS_RETRY, c.STATUS_REQ_ERROR, c.STATUS_READY) == ("15003", "15033", "17043")
