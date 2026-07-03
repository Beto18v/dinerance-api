from __future__ import annotations

from calendar import monthrange
from datetime import date

from app.models.obligation import ObligationCadence


def advance_due_date(
    *,
    cadence: ObligationCadence,
    due_date: date,
    monthly_anchor_day: int | None = None,
    monthly_anchor_is_month_end: bool = False,
    cadence_months: int | None = None,
) -> date:
    if cadence == ObligationCadence.weekly:
        return due_date.fromordinal(due_date.toordinal() + 7)
    if cadence == ObligationCadence.biweekly:
        return due_date.fromordinal(due_date.toordinal() + 14)

    step = cadence_months if cadence_months is not None else 1
    next_month_year, next_month = _shift_month(
        due_date.year,
        due_date.month,
        step,
    )
    if monthly_anchor_is_month_end:
        return date(
            next_month_year,
            next_month,
            monthrange(next_month_year, next_month)[1],
        )

    anchor_day = monthly_anchor_day or due_date.day
    return date(
        next_month_year,
        next_month,
        min(anchor_day, monthrange(next_month_year, next_month)[1]),
    )


def _shift_month(year: int, month: int, months: int) -> tuple[int, int]:
    total_months = (year * 12) + (month - 1) + months
    shifted_year = total_months // 12
    shifted_month = (total_months % 12) + 1
    return shifted_year, shifted_month
