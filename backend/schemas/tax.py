"""
Tax schemas — request/response for tax rate endpoints.
"""

from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, ConfigDict


class TaxRateMonth(BaseModel):
    """Single month rate data."""
    month: int
    usn_rate: Decimal = Decimal("0")
    nds_rate: Decimal = Decimal("0")
    cost_as_expense: bool = False


class TaxRateSaveRequest(BaseModel):
    """Save rates for one brand for one year."""
    brand: str = ""
    year: int
    tax_regime: str = "usn_income"
    months: List[TaxRateMonth]


class TaxRateRegimeChangeRequest(BaseModel):
    """Change tax regime for a brand."""
    brand: str
    tax_regime: str


class TaxRateBrandData(BaseModel):
    """Response: one brand's rates for a year."""
    brand: str
    tax_regime: str
    months: List[TaxRateMonth]

    model_config = ConfigDict(from_attributes=True)


class TaxRatesResponse(BaseModel):
    """Response: all brands' rates for a year + available brands list."""
    year: int
    brands: List[str]
    rates: List[TaxRateBrandData]
