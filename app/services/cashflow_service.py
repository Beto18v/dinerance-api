from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, cast
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.obligation import Obligation, ObligationStatus
from app.schemas.cashflow import (
    CashflowForecastRead,
    ForecastWindowRead,
    SafeToSpendRead,
)
from app.services.ledger_service import get_ledger_balances
from app.services.obligation_schedule import advance_due_date
from app.services.obligation_service import resolve_obligation_reference_date
from app.services.user_service import ensure_active_user

MAX_MONTH_OFFSET = 11


@dataclass(frozen=True)
class ScheduledPayment:
    obligation_id: UUID
    due_date: date
    amount: Decimal


def get_cashflow_forecast(
    db: Session,
    user_id: UUID,
    *,
    reference_date: date | None = None,
) -> CashflowForecastRead:
    currency, current_balance, resolved_reference_date = _load_cashflow_inputs(
        db,
        user_id,
        reference_date=reference_date,
    )

    windows = [
        _build_month_window(resolved_reference_date, month_offset)
        for month_offset in range(3)
    ]
    max_window_end = max(window[1] for window in windows)
    scheduled_payments = _expand_scheduled_payments(
        db,
        user_id,
        window_end_date=max_window_end,
    )

    horizons = [
        _build_monthly_forecast_window(
            current_balance=current_balance,
            reference_date=resolved_reference_date,
            month_offset=month_offset,
            window=window,
            scheduled_payments=scheduled_payments,
        )
        for month_offset, window in enumerate(windows)
    ]

    return CashflowForecastRead(
        reference_date=resolved_reference_date,
        currency=currency,
        current_balance=current_balance,
        safe_to_spend=_safe_to_spend_from_window(
            currency=currency,
            current_balance=current_balance,
            reference_date=resolved_reference_date,
            window=horizons[0],
        ),
        horizons=horizons,
    )


def get_safe_to_spend(
    db: Session,
    user_id: UUID,
    *,
    month_offset: int = 0,
    reference_date: date | None = None,
) -> SafeToSpendRead:
    if month_offset < 0 or month_offset > MAX_MONTH_OFFSET:
        raise HTTPException(
            status_code=422,
            detail="Safe-to-spend month offset must be between 0 and 11",
        )

    currency, current_balance, resolved_reference_date = _load_cashflow_inputs(
        db,
        user_id,
        reference_date=reference_date,
    )
    window = _build_month_window(resolved_reference_date, month_offset)

    scheduled_payments = _expand_scheduled_payments(
        db,
        user_id,
        window_end_date=window[1],
    )
    forecast_window = _build_monthly_forecast_window(
        current_balance=current_balance,
        reference_date=resolved_reference_date,
        month_offset=month_offset,
        window=window,
        scheduled_payments=scheduled_payments,
    )
    return _safe_to_spend_from_window(
        currency=currency,
        current_balance=current_balance,
        reference_date=resolved_reference_date,
        window=forecast_window,
    )


def _load_cashflow_inputs(
    db: Session,
    user_id: UUID,
    *,
    reference_date: date | None,
) -> tuple[str, Decimal, date]:
    user = ensure_active_user(db, user_id)
    if not user.base_currency:
        raise HTTPException(
            status_code=409,
            detail="User base currency must be configured before calculating forecast",
        )

    ledger_balances = get_ledger_balances(db, user_id)
    resolved_reference_date = reference_date or resolve_obligation_reference_date(
        cast(str | None, user.timezone)
    )
    currency = cast(str, ledger_balances.currency or user.base_currency)
    return (
        currency,
        _normalize_decimal(ledger_balances.consolidated_balance),
        resolved_reference_date,
    )


def _build_month_window(
    reference_date: date,
    month_offset: int,
) -> tuple[date, date, int]:
    """Return (window_start, window_end, days_in_window) for a calendar month window.

    Offset 0 is the current partial month starting at ``reference_date``.
    Offsets 1+ are full calendar months.
    """
    if month_offset == 0:
        year, month = reference_date.year, reference_date.month
        start = reference_date
    else:
        total = reference_date.year * 12 + (reference_date.month - 1) + month_offset
        year, month = divmod(total, 12)
        month += 1
        start = date(year, month, 1)

    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day)
    days = (end - start).days + 1
    return start, end, days


def _expand_scheduled_payments(
    db: Session,
    user_id: UUID,
    *,
    window_end_date: date,
) -> list[ScheduledPayment]:
    obligations = (
        db.query(Obligation)
        .filter(
            Obligation.user_id == user_id,
            Obligation.status == ObligationStatus.active,
            Obligation.next_due_date <= window_end_date,
        )
        .order_by(
            Obligation.next_due_date.asc(),
            Obligation.created_at.asc(),
            Obligation.id.asc(),
        )
        .all()
    )

    scheduled_payments: list[ScheduledPayment] = []
    for obligation in obligations:
        due_date = obligation.next_due_date
        while due_date <= window_end_date:
            scheduled_payments.append(
                ScheduledPayment(
                    obligation_id=obligation.id,
                    due_date=due_date,
                    amount=_normalize_decimal(obligation.amount),
                )
            )
            due_date = advance_due_date(
                cadence=obligation.cadence,
                due_date=due_date,
                monthly_anchor_day=obligation.monthly_anchor_day,
                monthly_anchor_is_month_end=obligation.monthly_anchor_is_month_end,
            )

    scheduled_payments.sort(key=lambda item: (item.due_date, item.obligation_id))
    return scheduled_payments


def _build_monthly_forecast_window(
    *,
    current_balance: Decimal,
    reference_date: date,
    month_offset: int,
    window: tuple[date, date, int],
    scheduled_payments: list[ScheduledPayment],
) -> ForecastWindowRead:
    window_start, window_end, days_in_window = window
    lower_bound = reference_date if month_offset == 0 else window_start

    payments_in_window = [
        payment
        for payment in scheduled_payments
        if lower_bound <= payment.due_date <= window_end
    ]
    confirmed_obligations_total = sum(
        (payment.amount for payment in payments_in_window),
        start=Decimal("0.00"),
    )
    projected_balance = _normalize_decimal(
        current_balance - confirmed_obligations_total
    )
    safe_to_spend = _normalize_decimal(max(projected_balance, Decimal("0.00")))
    safe_to_spend_per_day = (
        _normalize_decimal(safe_to_spend / Decimal(days_in_window))
        if days_in_window > 0
        else Decimal("0.00")
    )
    shortfall_amount = _normalize_decimal(max(-projected_balance, Decimal("0.00")))

    return ForecastWindowRead(
        month_offset=month_offset,
        month_label=window_start.strftime("%b %Y"),
        days_in_window=days_in_window,
        scheduled_payments_count=len(payments_in_window),
        confirmed_obligations_total=_normalize_decimal(confirmed_obligations_total),
        projected_balance=projected_balance,
        safe_to_spend=safe_to_spend,
        safe_to_spend_per_day=safe_to_spend_per_day,
        shortfall_amount=shortfall_amount,
        status=_resolve_window_status(projected_balance),
    )


def _safe_to_spend_from_window(
    *,
    currency: str,
    current_balance: Decimal,
    reference_date: date,
    window: ForecastWindowRead,
) -> SafeToSpendRead:
    return SafeToSpendRead(
        reference_date=reference_date,
        month_offset=window.month_offset,
        month_label=window.month_label,
        days_in_window=window.days_in_window,
        currency=currency,
        current_balance=current_balance,
        scheduled_payments_count=window.scheduled_payments_count,
        confirmed_obligations_total=window.confirmed_obligations_total,
        projected_balance=window.projected_balance,
        safe_to_spend=window.safe_to_spend,
        safe_to_spend_per_day=window.safe_to_spend_per_day,
        shortfall_amount=window.shortfall_amount,
        status=window.status,
    )


def _resolve_window_status(
    projected_balance: Decimal,
) -> Literal["covered", "tight", "shortfall"]:
    if projected_balance < 0:
        return "shortfall"
    if projected_balance == 0:
        return "tight"
    return "covered"


def _normalize_decimal(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return Decimal("0.00")

    if isinstance(value, Decimal):
        normalized = value
    else:
        normalized = Decimal(str(value))

    return normalized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
