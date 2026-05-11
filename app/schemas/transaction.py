from uuid import UUID
from datetime import date, datetime
from decimal import Decimal
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import field_validator

from app.core.finance import (
    assume_utc_if_naive,
    ensure_aware_datetime,
    validate_currency_code,
)
from app.models.transaction import TransactionType


class TransactionCreate(BaseModel):
    category_id: UUID | None = None
    financial_account_id: UUID | None = None
    transaction_type: TransactionType | None = None
    amount: Decimal
    currency: str = "COP"
    description: str | None = None
    occurred_at: datetime

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = validate_currency_code(value)
        if normalized is None:
            raise ValueError("Currency is required")
        return normalized

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return ensure_aware_datetime(value)


class TransactionUpdate(BaseModel):
    category_id: UUID | None = None
    financial_account_id: UUID | None = None
    transaction_type: TransactionType | None = None
    amount: Decimal | None = None
    currency: str | None = None
    description: str | None = None
    occurred_at: datetime | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return validate_currency_code(value)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_aware_datetime(value)


class TransactionRead(BaseModel):
    id: UUID
    category_id: UUID | None = None
    financial_account_id: UUID
    financial_account_name: str | None = None
    transaction_type: TransactionType
    transfer_group_id: UUID | None = None
    amount: Decimal
    currency: str
    fx_rate: Decimal | None = None
    fx_rate_date: date | None = None
    fx_rate_source: str | None = None
    base_currency: str | None = None
    amount_in_base_currency: Decimal | None = None
    description: str | None
    occurred_at: datetime
    created_at: datetime

    @field_validator("occurred_at", "created_at", mode="before")
    @classmethod
    def validate_response_datetimes(cls, value: datetime) -> datetime:
        return assume_utc_if_naive(value)

    model_config = ConfigDict(from_attributes=True)


class TransactionAggregateTotal(BaseModel):
    currency: str
    amount: Decimal

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = validate_currency_code(value)
        if normalized is None:
            raise ValueError("Currency is required")
        return normalized


class TransactionListSummary(BaseModel):
    active_categories_count: int
    skipped_transactions: int = 0
    income_totals: list[TransactionAggregateTotal]
    expense_totals: list[TransactionAggregateTotal]
    balance_totals: list[TransactionAggregateTotal]


class TransactionListPage(BaseModel):
    items: list[TransactionRead]
    total_count: int | None = None
    limit: int
    offset: int
    summary: TransactionListSummary | None = None
