"""
Reference schemas: Account, CounterpartyCategory, Override, OpeningBalance, CategoryRef.
"""

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class AccountSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    account: str
    bank: str
    currency: str
    account_type: str | None = None
    is_our_account: bool = True
    account_name: str | None = None
    is_customs_payee: bool = False


class CounterpartyCategorySchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    cp_key: str
    cp_name: str | None = None
    cat_lvl1: str | None = None
    cat_lvl2: str | None = None
    note: str | None = None


class OverrideSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    txn_id: str
    cat_lvl1: str | None = None
    cat_lvl2: str | None = None
    order_id: str | None = None
    comment: str | None = None
    updated_at: datetime | None = None


class OpeningBalanceSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    date_open: date
    account: str
    currency: str
    opening_balance: Decimal


class CategoryRefSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    direction: str = "expense"
    cat_lvl1: str
    cat_lvl2: str | None = None


class CategoryRefCreate(BaseModel):
    """Input: add a category reference."""

    cat_lvl1: str
    cat_lvl2: str | None = None
    direction: str = "expense"
    sort_order: int = 0


class ProductTagSchema(BaseModel):
    """Output for ProductTag."""

    model_config = ConfigDict(from_attributes=True)
    id: int | None = None
    name: str
    color: str = "#3B82F6"


class ProductTagMappingPayload(BaseModel):
    """Input payload to map tags to nm_ids."""

    nm_ids: list[int]
    add_tags: list[int]
    remove_tags: list[int]


VALID_PRODUCT_STATUSES = {"active", "new", "clearance"}


class ProductStatusPayload(BaseModel):
    """Set status for one product."""

    nm_id: int
    status: str

    def model_post_init(self, __context: object) -> None:
        if self.status not in VALID_PRODUCT_STATUSES:
            raise ValueError(f"status must be one of {VALID_PRODUCT_STATUSES}")


class ProductStatusBulkPayload(BaseModel):
    """Bulk set status for multiple products."""

    nm_ids: list[int]
    status: str

    def model_post_init(self, __context: object) -> None:
        if self.status not in VALID_PRODUCT_STATUSES:
            raise ValueError(f"status must be one of {VALID_PRODUCT_STATUSES}")


class ImtAliasPayload(BaseModel):
    """Set alias for an imt_id group."""

    imt_id: int
    name: str


class ExcludedWarehousesPayload(BaseModel):
    """Input payload for setting excluded warehouses."""

    warehouses: list[str]


class ForecastRfDefaultDaysPayload(BaseModel):
    """Input payload for default RF→WB lead time (days), used when
    `WarehouseDeliveryTime` rows are missing for a fulfillment warehouse."""

    days: int
