from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.finance import (
    assume_utc_if_naive,
    ensure_aware_datetime,
    validate_currency_code,
)
from app.models.transaction import BalanceDirection, TransactionType


def _normalize_required_description(value: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ValueError("Description is required")
    return normalized


class TransferCreate(BaseModel):
    source_financial_account_id: UUID
    destination_financial_account_id: UUID
    amount: Decimal
    currency: str = "COP"
    description: str
    occurred_at: datetime

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = validate_currency_code(value)
        if normalized is None:
            raise ValueError("Currency is required")
        return normalized

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _normalize_required_description(value)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return ensure_aware_datetime(value)


class AdjustmentCreate(BaseModel):
    financial_account_id: UUID | None = None
    balance_direction: BalanceDirection
    amount: Decimal
    currency: str = "COP"
    description: str
    occurred_at: datetime

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = validate_currency_code(value)
        if normalized is None:
            raise ValueError("Currency is required")
        return normalized

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, value: str) -> str:
        return _normalize_required_description(value)

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime) -> datetime:
        return ensure_aware_datetime(value)


class LedgerMovementRead(BaseModel):
    id: UUID
    category_id: UUID | None = None
    category_name: str | None = None
    financial_account_id: UUID
    financial_account_name: str | None = None
    counterparty_financial_account_id: UUID | None = None
    counterparty_financial_account_name: str | None = None
    transaction_type: TransactionType
    balance_direction: BalanceDirection
    transfer_group_id: UUID | None = None
    amount: Decimal
    currency: str
    base_currency: str | None = None
    amount_in_base_currency: Decimal | None = None
    description: str | None = None
    occurred_at: datetime
    created_at: datetime

    @field_validator("currency", "base_currency", mode="before")
    @classmethod
    def validate_response_currency(cls, value: str | None) -> str | None:
        return validate_currency_code(value)

    @field_validator("occurred_at", "created_at", mode="before")
    @classmethod
    def validate_response_datetimes(cls, value: datetime) -> datetime:
        return assume_utc_if_naive(value)

    model_config = ConfigDict(from_attributes=True)


class TransferRead(BaseModel):
    transfer_group_id: UUID
    source_transaction: LedgerMovementRead
    destination_transaction: LedgerMovementRead


class LedgerBalanceAccountRead(BaseModel):
    financial_account_id: UUID
    financial_account_name: str
    currency: str | None = None
    balance: Decimal
    balance_in_base_currency: Decimal | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return validate_currency_code(value)


class LedgerBalancesRead(BaseModel):
    currency: str | None = None
    consolidated_balance: Decimal
    skipped_transactions: int = 0
    accounts: list[LedgerBalanceAccountRead]

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return validate_currency_code(value)


class LedgerActivityRead(BaseModel):
    items: list[LedgerMovementRead]
    limit: int
