"""
Shared utilities for AI tool implementations.

DecimalEncoder, _json serializer, and helpers used across domain modules.
"""

import json
from datetime import date
from decimal import Decimal


class DecimalEncoder(json.JSONEncoder):
    """JSON encoder that handles Decimal and date types."""

    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, date):
            return o.isoformat()
        return super().default(o)


def _json(data: object) -> str:
    """Serialize to JSON string, truncating if too large."""
    result = json.dumps(data, cls=DecimalEncoder, ensure_ascii=False)
    if len(result) > 60000:
        result = result[:60000] + "... (truncated)"
    return result


def build_tax_info(tax_rate: float) -> dict:
    """Build the standard tax_info dict used by funnel/capital/anomalies services."""
    return {
        "tax_regime": "usn_income",
        "usn_rate": tax_rate,
        "nds_rate": 0,
        "cost_as_expense": False,
    }


def normalize_brand(brand: str | None) -> str | None:
    """Normalize brand: 'all brands' variants and empty string become None."""
    if brand and brand.lower().strip() in ("все бренды", "all brands", "all", ""):
        return None
    return brand
