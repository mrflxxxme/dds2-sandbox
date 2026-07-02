"""
VTB «Интеграционный Банк-Клиент» (ИБК) — Host-to-Host SOAP-клиент.

Реверс НЕ нужен — есть официальная «Инструкция по подключению к сервису
Интеграционный Банк-Клиент ВТБ Бизнес» (Instruction_VTB_API_21.04.24, Приложения
1–10). Транспорт — сырой SOAP-XML POST на ``bsi.dll`` (BS-Client), тело в
windows-1251. Аутентификация — GOST mutual-TLS клиентским сертификатом +
``CustID`` (идентификатор организации в СДБО). Подпись документов — detached CMS
ГОСТ-2012-256, base64, считается утилитой ``csptest`` (входит в КриптоПро CSP);
GOST-TLS — через ``cprocsp-curl``. Внешних python-зависимостей у билдеров нет.

SCOPE: read-only выписка (предзаказ → статус → получение по дням). Платёжное
поручение (``PayDocRu``) НЕ реализовано намеренно (необратимая операция с
деньгами — отдельная итерация за confirm-gate). См. [[vtb-ibk-integration]].

Этот модуль — чистые билдеры запросов и конструкторы команд (тестируются без
сети/КриптоПро). Исполнение (csptest/curl subprocess) и парсинг ответа — в
``services/vtb_ibk_service.py`` / следующей итерации.

⚠ ОСТРЫЕ УГЛЫ (проверить на тест-стенде ``i0.h2h.db-test.vtb.ru`` с реальным ключом):
  • Кодировка объекта подписи: байты, которые подписывает csptest, должны
    совпадать с тем, что банк проверяет (windows-1251 для DocData) — см.
    ``encode_cp1251``. Спека §5.1.b формулирует это туманно («UTF-8, но позже
    Win-1251»), поэтому финальная валидация — только живой ответ банка.
  • ``KBOPID`` — параметр операции/подразделения; реальное значение берётся из
    companion-спеки «Спецификация форматов сообщений ИБК» / GetAccountsList.
  • Синтаксис ``--cert`` у cprocsp-curl под Linux может отличаться от Windows-
    примера (``CurrentUser\\MY\\<thumbprint>``) — уточняется на тест-стенде.
"""

import logging
import os
import re
import subprocess
import tempfile
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger("dds.vtb")

# ─── Эндпоинты и бинарники ───────────────────────────────────────────────────

ENDPOINT_PROD = "https://h2h.db.vtb.ru/bss/s/bsi.dll?soap/"
ENDPOINT_TEST = "https://i0.h2h.db-test.vtb.ru/bss/s/bsi.dll?soap/"

CSPTEST_BIN = "/opt/cprocsp/bin/amd64/csptest"
CURL_BIN = "/opt/cprocsp/bin/amd64/curl"

# DocScheme/версия документа предзаказа выписки (Приложение 1)
DOC_SCHEME_STATEMENT = "StatementQuery"
DOC_VERSION_STATEMENT = 3

# Поля и порядок для подписи предзаказа выписки (Приложение 1, тег <SignDCMFields>)
STATEMENT_SIGN_FIELDS = [
    "DOCUMENTDATE", "DOCUMENTNUMBER", "CUSTID", "SENDEROFFICIALS",
    "STATEMENTTYPE", "ACCOUNT", "DATEFROM", "DATETO", "KBOPID",
]

# Тип данных каждого поля в объекте подписи (Приложение 10)
_SIGN_FIELD_TYPES = {
    "DOCUMENTDATE": "DATE", "DOCUMENTNUMBER": "STRING", "CUSTID": "INTEGER",
    "SENDEROFFICIALS": "STRING", "STATEMENTTYPE": "INTEGER", "ACCOUNT": "STRING",
    "DATEFROM": "DATE", "DATETO": "DATE", "KBOPID": "INTEGER",
}


# ─── Кодировка ───────────────────────────────────────────────────────────────

def encode_cp1251(text: str) -> bytes:
    """Тело запросов ВТБ — windows-1251 (как в DocData/BSDocument)."""
    return text.encode("cp1251")


def strip_sig_whitespace(sig_b64: str) -> str:
    """§5.8: из файла подписи .sig убрать переносы строк и возвраты каретки."""
    return sig_b64.replace("\r", "").replace("\n", "")


# ─── Объект подписи (Приложение 10) ──────────────────────────────────────────

def build_statement_sign_object(
    *, doc_date: str, doc_number: str, custid: str, account: str,
    date_from: str, date_to: str, kbopid: str,
    sender_officials: str = "", statement_type: str = "0",
) -> str:
    """
    «Объект подписания» для предзаказа выписки (Приложение 10) — именно его
    байты (в windows-1251) подписывает csptest, результат идёт в тег <Sign>.

    §5.1.d: каждая строка <Field…> начинается с пробела.
    """
    values = {
        "DOCUMENTDATE": doc_date, "DOCUMENTNUMBER": doc_number, "CUSTID": custid,
        "SENDEROFFICIALS": sender_officials, "STATEMENTTYPE": statement_type,
        "ACCOUNT": account, "DATEFROM": date_from, "DATETO": date_to, "KBOPID": kbopid,
    }
    lines = ['<?xml version="1.0" encoding="windows-1251"?>', "<Body>"]
    for name in STATEMENT_SIGN_FIELDS:
        dtype = _SIGN_FIELD_TYPES[name]
        val = values[name]
        if dtype == "STRING":
            body = f"<![CDATA[{val}]]>"
        else:
            body = _xml_escape(str(val))
        # ведущий пробел — обязателен (§5.1.d)
        lines.append(f' <Field FieldName="{name}" DataType="{dtype}">{body}</Field>')
    lines.append("</Body>")
    return "\n".join(lines)


# ─── SOAP-конверт ────────────────────────────────────────────────────────────

def _soap_envelope(inner: str, *, encoding: str = "windows-1251") -> str:
    return (
        f'<?xml version="1.0" encoding="{encoding}"?>\n'
        '<SOAP-ENV:Envelope '
        'xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/">\n'
        '<SOAP-ENV:Body SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">\n'
        f"{inner}\n"
        "</SOAP-ENV:Body>\n"
        "</SOAP-ENV:Envelope>"
    )


# ─── Предзаказ выписки: ImportSignedDocument (Приложение 1) ───────────────────

def build_statement_preorder(
    *, custid: str, doc_date: str, doc_number: str, account: str,
    date_from: str, date_to: str, kbopid: str, sign_b64: str, uid: str,
    sender_officials: str = "", statement_type: str = "0", fgetprintform: str = "0",
) -> str:
    """ImportSignedDocument с DocScheme=StatementQuery (Приложение 1). Подписан."""
    bsdoc = (
        '<?xml version="1.0" encoding="windows-1251"?>\n'
        "<BSDocument>\n"
        f"<DOCUMENTDATE>{_xml_escape(doc_date)}</DOCUMENTDATE>\n"
        f"<DOCUMENTNUMBER>{_xml_escape(doc_number)}</DOCUMENTNUMBER>\n"
        f"<CUSTID>{_xml_escape(custid)}</CUSTID>\n"
        f"<SENDEROFFICIALS>{_xml_escape(sender_officials)}</SENDEROFFICIALS>\n"
        f"<STATEMENTTYPE>{_xml_escape(statement_type)}</STATEMENTTYPE>\n"
        f"<ACCOUNT>{_xml_escape(account)}</ACCOUNT>\n"
        f"<DATEFROM>{_xml_escape(date_from)}</DATEFROM>\n"
        f"<DATETO>{_xml_escape(date_to)}</DATETO>\n"
        f"<KBOPID>{_xml_escape(kbopid)}</KBOPID>\n"
        f"<FGETPRINTFORM>{_xml_escape(fgetprintform)}</FGETPRINTFORM>\n"
        "</BSDocument>"
    )
    signdata = (
        '<?xml version="1.0" encoding="windows-1251"?>\n'
        "<SignInfo>\n"
        f"<SignDCMFields>{'|'.join(STATEMENT_SIGN_FIELDS)}</SignDCMFields>\n"
        f'<Sign SignNum="1" CryptLibID="8" UID="{uid}">{sign_b64}</Sign>\n'
        "</SignInfo>"
    )
    inner = (
        '<NS1:ImportSignedDocument xmlns:NS1="urn:WSImportSignedDocumentIntf-WSImportSignedDocument">\n'
        f'<CustID xsi:type="xsd:integer">{custid}</CustID>\n'
        f'<DocScheme xsi:type="xsd:string">{DOC_SCHEME_STATEMENT}</DocScheme>\n'
        f'<DocVersion xsi:type="xsd:integer">{DOC_VERSION_STATEMENT}</DocVersion>\n'
        f'<DocData xsi:type="xsd:string"><![CDATA[{bsdoc}]]></DocData>\n'
        f'<SignData xsi:type="xsd:string"><![CDATA[{signdata}]]></SignData>\n'
        '<RecordID xsi:type="xsd:string"></RecordID>\n'
        '<BSErrorCode xsi:type="xsd:string"></BSErrorCode>\n'
        '<BSError xsi:type="xsd:string"></BSError>\n'
        "</NS1:ImportSignedDocument>"
    )
    return _soap_envelope(inner)


# ─── Статус документа: GetDocStatus (Приложение 2) ───────────────────────────

def build_get_doc_status(*, custid: str, record_id: str, doc_scheme: str = DOC_SCHEME_STATEMENT,
                         remote_user: str = "") -> str:
    """GetDocStatus по референсу (Приложение 2). Подпись не требуется."""
    inner = (
        '<NS1:GetDocStatus xmlns:NS1="urn:WSGetDocStatusIntf-WSGetDocStatus">\n'
        f'<CustID xsi:type="xsd:int">{custid}</CustID>\n'
        f'<DocScheme xsi:type="xsd:string">{doc_scheme}</DocScheme>\n'
        f'<RecordID xsi:type="xsd:int">{record_id}</RecordID>\n'
        f'<RemoteUser xsi:type="xsd:string">{remote_user}</RemoteUser>\n'
        "</NS1:GetDocStatus>"
    )
    return _soap_envelope(inner, encoding="UTF-8")


# ─── Получение выписки: GetStatement (Приложение 3) ──────────────────────────

def build_get_statement(*, custid: str, account: str, bic: str, statement_date: str,
                        statement_type: str = "0", remote_user: str = "") -> str:
    """
    GetStatement за один день (Приложение 3). Подпись не требуется.
    ``statement_date`` — ISO ``YYYY-MM-DD`` (в отличие от DD.MM.YYYY в предзаказе).
    """
    inner = (
        '<NS1:GetStatement xmlns:NS1="urn:WSGetStatementIntf-WSGetStatement">\n'
        f'<CustID xsi:type="xsd:int">{custid}</CustID>\n'
        f'<Account xsi:type="xsd:string"><![CDATA[{account}]]></Account>\n'
        f'<BIC xsi:type="xsd:string"><![CDATA[{bic}]]></BIC>\n'
        f'<StatementDate xsi:type="xsd:string"><![CDATA[{statement_date}]]></StatementDate>\n'
        f'<StatementType xsi:type="xsd:string"><![CDATA[{statement_type}]]></StatementType>\n'
        '<StatementDoc xsi:type="xsd:string"><![CDATA[]]></StatementDoc>\n'
        '<SignData xsi:type="xsd:string"><![CDATA[]]></SignData>\n'
        '<BSError xsi:type="xsd:string"><![CDATA[]]></BSError>\n'
        '<BSErrorCode xsi:type="xsd:string"><![CDATA[]]></BSErrorCode>\n'
        f'<RemoteUser xsi:type="xsd:string"><![CDATA[{remote_user}]]></RemoteUser>\n'
        "</NS1:GetStatement>"
    )
    return _soap_envelope(inner)


# ─── Список счетов: GetAccountsList (Приложение 7) ───────────────────────────

def build_get_accounts_list(*, custid: str, account: str = "", bic: str = "",
                            remote_user: str = "") -> str:
    """GetAccountsList — счета организации с остатками (Приложение 7)."""
    inner = (
        '<NS1:GetAccountsList xmlns:NS1="urn:WSGetCustomerIntf-GetAccountsList">\n'
        f'<CustID xsi:type="xsd:int">{custid}</CustID>\n'
        f'<Account xsi:type="xsd:string"><![CDATA[{account}]]></Account>\n'
        f'<BIC xsi:type="xsd:string"><![CDATA[{bic}]]></BIC>\n'
        f'<RemoteUser xsi:type="xsd:string"><![CDATA[{remote_user}]]></RemoteUser>\n'
        "</NS1:GetAccountsList>"
    )
    return _soap_envelope(inner)


# ─── Команды КриптоПро ───────────────────────────────────────────────────────

def csptest_sign_argv(*, in_path: str, out_path: str, thumbprint: str) -> list[str]:
    """
    §5.7: detached CMS ГОСТ-2012-256, base64.
    csptest -sfsign -sign -in <in> -out <out> -my <thumbprint> -base64 -alg GOST12_256 -add -detached
    """
    return [
        CSPTEST_BIN, "-sfsign", "-sign",
        "-in", in_path, "-out", out_path,
        "-my", thumbprint, "-base64", "-alg", "GOST12_256", "-add", "-detached",
    ]


def curl_post_argv(*, url: str, body_path: str, thumbprint: str) -> list[str]:
    """
    POST тела запроса через GOST-TLS curl (cprocsp-curl) с клиентским сертификатом.
    ⚠ Точный формат ``--cert`` под Linux уточняется на тест-стенде.
    """
    return [
        CURL_BIN, "-sS",
        "--cert", thumbprint,
        "-H", "Content-Type: text/xml",
        "-d", f"@{body_path}",
        url,
    ]


# ─── Коды статусов документа (раздел 6 инструкции) ───────────────────────────

STATUS_RETRY = "15003"          # предзаказ ещё не готов — повторить GetDocStatus
STATUS_REQ_ERROR = "15033"      # ошибка в реквизитах запроса
STATUS_READY = "17043"          # готово — можно забирать выписку
STATUS_NEEDS_CONFIRM = "15063"  # требует подтверждения в ИБ (актуально для PayDocRu)

_SIGN_TIMEOUT = 30
_HTTP_TIMEOUT = 90


# ─── Исполнение: подпись (csptest) и транспорт (cprocsp-curl) ────────────────

def sign_detached(sign_object: str, thumbprint: str, *, timeout: int = _SIGN_TIMEOUT) -> str:
    """
    Подписать «объект подписания» → base64-строка для тега <Sign>.
    Пишет объект в windows-1251 во временный файл, гонит csptest, чистит \\r\\n.
    Требует КриптоПро CSP + закрытый ключ серта `thumbprint` в контейнере на сервере.
    """
    with tempfile.TemporaryDirectory() as tmp:
        in_path = os.path.join(tmp, "obj.txt")
        out_path = os.path.join(tmp, "obj.sig")
        with open(in_path, "wb") as f:
            f.write(encode_cp1251(sign_object))
        argv = csptest_sign_argv(in_path=in_path, out_path=out_path, thumbprint=thumbprint)
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)  # noqa: S603
        if proc.returncode != 0 or not os.path.exists(out_path):
            raise RuntimeError(
                f"csptest подпись не удалась (rc={proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace')[:400]}"
            )
        with open(out_path, "rb") as f:
            raw = f.read().decode("ascii", "replace")
    return strip_sig_whitespace(raw)


def post(*, url: str, body: str, thumbprint: str, timeout: int = _HTTP_TIMEOUT) -> str:
    """
    POST SOAP-тела на ИБК через GOST-TLS (cprocsp-curl) с клиентским сертификатом.
    Тело пишется в windows-1251. Возвращает текст ответа (best-effort decode).
    """
    with tempfile.TemporaryDirectory() as tmp:
        body_path = os.path.join(tmp, "req.xml")
        with open(body_path, "wb") as f:
            f.write(encode_cp1251(body))
        argv = curl_post_argv(url=url, body_path=body_path, thumbprint=thumbprint)
        proc = subprocess.run(argv, capture_output=True, timeout=timeout)  # noqa: S603
        if proc.returncode != 0:
            raise RuntimeError(
                f"cprocsp-curl POST не удался (rc={proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace')[:400]}"
            )
    return _decode_response(proc.stdout)


def _decode_response(data: bytes) -> str:
    for enc in ("utf-8", "cp1251"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


# ─── Разбор ответов (имена тегов финализируются по живому ответу тест-стенда) ─

def _extract_tag(xml: str, tag: str) -> str | None:
    """Текст первого <…:tag>…</…:tag> (с возможным namespace-префиксом), снимает CDATA."""
    m = re.search(rf"<(?:\w+:)?{tag}\b[^>]*>(.*?)</(?:\w+:)?{tag}>", xml, re.S)
    if not m:
        return None
    val = m.group(1).strip()
    cm = re.match(r"<!\[CDATA\[(.*)\]\]>\s*$", val, re.S)
    return cm.group(1).strip() if cm else val


def parse_record_id(resp: str) -> str | None:
    """Референс предзаказа/документа из ответа ImportSignedDocument (§6.1)."""
    return _extract_tag(resp, "RecordID")


def parse_doc_status(resp: str) -> str | None:
    """Код статуса из ответа GetDocStatus (§6.2): 15003/15033/17043/15063."""
    return _extract_tag(resp, "DocStatus")


def extract_statement_payload(resp: str) -> str | None:
    """
    Документ выписки из ответа GetStatement. Best-effort: содержимое <StatementDoc>
    либо весь ответ. ⚠ Точная структура — в companion-спеке «Спецификация форматов».
    """
    return _extract_tag(resp, "StatementDoc") or resp


def parse_statement_to_norm(payload: str) -> tuple[list[dict], int]:
    """
    SEAM: документ выписки → строки NORM_COLS (date/bank/account/currency/counterparty/
    inn/counterparty_account/purpose/income/expense).

    ⚠ НЕ реализовано: формата ответа выписки в присланной инструкции НЕТ (только
    запросы, Приложения 1–10). Достраивается по companion-спеке ИЛИ по живому ответу
    тест-стенда. Пока возвращает [] и логирует длину payload — чтобы по сырому ответу
    собрать маппинг (см. [[vtb-ibk-integration]]).
    """
    logger.warning(
        "VTB statement parser НЕ реализован — нужен формат ответа выписки "
        "(companion-спека/тест-стенд). payload_len=%d",
        len(payload or ""),
    )
    return [], 0
