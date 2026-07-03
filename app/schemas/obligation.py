from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.finance import (
    assume_utc_if_naive,
    ensure_aware_datetime,
    validate_currency_code,
)
from app.models.obligation import (
    ObligationCadence,
    ObligationStatus,
)
from app.schemas.transaction import TransactionRead


class ObligationUrgency(str):
    overdue = "overdue"
    today = "today"
    soon = "soon"
    upcoming = "upcoming"


def _normalize_required_name(value: str) -> str:
    normalized = " ".join(str(value).split())
    if not normalized:
        raise ValueError("Name is required")
    return normalized


def _normalize_optional_description(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = " ".join(str(value).split())
    return normalized or None


def _normalize_optional_recurring_candidate_key(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip().lower()
    return normalized or None


class ObligationCreate(BaseModel):
    name: str
    amount: Decimal
    currency: str | None = None
    cadence: ObligationCadence
    next_due_date: date
    category_id: UUID
    expected_financial_account_id: UUID | None = None
    source_recurring_candidate_key: str | None = None
    status: ObligationStatus = ObligationStatus.active
    cadence_months: int | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _normalize_required_name(value)

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return validate_currency_code(value)

    @field_validator("source_recurring_candidate_key", mode="before")
    @classmethod
    def validate_source_recurring_candidate_key(
        cls,
        value: str | None,
    ) -> str | None:
        return _normalize_optional_recurring_candidate_key(value)


class ObligationUpdate(BaseModel):
    name: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    cadence: ObligationCadence | None = None
    next_due_date: date | None = None
    category_id: UUID | None = None
    expected_financial_account_id: UUID | None = None
    status: ObligationStatus | None = None
    cadence_months: int | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_required_name(value)

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return validate_currency_code(value)


class ObligationRead(BaseModel):
    id: UUID
    name: str
    amount: Decimal
    currency: str
    cadence: ObligationCadence
    next_due_date: date
    category_id: UUID
    category_name: str
    expected_financial_account_id: UUID | None = None
    expected_financial_account_name: str | None = None
    source_recurring_candidate_key: str | None = None
    status: ObligationStatus
    urgency: Literal["overdue", "today", "soon", "upcoming"]
    days_until_due: int
    expected_account_current_balance: Decimal | None = None
    expected_account_shortfall_amount: Decimal | None = None
    cadence_months: int | None = None
    created_at: datetime
    updated_at: datetime | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = validate_currency_code(value)
        if normalized is None:
            raise ValueError("Currency is required")
        return normalized

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def validate_datetimes(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None
        return assume_utc_if_naive(value)

    model_config = ConfigDict(from_attributes=True)


class ObligationStatusCountsRead(BaseModel):
    active: int
    paused: int
    archived: int


class ObligationListRead(BaseModel):
    items: list[ObligationRead]
    counts: ObligationStatusCountsRead


class ObligationUpcomingSummaryRead(BaseModel):
    currency: str
    total_active: int
    items_in_window: int
    overdue_count: int
    due_today_count: int
    due_soon_count: int
    expected_account_risk_count: int
    total_expected_amount: Decimal

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        normalized = validate_currency_code(value)
        if normalized is None:
            raise ValueError("Currency is required")
        return normalized


class ObligationUpcomingRead(BaseModel):
    reference_date: date
    window_end_date: date
    summary: ObligationUpcomingSummaryRead
    items: list[ObligationRead]


class ObligationMarkPaid(BaseModel):
    financial_account_id: UUID | None = None
    paid_at: datetime | None = None
    description: str | None = None

    @field_validator("paid_at")
    @classmethod
    def validate_paid_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_aware_datetime(value)

    @field_validator("description", mode="before")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        return _normalize_optional_description(value)


class ObligationPaymentRead(BaseModel):
    obligation: ObligationRead
    transaction: TransactionRead
