from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session, joinedload

from app.core.finance import get_timezone, validate_currency_code
from app.models.category import Category, CategoryDirection
from app.models.financial_account import FinancialAccount
from app.models.obligation import (
    Obligation,
    ObligationCadence,
    ObligationStatus,
)
from app.models.transaction import (
    BalanceDirection,
    Transaction,
    TransactionType,
)
from app.schemas.obligation import (
    ObligationCreate,
    ObligationListRead,
    ObligationMarkPaid,
    ObligationPaymentRead,
    ObligationRead,
    ObligationStatusCountsRead,
    ObligationUpcomingRead,
    ObligationUpcomingSummaryRead,
    ObligationUpdate,
)
from app.schemas.transaction import TransactionRead
from app.services.ledger_shared import (
    build_ledger_transaction,
    ensure_transaction_amount_is_positive,
    ensure_transaction_currency_matches_financial_account_currency,
    ensure_transaction_currency_matches_user_base_currency,
    resolve_financial_account,
)
from app.services.obligation_schedule import advance_due_date
from app.services.user_service import ensure_active_user

DUE_SOON_WINDOW_DAYS = 7
UPCOMING_WINDOW_DAYS = 30
UPCOMING_ITEMS_LIMIT = 12


def create_obligation(
    db: Session,
    user_id: UUID,
    obligation_data: ObligationCreate,
) -> ObligationRead:
    user = _ensure_user_with_base_currency(db, user_id)
    ensure_transaction_amount_is_positive(obligation_data.amount)

    category = _get_expense_category_for_user(
        db,
        user_id=user_id,
        category_id=obligation_data.category_id,
    )
    expected_financial_account = _get_expected_financial_account(
        db,
        user_id=user_id,
        expected_financial_account_id=obligation_data.expected_financial_account_id,
    )
    currency = _resolve_currency(
        requested_currency=obligation_data.currency,
        user_base_currency=user.base_currency,
    )
    _ensure_obligation_currency_is_supported(
        user_base_currency=user.base_currency,
        currency=currency,
        expected_financial_account=expected_financial_account,
    )
    monthly_anchor_day, monthly_anchor_is_month_end = _resolve_monthly_anchor(
        cadence=obligation_data.cadence,
        next_due_date=obligation_data.next_due_date,
    )

    obligation = Obligation(
        user_id=user_id,
        category_id=category.id,
        expected_financial_account_id=(
            expected_financial_account.id if expected_financial_account else None
        ),
        name=obligation_data.name,
        source_recurring_candidate_key=obligation_data.source_recurring_candidate_key,
        amount=obligation_data.amount,
        currency=currency,
        cadence=obligation_data.cadence,
        next_due_date=obligation_data.next_due_date,
        monthly_anchor_day=monthly_anchor_day,
        monthly_anchor_is_month_end=monthly_anchor_is_month_end,
        status=obligation_data.status,
        cadence_months=obligation_data.cadence_months,
    )
    db.add(obligation)
    db.commit()
    db.refresh(obligation)

    reference_date = resolve_obligation_reference_date(user.timezone)
    return serialize_obligation(
        obligation,
        reference_date=reference_date,
        expected_account_balance_by_id=_get_financial_account_balances(
            db,
            user_id=user_id,
            account_ids=[expected_financial_account.id]
            if expected_financial_account is not None
            else [],
        ),
    )


def list_obligations(
    db: Session,
    user_id: UUID,
    *,
    status: ObligationStatus | None = None,
    reference_date: date | None = None,
) -> ObligationListRead:
    user = _ensure_user_with_base_currency(db, user_id)
    resolved_reference_date = reference_date or resolve_obligation_reference_date(
        user.timezone
    )

    counts = _get_obligation_status_counts(db, user_id)
    query = _build_obligations_query(db, user_id)
    if status is not None:
        query = query.filter(Obligation.status == status)

    obligations = query.all()
    balance_by_account_id = _get_financial_account_balances(
        db,
        user_id=user_id,
        account_ids=[
            obligation.expected_financial_account_id
            for obligation in obligations
            if obligation.expected_financial_account_id is not None
        ],
    )

    return ObligationListRead(
        items=[
            serialize_obligation(
                obligation,
                reference_date=resolved_reference_date,
                expected_account_balance_by_id=balance_by_account_id,
            )
            for obligation in obligations
        ],
        counts=counts,
    )


def update_obligation(
    db: Session,
    user_id: UUID,
    obligation_id: UUID,
    obligation_data: ObligationUpdate,
) -> ObligationRead:
    user = _ensure_user_with_base_currency(db, user_id)
    obligation = get_obligation_for_user(db, user_id, obligation_id)
    updates = obligation_data.model_dump(exclude_unset=True)

    if "name" in updates:
        if updates["name"] is None:
            raise HTTPException(status_code=422, detail="Name is required")
        obligation.name = updates["name"]

    if "amount" in updates:
        if updates["amount"] is None:
            raise HTTPException(status_code=422, detail="Amount is required")
        ensure_transaction_amount_is_positive(updates["amount"])
        obligation.amount = updates["amount"]

    if "status" in updates:
        if updates["status"] is None:
            raise HTTPException(status_code=422, detail="Status is required")
        obligation.status = updates["status"]

    next_category = obligation.category
    if "category_id" in updates:
        if updates["category_id"] is None:
            raise HTTPException(status_code=422, detail="Category is required")
        next_category = _get_expense_category_for_user(
            db,
            user_id=user_id,
            category_id=updates["category_id"],
        )
        obligation.category_id = next_category.id

    next_expected_financial_account = obligation.expected_financial_account
    if "expected_financial_account_id" in updates:
        next_expected_financial_account = _get_expected_financial_account(
            db,
            user_id=user_id,
            expected_financial_account_id=updates["expected_financial_account_id"],
        )
        obligation.expected_financial_account_id = (
            next_expected_financial_account.id
            if next_expected_financial_account is not None
            else None
        )

    if "currency" in updates:
        if updates["currency"] is None:
            raise HTTPException(status_code=422, detail="Currency is required")
        obligation.currency = _resolve_currency(
            requested_currency=updates["currency"],
            user_base_currency=user.base_currency,
        )

    if "cadence" in updates and updates["cadence"] is not None:
        obligation.cadence = updates["cadence"]

    if "next_due_date" in updates:
        if updates["next_due_date"] is None:
            raise HTTPException(status_code=422, detail="Next due date is required")
        obligation.next_due_date = updates["next_due_date"]

    if "cadence_months" in updates:
        obligation.cadence_months = updates["cadence_months"]

    _ensure_obligation_currency_is_supported(
        user_base_currency=user.base_currency,
        currency=obligation.currency,
        expected_financial_account=next_expected_financial_account,
    )
    monthly_anchor_day, monthly_anchor_is_month_end = _resolve_monthly_anchor(
        cadence=obligation.cadence,
        next_due_date=obligation.next_due_date,
    )
    obligation.monthly_anchor_day = monthly_anchor_day
    obligation.monthly_anchor_is_month_end = monthly_anchor_is_month_end

    db.commit()
    db.refresh(obligation)

    reference_date = resolve_obligation_reference_date(user.timezone)
    return serialize_obligation(
        obligation,
        reference_date=reference_date,
        expected_account_balance_by_id=_get_financial_account_balances(
            db,
            user_id=user_id,
            account_ids=[obligation.expected_financial_account_id]
            if obligation.expected_financial_account_id is not None
            else [],
        ),
    )


def delete_obligation(
    db: Session,
    user_id: UUID,
    obligation_id: UUID,
) -> None:
    get_obligation_for_user(db, user_id, obligation_id)
    (
        db.query(Obligation)
        .filter(
            Obligation.id == obligation_id,
            Obligation.user_id == user_id,
        )
        .delete(synchronize_session=False)
    )
    db.commit()


def get_upcoming_obligations(
    db: Session,
    user_id: UUID,
    *,
    reference_date: date | None = None,
    days_ahead: int = UPCOMING_WINDOW_DAYS,
    limit: int = UPCOMING_ITEMS_LIMIT,
) -> ObligationUpcomingRead:
    user = _ensure_user_with_base_currency(db, user_id)
    resolved_reference_date = reference_date or resolve_obligation_reference_date(
        user.timezone
    )
    window_end_date = resolved_reference_date.fromordinal(
        resolved_reference_date.toordinal() + days_ahead
    )
    upcoming_obligations = (
        _build_obligations_query(db, user_id)
        .filter(
            Obligation.status == ObligationStatus.active,
            Obligation.next_due_date <= window_end_date,
        )
        .all()
    )

    active_count = int(
        db.query(func.count(Obligation.id))
        .filter(
            Obligation.user_id == user_id,
            Obligation.status == ObligationStatus.active,
        )
        .scalar()
        or 0
    )

    balance_by_account_id = _get_financial_account_balances(
        db,
        user_id=user_id,
        account_ids=[
            obligation.expected_financial_account_id
            for obligation in upcoming_obligations
            if obligation.expected_financial_account_id is not None
        ],
    )
    serialized_upcoming_obligations = [
        serialize_obligation(
            obligation,
            reference_date=resolved_reference_date,
            expected_account_balance_by_id=balance_by_account_id,
        )
        for obligation in upcoming_obligations
    ]
    items = serialized_upcoming_obligations[:limit]

    return ObligationUpcomingRead(
        reference_date=resolved_reference_date,
        window_end_date=window_end_date,
        summary=ObligationUpcomingSummaryRead(
            currency=user.base_currency,
            total_active=active_count,
            items_in_window=len(serialized_upcoming_obligations),
            overdue_count=sum(
                1
                for item in serialized_upcoming_obligations
                if item.urgency == "overdue"
            ),
            due_today_count=sum(
                1
                for item in serialized_upcoming_obligations
                if item.urgency == "today"
            ),
            due_soon_count=sum(
                1
                for item in serialized_upcoming_obligations
                if item.urgency == "soon"
            ),
            expected_account_risk_count=sum(
                1
                for item in serialized_upcoming_obligations
                if item.expected_account_shortfall_amount is not None
                and item.expected_account_shortfall_amount > 0
            ),
            total_expected_amount=sum(
                (item.amount for item in serialized_upcoming_obligations),
                start=Decimal("0.00"),
            ),
        ),
        items=items,
    )


def mark_obligation_paid(
    db: Session,
    user_id: UUID,
    obligation_id: UUID,
    payment_data: ObligationMarkPaid,
) -> ObligationPaymentRead:
    user = _ensure_user_with_base_currency(db, user_id)
    obligation = get_obligation_for_user(db, user_id, obligation_id)
    if obligation.status != ObligationStatus.active:
        raise HTTPException(
            status_code=409,
            detail="Only active obligations can be marked as paid",
        )

    category = _get_expense_category_for_user(
        db,
        user_id=user_id,
        category_id=obligation.category_id,
    )
    financial_account = resolve_financial_account(
        db,
        user_id=user_id,
        user=user,
        financial_account_id=(
            payment_data.financial_account_id
            or obligation.expected_financial_account_id
        ),
    )
    ensure_transaction_currency_matches_user_base_currency(
        user_base_currency=user.base_currency,
        transaction_currency=obligation.currency,
    )
    ensure_transaction_currency_matches_financial_account_currency(
        financial_account=financial_account,
        transaction_currency=obligation.currency,
    )

    paid_at = payment_data.paid_at or datetime.now(tz=get_timezone(user.timezone))
    transaction = build_ledger_transaction(
        db,
        user=user,
        financial_account=financial_account,
        transaction_type=TransactionType.expense,
        balance_direction=BalanceDirection.outflow,
        amount=obligation.amount,
        currency=obligation.currency,
        occurred_at=paid_at,
        description=payment_data.description or obligation.name,
        category=category,
    )
    next_due_date = advance_obligation_due_date(obligation)

    try:
        db.add(transaction)
        obligation.next_due_date = next_due_date
        db.commit()
    except Exception:
        db.rollback()
        raise

    db.refresh(transaction)
    db.refresh(obligation)

    reference_date = resolve_obligation_reference_date(user.timezone)
    return ObligationPaymentRead(
        obligation=serialize_obligation(
            obligation,
            reference_date=reference_date,
            expected_account_balance_by_id=_get_financial_account_balances(
                db,
                user_id=user_id,
                account_ids=[obligation.expected_financial_account_id]
                if obligation.expected_financial_account_id is not None
                else [],
            ),
        ),
        transaction=TransactionRead.model_validate(transaction),
    )


def get_obligation_for_user(
    db: Session,
    user_id: UUID,
    obligation_id: UUID,
) -> Obligation:
    obligation = (
        _build_obligations_query(db, user_id)
        .filter(Obligation.id == obligation_id)
        .first()
    )
    if not obligation:
        raise HTTPException(status_code=404, detail="Obligation not found")
    return obligation


def serialize_obligation(
    obligation: Obligation,
    *,
    reference_date: date,
    expected_account_balance_by_id: dict[UUID, Decimal] | None = None,
) -> ObligationRead:
    days_until_due = (obligation.next_due_date - reference_date).days
    urgency = _resolve_urgency(days_until_due)
    expected_balance = None
    expected_shortfall = None

    if (
        obligation.status == ObligationStatus.active
        and obligation.expected_financial_account_id is not None
        and expected_account_balance_by_id is not None
    ):
        expected_balance = expected_account_balance_by_id.get(
            obligation.expected_financial_account_id
        )
        if expected_balance is not None and expected_balance < obligation.amount:
            expected_shortfall = _normalize_decimal(
                obligation.amount - expected_balance
            )

    return ObligationRead(
        id=obligation.id,
        name=obligation.name,
        amount=_normalize_decimal(obligation.amount),
        currency=obligation.currency,
        cadence=obligation.cadence,
        next_due_date=obligation.next_due_date,
        category_id=obligation.category_id,
        category_name=obligation.category.name,
        expected_financial_account_id=obligation.expected_financial_account_id,
        expected_financial_account_name=(
            obligation.expected_financial_account.name
            if obligation.expected_financial_account is not None
            else None
        ),
        source_recurring_candidate_key=obligation.source_recurring_candidate_key,
        status=obligation.status,
        urgency=urgency,
        days_until_due=days_until_due,
        expected_account_current_balance=expected_balance,
        expected_account_shortfall_amount=expected_shortfall,
        cadence_months=obligation.cadence_months,
        created_at=obligation.created_at,
        updated_at=obligation.updated_at,
    )


def resolve_obligation_reference_date(timezone_name: str | None) -> date:
    return datetime.now(tz=get_timezone(timezone_name)).date()


def advance_obligation_due_date(obligation: Obligation) -> date:
    return advance_due_date(
        cadence=obligation.cadence,
        due_date=obligation.next_due_date,
        monthly_anchor_day=obligation.monthly_anchor_day,
        monthly_anchor_is_month_end=obligation.monthly_anchor_is_month_end,
        cadence_months=obligation.cadence_months,
    )


def _build_obligations_query(db: Session, user_id: UUID):
    status_rank = case(
        (Obligation.status == ObligationStatus.active, 0),
        (Obligation.status == ObligationStatus.paused, 1),
        else_=2,
    )
    return (
        db.query(Obligation)
        .options(
            joinedload(Obligation.category),
            joinedload(Obligation.expected_financial_account),
        )
        .filter(Obligation.user_id == user_id)
        .order_by(
            status_rank.asc(),
            Obligation.next_due_date.asc(),
            Obligation.created_at.asc(),
            Obligation.id.asc(),
        )
    )


def _ensure_user_with_base_currency(db: Session, user_id: UUID):
    user = ensure_active_user(db, user_id)
    if not user.base_currency:
        raise HTTPException(
            status_code=409,
            detail="User base currency must be configured before creating obligations",
        )
    return user


def _get_expense_category_for_user(
    db: Session,
    *,
    user_id: UUID,
    category_id: UUID,
) -> Category:
    category = (
        db.query(Category)
        .filter(
            Category.id == category_id,
            Category.user_id == user_id,
        )
        .first()
    )
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    if category.direction != CategoryDirection.expense:
        raise HTTPException(
            status_code=409,
            detail="Only expense categories can be used for obligations",
        )
    return category


def _get_expected_financial_account(
    db: Session,
    *,
    user_id: UUID,
    expected_financial_account_id: UUID | None,
) -> FinancialAccount | None:
    if expected_financial_account_id is None:
        return None

    account = (
        db.query(FinancialAccount)
        .filter(
            FinancialAccount.id == expected_financial_account_id,
            FinancialAccount.user_id == user_id,
        )
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Financial account not found")
    return account


def _resolve_currency(
    *,
    requested_currency: str | None,
    user_base_currency: str | None,
) -> str:
    normalized_currency = validate_currency_code(requested_currency)
    if normalized_currency is not None:
        return normalized_currency
    if user_base_currency is None:
        raise HTTPException(
            status_code=409,
            detail="User base currency must be configured before creating obligations",
        )
    return user_base_currency


def _ensure_obligation_currency_is_supported(
    *,
    user_base_currency: str | None,
    currency: str,
    expected_financial_account: FinancialAccount | None,
) -> None:
    ensure_transaction_currency_matches_user_base_currency(
        user_base_currency=user_base_currency,
        transaction_currency=currency,
    )
    if expected_financial_account is not None:
        ensure_transaction_currency_matches_financial_account_currency(
            financial_account=expected_financial_account,
            transaction_currency=currency,
        )


def _resolve_monthly_anchor(
    *,
    cadence: ObligationCadence,
    next_due_date: date,
) -> tuple[int | None, bool]:
    if cadence != ObligationCadence.monthly:
        return None, False

    month_end_day = monthrange(next_due_date.year, next_due_date.month)[1]
    return next_due_date.day, next_due_date.day == month_end_day


def _get_obligation_status_counts(
    db: Session,
    user_id: UUID,
) -> ObligationStatusCountsRead:
    rows = (
        db.query(Obligation.status, func.count(Obligation.id))
        .filter(Obligation.user_id == user_id)
        .group_by(Obligation.status)
        .all()
    )
    counts = {
        ObligationStatus.active: 0,
        ObligationStatus.paused: 0,
        ObligationStatus.archived: 0,
    }
    for status, count in rows:
        counts[status] = int(count)

    return ObligationStatusCountsRead(
        active=counts[ObligationStatus.active],
        paused=counts[ObligationStatus.paused],
        archived=counts[ObligationStatus.archived],
    )


def _get_financial_account_balances(
    db: Session,
    *,
    user_id: UUID,
    account_ids: list[UUID],
) -> dict[UUID, Decimal]:
    normalized_account_ids = list(dict.fromkeys(account_ids))
    if not normalized_account_ids:
        return {}

    signed_amount = case(
        (
            Transaction.balance_direction == BalanceDirection.inflow,
            Transaction.amount,
        ),
        else_=-Transaction.amount,
    )
    rows = (
        db.query(
            Transaction.financial_account_id,
            func.coalesce(func.sum(signed_amount), 0).label("balance"),
        )
        .filter(
            Transaction.user_id == user_id,
            Transaction.financial_account_id.in_(normalized_account_ids),
        )
        .group_by(Transaction.financial_account_id)
        .all()
    )

    return {
        row.financial_account_id: _normalize_decimal(row.balance)
        for row in rows
    }


def _resolve_urgency(days_until_due: int) -> str:
    if days_until_due < 0:
        return "overdue"
    if days_until_due == 0:
        return "today"
    if days_until_due <= DUE_SOON_WINDOW_DAYS:
        return "soon"
    return "upcoming"


def _normalize_decimal(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return Decimal("0.00")

    if isinstance(value, Decimal):
        normalized = value
    else:
        normalized = Decimal(str(value))

    return normalized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
