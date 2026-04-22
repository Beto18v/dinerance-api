from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, case, distinct, func, or_
from sqlalchemy.orm import Session

from app.models.category import Category, CategoryDirection
from app.models.transaction import BalanceDirection, Transaction, TransactionType
from app.schemas.transaction import (
    TransactionAggregateTotal,
    TransactionCreate,
    TransactionListPage,
    TransactionListSummary,
    TransactionUpdate,
)
from app.services.exchange_rate_service import (
    apply_transaction_fx_snapshot,
    resolve_transaction_fx_snapshot,
)
from app.services.ledger_shared import (
    PUBLIC_TRANSACTION_TYPES,
    ensure_transaction_amount_is_positive,
    ensure_transaction_currency_matches_financial_account_currency,
    get_transaction_for_user,
    resolve_financial_account,
)
from app.services.user_service import ensure_active_user


def create_transaction(
    db: Session,
    user_id: UUID,
    transaction_data: TransactionCreate,
):
    user = ensure_active_user(db, user_id)
    ensure_transaction_amount_is_positive(transaction_data.amount)

    financial_account = resolve_financial_account(
        db,
        user_id=user_id,
        user=user,
        financial_account_id=transaction_data.financial_account_id,
    )
    ensure_transaction_currency_matches_financial_account_currency(
        financial_account=financial_account,
        transaction_currency=transaction_data.currency,
    )

    category = _get_category_for_user(
        db,
        user_id=user_id,
        category_id=transaction_data.category_id,
    )
    transaction_type = _resolve_transaction_type(
        category=category,
        requested_type=transaction_data.transaction_type,
    )

    snapshot = resolve_transaction_fx_snapshot(
        db,
        user=user,
        transaction_currency=transaction_data.currency,
        occurred_at=transaction_data.occurred_at,
        amount=transaction_data.amount,
    )

    transaction = Transaction(
        user_id=user_id,
        financial_account_id=financial_account.id,
        category_id=category.id if category else None,
        transaction_type=transaction_type,
        balance_direction=_transaction_type_to_balance_direction(transaction_type),
        amount=transaction_data.amount,
        currency=transaction_data.currency,
        description=transaction_data.description,
        occurred_at=transaction_data.occurred_at,
    )
    apply_transaction_fx_snapshot(transaction, snapshot)

    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    return transaction


def get_transaction(db: Session, user_id: UUID, transaction_id: UUID) -> Transaction:
    return get_transaction_for_user(
        db,
        user_id,
        transaction_id,
        allowed_types=PUBLIC_TRANSACTION_TYPES,
    )


def list_transactions(
    db: Session,
    user_id: UUID,
    *,
    financial_account_id: UUID | None = None,
    category_id: UUID | None = None,
    parent_category_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
    user_base_currency: str | None = None,
    include_total_count: bool = True,
    include_summary: bool = True,
) -> TransactionListPage:
    query = _build_transactions_query(
        db,
        user_id,
        financial_account_id=financial_account_id,
        category_id=category_id,
        parent_category_id=parent_category_id,
        start_date=start_date,
        end_date=end_date,
    )
    total_count = (
        int(query.with_entities(func.count(Transaction.id)).scalar() or 0)
        if include_total_count
        else None
    )
    items = (
        query.order_by(
            Transaction.occurred_at.desc(),
            Transaction.created_at.desc(),
            Transaction.id.desc(),
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    return TransactionListPage(
        items=items,
        total_count=total_count,
        limit=limit,
        offset=offset,
        summary=(
            _build_transactions_summary(
                query=query,
                user_base_currency=user_base_currency,
            )
            if include_summary
            else None
        ),
    )


def update_transaction(
    db: Session,
    user_id: UUID,
    transaction_id: UUID,
    transaction_data: TransactionUpdate,
):
    user = ensure_active_user(db, user_id)
    transaction = get_transaction(db, user_id, transaction_id)
    updates = transaction_data.model_dump(exclude_unset=True)

    if "amount" in updates and updates["amount"] is not None:
        ensure_transaction_amount_is_positive(updates["amount"])

    category_id = (
        updates["category_id"]
        if "category_id" in updates
        else transaction.category_id
    )
    category = _get_category_for_user(
        db,
        user_id=user_id,
        category_id=category_id,
    )
    requested_transaction_type = (
        updates["transaction_type"]
        if "transaction_type" in updates
        else None
        if "category_id" in updates
        else transaction.transaction_type
    )
    transaction_type = _resolve_transaction_type(
        category=category,
        requested_type=requested_transaction_type,
    )

    financial_account_id = (
        updates["financial_account_id"]
        if "financial_account_id" in updates
        else transaction.financial_account_id
    )
    financial_account = resolve_financial_account(
        db,
        user_id=user_id,
        user=user,
        financial_account_id=financial_account_id,
    )

    transaction.category_id = category.id if category else None
    transaction.transaction_type = transaction_type
    transaction.balance_direction = _transaction_type_to_balance_direction(
        transaction_type
    )
    transaction.financial_account_id = financial_account.id

    for field in (
        "amount",
        "currency",
        "description",
        "occurred_at",
    ):
        if field in updates:
            setattr(transaction, field, updates[field])

    ensure_transaction_currency_matches_financial_account_currency(
        financial_account=financial_account,
        transaction_currency=transaction.currency,
    )

    needs_fx_refresh = any(
        field in updates for field in ("amount", "currency", "occurred_at")
    ) or transaction.base_currency != user.base_currency
    if needs_fx_refresh:
        snapshot = resolve_transaction_fx_snapshot(
            db,
            user=user,
            transaction_currency=transaction.currency,
            occurred_at=transaction.occurred_at,
            amount=transaction.amount,
        )
        apply_transaction_fx_snapshot(transaction, snapshot)

    db.commit()
    db.refresh(transaction)
    return transaction


def delete_transaction(db: Session, user_id: UUID, transaction_id: UUID) -> None:
    transaction = get_transaction(db, user_id, transaction_id)
    db.delete(transaction)
    db.commit()


def _build_transactions_query(
    db: Session,
    user_id: UUID,
    *,
    financial_account_id: UUID | None = None,
    category_id: UUID | None = None,
    parent_category_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
):
    query = (
        db.query(Transaction)
        .outerjoin(
            Category,
            and_(
                Transaction.category_id == Category.id,
                Category.user_id == user_id,
            ),
        )
        .filter(
            Transaction.user_id == user_id,
            Transaction.transaction_type.in_(PUBLIC_TRANSACTION_TYPES),
        )
    )

    if category_id:
        query = query.filter(Transaction.category_id == category_id)
    if financial_account_id:
        query = query.filter(Transaction.financial_account_id == financial_account_id)
    if parent_category_id:
        query = query.filter(
            or_(
                Transaction.category_id == parent_category_id,
                Category.parent_id == parent_category_id,
            )
        )
    if start_date:
        query = query.filter(Transaction.occurred_at >= start_date)
    if end_date:
        query = query.filter(Transaction.occurred_at <= end_date)

    return query


def _build_transactions_summary(
    *,
    query,
    user_base_currency: str | None,
) -> TransactionListSummary:
    active_categories_count = (
        query.with_entities(func.count(distinct(Transaction.category_id))).scalar() or 0
    )

    if user_base_currency:
        return _build_base_currency_summary(
            query=query,
            user_base_currency=user_base_currency,
            active_categories_count=int(active_categories_count),
        )

    return _build_multi_currency_summary(
        query=query,
        active_categories_count=int(active_categories_count),
    )


def _build_base_currency_summary(
    *,
    query,
    user_base_currency: str,
    active_categories_count: int,
) -> TransactionListSummary:
    usable_amount = case(
        (Transaction.currency == user_base_currency, Transaction.amount),
        (
            and_(
                Transaction.base_currency == user_base_currency,
                Transaction.amount_in_base_currency.isnot(None),
            ),
            Transaction.amount_in_base_currency,
        ),
        else_=None,
    )

    income_total = _normalize_decimal(
        query.with_entities(
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Transaction.transaction_type == TransactionType.income,
                                usable_amount.isnot(None),
                            ),
                            usable_amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            )
        ).scalar()
    )
    expense_total = _normalize_decimal(
        query.with_entities(
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Transaction.transaction_type == TransactionType.expense,
                                usable_amount.isnot(None),
                            ),
                            usable_amount,
                        ),
                        else_=0,
                    )
                ),
                0,
            )
        ).scalar()
    )
    usable_count = (
        query.with_entities(
            func.count(
                case(
                    (
                        and_(
                            usable_amount.isnot(None),
                            Transaction.transaction_type.in_(
                                [
                                    TransactionType.income,
                                    TransactionType.expense,
                                ]
                            ),
                        ),
                        1,
                    ),
                    else_=None,
                )
            )
        ).scalar()
        or 0
    )
    skipped_transactions = (
        query.with_entities(
            func.count(
                case(
                    (
                        and_(
                            usable_amount.is_(None),
                            Transaction.transaction_type.in_(
                                [
                                    TransactionType.income,
                                    TransactionType.expense,
                                ]
                            ),
                        ),
                        1,
                    ),
                    else_=None,
                )
            )
        ).scalar()
        or 0
    )

    income_totals = (
        [TransactionAggregateTotal(currency=user_base_currency, amount=income_total)]
        if income_total != 0
        else []
    )
    expense_totals = (
        [TransactionAggregateTotal(currency=user_base_currency, amount=expense_total)]
        if expense_total != 0
        else []
    )
    balance_totals = (
        [
            TransactionAggregateTotal(
                currency=user_base_currency,
                amount=income_total - expense_total,
            )
        ]
        if usable_count > 0
        else []
    )

    return TransactionListSummary(
        active_categories_count=active_categories_count,
        skipped_transactions=int(skipped_transactions),
        income_totals=income_totals,
        expense_totals=expense_totals,
        balance_totals=balance_totals,
    )


def _build_multi_currency_summary(
    *,
    query,
    active_categories_count: int,
) -> TransactionListSummary:
    grouped_totals = query.with_entities(
        Transaction.currency,
        Transaction.transaction_type,
        func.coalesce(func.sum(Transaction.amount), 0),
    ).group_by(Transaction.currency, Transaction.transaction_type)

    income_totals: dict[str, Decimal] = {}
    expense_totals: dict[str, Decimal] = {}
    balance_totals: dict[str, Decimal] = {}

    for currency, transaction_type, amount in grouped_totals.all():
        normalized_amount = _normalize_decimal(amount)
        normalized_type = _normalize_transaction_type(transaction_type)

        if normalized_type == TransactionType.income:
            income_totals[currency] = normalized_amount
            balance_totals[currency] = (
                balance_totals.get(currency, Decimal("0")) + normalized_amount
            )
        elif normalized_type == TransactionType.expense:
            expense_totals[currency] = normalized_amount
            balance_totals[currency] = (
                balance_totals.get(currency, Decimal("0")) - normalized_amount
            )

    return TransactionListSummary(
        active_categories_count=active_categories_count,
        skipped_transactions=0,
        income_totals=_serialize_totals(income_totals),
        expense_totals=_serialize_totals(expense_totals),
        balance_totals=_serialize_totals(balance_totals),
    )


def _serialize_totals(totals: dict[str, Decimal]) -> list[TransactionAggregateTotal]:
    return [
        TransactionAggregateTotal(currency=currency, amount=amount)
        for currency, amount in sorted(totals.items())
    ]


def _normalize_decimal(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return Decimal("0")

    if isinstance(value, Decimal):
        return value

    return Decimal(str(value))


def _get_category_for_user(
    db: Session,
    *,
    user_id: UUID,
    category_id: UUID | None,
) -> Category | None:
    if category_id is None:
        return None

    category = db.query(Category).filter(
        Category.id == category_id,
        Category.user_id == user_id,
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return category


def _resolve_transaction_type(
    *,
    category: Category | None,
    requested_type: TransactionType | str | None,
) -> TransactionType:
    normalized_type = _normalize_transaction_type(requested_type)

    if normalized_type in {TransactionType.transfer, TransactionType.adjustment}:
        raise HTTPException(
            status_code=409,
            detail="Only income and expense transaction types are supported right now",
        )

    if category is None:
        raise HTTPException(status_code=422, detail="Category is required")

    # Category direction is the canonical source of truth in the current UX.
    # Older/stale clients may still send an outdated income/expense value when
    # the user switches categories before saving, so normalize to the category.
    return _category_direction_to_transaction_type(category.direction)


def _category_direction_to_transaction_type(
    direction: CategoryDirection | str,
) -> TransactionType:
    normalized_direction = (
        direction.value if isinstance(direction, CategoryDirection) else str(direction)
    )
    if normalized_direction == CategoryDirection.income.value:
        return TransactionType.income
    return TransactionType.expense


def _normalize_transaction_type(
    value: TransactionType | str | None,
) -> TransactionType | None:
    if value is None or isinstance(value, TransactionType):
        return value
    return TransactionType(str(value))


def _transaction_type_to_balance_direction(
    transaction_type: TransactionType,
) -> BalanceDirection:
    if transaction_type == TransactionType.income:
        return BalanceDirection.inflow
    return BalanceDirection.outflow
