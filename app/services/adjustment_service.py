from __future__ import annotations

from uuid import UUID

from app.models.transaction import TransactionType
from app.schemas.ledger import AdjustmentCreate, LedgerMovementRead
from app.services.ledger_shared import (
    build_ledger_transaction,
    ensure_transaction_amount_is_positive,
    ensure_transaction_currency_matches_financial_account_currency,
    get_transaction_for_user,
    resolve_financial_account,
    serialize_ledger_movement,
)
from app.services.user_service import ensure_active_user
from sqlalchemy.orm import Session


def create_adjustment(
    db: Session,
    user_id: UUID,
    adjustment_data: AdjustmentCreate,
) -> LedgerMovementRead:
    user = ensure_active_user(db, user_id)
    ensure_transaction_amount_is_positive(adjustment_data.amount)

    financial_account = resolve_financial_account(
        db,
        user_id=user_id,
        user=user,
        financial_account_id=adjustment_data.financial_account_id,
    )
    ensure_transaction_currency_matches_financial_account_currency(
        financial_account=financial_account,
        transaction_currency=adjustment_data.currency,
    )

    transaction = build_ledger_transaction(
        db,
        user=user,
        financial_account=financial_account,
        transaction_type=TransactionType.adjustment,
        balance_direction=adjustment_data.balance_direction,
        amount=adjustment_data.amount,
        currency=adjustment_data.currency,
        occurred_at=adjustment_data.occurred_at,
        description=adjustment_data.description,
    )

    db.add(transaction)
    db.commit()
    db.refresh(transaction)

    return serialize_ledger_movement(
        transaction,
        financial_account_name=financial_account.name,
    )


def delete_adjustment(
    db: Session,
    user_id: UUID,
    adjustment_id: UUID,
) -> None:
    transaction = get_transaction_for_user(
        db,
        user_id,
        adjustment_id,
        allowed_types=(TransactionType.adjustment,),
    )
    db.delete(transaction)
    db.commit()
