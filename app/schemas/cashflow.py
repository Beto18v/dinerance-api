from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, field_validator

from app.core.finance import validate_currency_code


class ForecastWindowRead(BaseModel):
    month_offset: int
    month_label: str
    days_in_window: int
    scheduled_payments_count: int
    confirmed_obligations_total: Decimal
    projected_balance: Decimal
    safe_to_spend: Decimal
    safe_to_spend_per_day: Decimal
    shortfall_amount: Decimal
    status: Literal["covered", "tight", "shortfall"]


class SafeToSpendRead(BaseModel):
    reference_date: date
    month_offset: int
    month_label: str
    days_in_window: int
    currency: str
    current_balance: Decimal
    scheduled_payments_count: int
    confirmed_obligations_total: Decimal
    projected_balance: Decimal
    safe_to_spend: Decimal
    safe_to_spend_per_day: Decimal
    shortfall_amount: Decimal
    status: Literal["covered", "tight", "shortfall"]

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = validate_currency_code(value)
        if normalized is None:
            raise ValueError("Currency is required")
        return normalized


class CashflowForecastRead(BaseModel):
    reference_date: date
    currency: str
    current_balance: Decimal
    safe_to_spend: SafeToSpendRead
    horizons: list[ForecastWindowRead]

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = validate_currency_code(value)
        if normalized is None:
            raise ValueError("Currency is required")
        return normalized
