from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import and_, case, func
from sqlalchemy.orm import Session, aliased

from app.models.category import Category
from app.models.financial_account import FinancialAccount
from app.models.transaction import BalanceDirection, Transaction, TransactionType
from app.schemas.ledger import (
    LedgerActivityRead,
    LedgerBalanceAccountRead,
    LedgerBalancesRead,
)
from app.services.financial_account_service import (
    get_financial_account_for_user,
    list_financial_accounts,
)
from app.services.ledger_shared import (
    ensure_active_user_with_money_profile,
    serialize_ledger_movement,
)


def get_ledger_balances(
    db: Session,
    user_id: UUID,
) -> LedgerBalancesRead:
    user = ensure_active_user_with_money_profile(db, user_id)
    financial_accounts = list_financial_accounts(db, user_id)

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
        .filter(Transaction.user_id == user_id)
        .group_by(Transaction.financial_account_id)
        .all()
    )
    balance_by_account_id = {
        row.financial_account_id: _normalize_decimal(row.balance)
        for row in rows
    }
    usable_signed_base_amount = case(
        (
            and_(
                Transaction.base_currency == user.base_currency,
                Transaction.amount_in_base_currency.isnot(None),
                Transaction.balance_direction == BalanceDirection.inflow,
            ),
            Transaction.amount_in_base_currency,
        ),
        (
            and_(
                Transaction.base_currency == user.base_currency,
                Transaction.amount_in_base_currency.isnot(None),
                Transaction.balance_direction == BalanceDirection.outflow,
            ),
            -Transaction.amount_in_base_currency,
        ),
        else_=None,
    )
    base_rows = (
        db.query(
            Transaction.financial_account_id,
            func.sum(usable_signed_base_amount).label("balance_in_base_currency"),
        )
        .filter(Transaction.user_id == user_id)
        .group_by(Transaction.financial_account_id)
        .all()
    )
    base_balance_by_account_id = {
        row.financial_account_id: _normalize_decimal(row.balance_in_base_currency)
        for row in base_rows
        if row.balance_in_base_currency is not None
    }
    consolidated_balance = _normalize_decimal(
        db.query(func.coalesce(func.sum(usable_signed_base_amount), 0))
        .filter(Transaction.user_id == user_id)
        .scalar()
    )
    skipped_transactions = int(
        db.query(
            func.count(
                case(
                    (usable_signed_base_amount.is_(None), 1),
                    else_=None,
                )
            )
        )
        .filter(Transaction.user_id == user_id)
        .scalar()
        or 0
    )

    accounts = [
        LedgerBalanceAccountRead(
            financial_account_id=account.id,
            financial_account_name=account.name,
            currency=account.currency or user.base_currency,
            balance=balance_by_account_id.get(account.id, Decimal("0.00")),
            balance_in_base_currency=base_balance_by_account_id.get(account.id),
        )
        for account in financial_accounts
    ]

    return LedgerBalancesRead(
        currency=user.base_currency,
        consolidated_balance=consolidated_balance,
        skipped_transactions=skipped_transactions,
        accounts=accounts,
    )


def get_ledger_activity(
    db: Session,
    user_id: UUID,
    *,
    financial_account_id: UUID | None = None,
    limit: int = 20,
) -> LedgerActivityRead:
    ensure_active_user_with_money_profile(db, user_id)
    if financial_account_id is not None:
        get_financial_account_for_user(db, user_id, financial_account_id)

    return LedgerActivityRead(
        limit=limit,
        items=_load_ledger_rows(
            db,
            user_id=user_id,
            financial_account_id=financial_account_id,
            limit=limit,
        ),
    )


def get_ledger_adjustments(
    db: Session,
    user_id: UUID,
    *,
    financial_account_id: UUID | None = None,
    limit: int = 20,
) -> LedgerActivityRead:
    ensure_active_user_with_money_profile(db, user_id)
    if financial_account_id is not None:
        get_financial_account_for_user(db, user_id, financial_account_id)

    return LedgerActivityRead(
        limit=limit,
        items=_load_ledger_rows(
            db,
            user_id=user_id,
            financial_account_id=financial_account_id,
            limit=limit,
            transaction_types=(TransactionType.adjustment,),
        ),
    )


def _load_ledger_rows(
    db: Session,
    *,
    user_id: UUID,
    financial_account_id: UUID | None,
    limit: int,
    transaction_types: tuple[TransactionType, ...] | None = None,
):
    counterparty_transaction = aliased(Transaction)
    counterparty_account = aliased(FinancialAccount)

    filters = [Transaction.user_id == user_id]
    if financial_account_id is not None:
        filters.append(Transaction.financial_account_id == financial_account_id)
    if transaction_types is not None:
        filters.append(Transaction.transaction_type.in_(transaction_types))

    rows = (
        db.query(
            Transaction,
            Category.name.label("category_name"),
            FinancialAccount.name.label("financial_account_name"),
            counterparty_transaction.financial_account_id.label(
                "counterparty_financial_account_id"
            ),
            counterparty_account.name.label("counterparty_financial_account_name"),
        )
        .join(
            FinancialAccount,
            Transaction.financial_account_id == FinancialAccount.id,
        )
        .outerjoin(
            Category,
            and_(
                Transaction.category_id == Category.id,
                Category.user_id == user_id,
            ),
        )
        .outerjoin(
            counterparty_transaction,
            and_(
                Transaction.transfer_group_id.isnot(None),
                counterparty_transaction.user_id == user_id,
                counterparty_transaction.transfer_group_id
                == Transaction.transfer_group_id,
                counterparty_transaction.id != Transaction.id,
            ),
        )
        .outerjoin(
            counterparty_account,
            counterparty_account.id == counterparty_transaction.financial_account_id,
        )
        .filter(*filters)
        .order_by(
            Transaction.occurred_at.desc(),
            Transaction.created_at.desc(),
            Transaction.id.desc(),
        )
        .limit(limit)
        .all()
    )

    return [
        serialize_ledger_movement(
            transaction,
            category_name=category_name,
            financial_account_name=financial_account_name,
            counterparty_financial_account_id=counterparty_financial_account_id,
            counterparty_financial_account_name=counterparty_financial_account_name,
        )
        for (
            transaction,
            category_name,
            financial_account_name,
            counterparty_financial_account_id,
            counterparty_financial_account_name,
        ) in rows
    ]


def _normalize_decimal(value: Decimal | int | float | None) -> Decimal:
    if value is None:
        return Decimal("0.00")

    if isinstance(value, Decimal):
        normalized = value
    else:
        normalized = Decimal(str(value))

    return normalized.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
