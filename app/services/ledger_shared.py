from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.category import Category
from app.models.financial_account import FinancialAccount
from app.models.transaction import BalanceDirection, Transaction, TransactionType
from app.schemas.ledger import LedgerMovementRead
from app.services.exchange_rate_service import (
    apply_transaction_fx_snapshot,
    resolve_transaction_fx_snapshot,
)
from app.services.financial_account_service import (
    ensure_default_financial_account,
    get_financial_account_for_user,
)
from app.services.user_service import ensure_active_user


PUBLIC_TRANSACTION_TYPES = (
    TransactionType.income,
    TransactionType.expense,
)


def ensure_transaction_amount_is_positive(amount: Decimal) -> None:
    if amount <= 0:
        raise HTTPException(
            status_code=422,
            detail="Transaction amount must be greater than zero",
        )


def ensure_transaction_currency_matches_user_base_currency(
    *,
    user_base_currency: str | None,
    transaction_currency: str,
) -> None:
    if not user_base_currency:
        raise HTTPException(
            status_code=409,
            detail="User base currency must be configured before creating transactions",
        )

    if transaction_currency != user_base_currency:
        raise HTTPException(
            status_code=409,
            detail="Transactions must use the user's base currency",
        )


def ensure_transaction_currency_matches_financial_account_currency(
    *,
    financial_account: FinancialAccount,
    transaction_currency: str,
) -> None:
    if (
        financial_account.currency is not None
        and transaction_currency != financial_account.currency
    ):
        raise HTTPException(
            status_code=409,
            detail="Transactions must use the financial account currency",
        )


def ensure_financial_accounts_share_currency(
    *,
    source_account: FinancialAccount,
    destination_account: FinancialAccount,
) -> None:
    if (
        source_account.currency is not None
        and destination_account.currency is not None
        and source_account.currency != destination_account.currency
    ):
        raise HTTPException(
            status_code=409,
            detail="Transfers between accounts with different currencies are not supported yet",
        )


def resolve_financial_account(
    db: Session,
    *,
    user_id: UUID,
    user,
    financial_account_id: UUID | None,
) -> FinancialAccount:
    if financial_account_id is None:
        account, _ = ensure_default_financial_account(db, user)
        return account

    account = get_financial_account_for_user(db, user_id, financial_account_id)
    if account.currency is None and user.base_currency is not None:
        account.currency = user.base_currency
    return account


def get_transaction_for_user(
    db: Session,
    user_id: UUID,
    transaction_id: UUID,
    *,
    allowed_types: Iterable[TransactionType] | None = None,
) -> Transaction:
    query = db.query(Transaction).filter(
        Transaction.id == transaction_id,
        Transaction.user_id == user_id,
    )
    if allowed_types is not None:
        query = query.filter(Transaction.transaction_type.in_(tuple(allowed_types)))

    transaction = query.first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction


def build_ledger_transaction(
    db: Session,
    *,
    user,
    financial_account: FinancialAccount,
    transaction_type: TransactionType,
    balance_direction: BalanceDirection,
    amount: Decimal,
    currency: str,
    occurred_at: datetime,
    description: str | None = None,
    category: Category | None = None,
    transfer_group_id: UUID | None = None,
) -> Transaction:
    snapshot = resolve_transaction_fx_snapshot(
        db,
        user=user,
        transaction_currency=currency,
        occurred_at=occurred_at,
        amount=amount,
    )

    transaction = Transaction(
        user_id=user.id,
        financial_account_id=financial_account.id,
        category_id=category.id if category else None,
        transaction_type=transaction_type,
        balance_direction=balance_direction,
        transfer_group_id=transfer_group_id,
        amount=amount,
        currency=currency,
        description=description,
        occurred_at=occurred_at,
    )
    apply_transaction_fx_snapshot(transaction, snapshot)
    return transaction


def serialize_ledger_movement(
    transaction: Transaction,
    *,
    category_name: str | None = None,
    financial_account_name: str | None = None,
    counterparty_financial_account_id: UUID | None = None,
    counterparty_financial_account_name: str | None = None,
) -> LedgerMovementRead:
    return LedgerMovementRead(
        id=transaction.id,
        category_id=transaction.category_id,
        category_name=category_name,
        financial_account_id=transaction.financial_account_id,
        financial_account_name=financial_account_name,
        counterparty_financial_account_id=counterparty_financial_account_id,
        counterparty_financial_account_name=counterparty_financial_account_name,
        transaction_type=transaction.transaction_type,
        balance_direction=transaction.balance_direction,
        transfer_group_id=transaction.transfer_group_id,
        amount=transaction.amount,
        currency=transaction.currency,
        base_currency=transaction.base_currency,
        amount_in_base_currency=transaction.amount_in_base_currency,
        description=transaction.description,
        occurred_at=transaction.occurred_at,
        created_at=transaction.created_at,
    )


def ensure_active_user_with_money_profile(db: Session, user_id: UUID):
    user = ensure_active_user(db, user_id)
    if not user.base_currency:
        raise HTTPException(
            status_code=409,
            detail="User base currency must be configured before calculating balances",
        )
    return user
