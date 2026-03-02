"""
Enums used across all models.
"""

import enum


class EventType2(str, enum.Enum):
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
    FX_BUY = "FX_BUY"
    CUSTOMS_PAYMENT = "CUSTOMS_PAYMENT"
    OPER = "OPER"


class TransactionStatus(str, enum.Enum):
    OK = "OK"
    UNASSIGNED = "UNASSIGNED"
    NO_CASHFLOW = "NO_CASHFLOW"


class PurposeTag(str, enum.Enum):
    COMMISSION = "Комиссия"
    LOGISTICS = "Логистика"
    ORDER = "Заказ"
    OTHER = "Другое"


class DutyBasis(str, enum.Enum):
    WEIGHT = "WEIGHT"       # евро за кг
    AREA = "AREA"           # евро за м²
    INVOICE = "INVOICE"     # % от инвойса
