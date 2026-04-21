"""
Enums used across all models.
"""

import enum


class EventType2(str, enum.Enum):
    INTERNAL_TRANSFER = "INTERNAL_TRANSFER"
    FX_BUY = "FX_BUY"
    CUSTOMS_PAYMENT = "CUSTOMS_PAYMENT"
    OPER = "OPER"
    # Deposit events (Phase 1: counterparties-loans)
    DEPOSIT_PLACE = "DEPOSIT_PLACE"  # placement of funds into deposit (is_cashflow2=0)
    DEPOSIT_RETURN = "DEPOSIT_RETURN"  # return of deposit funds (is_cashflow2=0)
    DEPOSIT_INTEREST = "DEPOSIT_INTEREST"  # interest earned on deposit (is_cashflow2=1)


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
    WEIGHT = "WEIGHT"  # евро за кг
    AREA = "AREA"  # евро за м²
    INVOICE = "INVOICE"  # % от инвойса


class FactoryOrderStatus(str, enum.Enum):
    FORMING = "FORMING"  # формируется (позиции добавляются / не всё распределено)
    DISTRIBUTED = "DISTRIBUTED"  # полностью распределён по машинам
    CLOSED = "CLOSED"  # all vehicles with items >= SHIPPED


class SupplierCountry(str, enum.Enum):
    CHINA = "CHINA"
    RUSSIA = "RUSSIA"


class VehicleStatus(str, enum.Enum):
    FORMING = "FORMING"  # наполняется товаром
    SHIPPED = "SHIPPED"  # отгружен с фабрики  # noqa: RUF003
    CUSTOMS = "CUSTOMS"  # на таможне
    DISPATCHED = "DISPATCHED"  # отправлена с таможни на склад  # noqa: RUF003
    DELIVERED = "DELIVERED"  # принята на складе
