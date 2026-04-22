from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

from app.core.finance import assume_utc_if_naive, validate_currency_code


class FinancialAccountCreate(BaseModel):
    name: str
    currency: str | None = None
    is_default: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = " ".join(str(value).split())
        if not normalized:
            raise ValueError("Financial account name is required")
        return normalized

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return validate_currency_code(value)


class FinancialAccountUpdate(BaseModel):
    name: str | None = None
    currency: str | None = None
    is_default: bool | None = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(str(value).split())
        if not normalized:
            raise ValueError("Financial account name is required")
        return normalized

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return validate_currency_code(value)


class FinancialAccountRead(BaseModel):
    id: UUID
    name: str
    currency: str | None = None
    is_default: bool
    created_at: datetime

    @field_validator("currency", mode="before")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        return validate_currency_code(value)

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created_at(cls, value: datetime) -> datetime:
        return assume_utc_if_naive(value)

    model_config = ConfigDict(from_attributes=True)
