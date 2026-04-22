from __future__ import annotations

import uuid
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.transaction import BalanceDirection, Transaction, TransactionType
from app.schemas.ledger import TransferCreate, TransferRead
from app.services.ledger_shared import (
    build_ledger_transaction,
    ensure_financial_accounts_share_currency,
    ensure_transaction_amount_is_positive,
    ensure_transaction_currency_matches_financial_account_currency,
    resolve_financial_account,
    serialize_ledger_movement,
)
from app.services.user_service import ensure_active_user


def create_transfer(
    db: Session,
    user_id: UUID,
    transfer_data: TransferCreate,
) -> TransferRead:
    user = ensure_active_user(db, user_id)
    ensure_transaction_amount_is_positive(transfer_data.amount)

    if (
        transfer_data.source_financial_account_id
        == transfer_data.destination_financial_account_id
    ):
        raise HTTPException(
            status_code=409,
            detail="Source and destination accounts must be different",
        )

    source_account = resolve_financial_account(
        db,
        user_id=user_id,
        user=user,
        financial_account_id=transfer_data.source_financial_account_id,
    )
    destination_account = resolve_financial_account(
        db,
        user_id=user_id,
        user=user,
        financial_account_id=transfer_data.destination_financial_account_id,
    )

    ensure_financial_accounts_share_currency(
        source_account=source_account,
        destination_account=destination_account,
    )
    ensure_transaction_currency_matches_financial_account_currency(
        financial_account=source_account,
        transaction_currency=transfer_data.currency,
    )
    ensure_transaction_currency_matches_financial_account_currency(
        financial_account=destination_account,
        transaction_currency=transfer_data.currency,
    )

    transfer_group_id = uuid.uuid4()
    source_transaction = build_ledger_transaction(
        db,
        user=user,
        financial_account=source_account,
        transaction_type=TransactionType.transfer,
        balance_direction=BalanceDirection.outflow,
        amount=transfer_data.amount,
        currency=transfer_data.currency,
        occurred_at=transfer_data.occurred_at,
        description=transfer_data.description,
        transfer_group_id=transfer_group_id,
    )
    destination_transaction = build_ledger_transaction(
        db,
        user=user,
        financial_account=destination_account,
        transaction_type=TransactionType.transfer,
        balance_direction=BalanceDirection.inflow,
        amount=transfer_data.amount,
        currency=transfer_data.currency,
        occurred_at=transfer_data.occurred_at,
        description=transfer_data.description,
        transfer_group_id=transfer_group_id,
    )

    db.add(source_transaction)
    db.add(destination_transaction)
    db.commit()
    db.refresh(source_transaction)
    db.refresh(destination_transaction)

    return TransferRead(
        transfer_group_id=transfer_group_id,
        source_transaction=serialize_ledger_movement(
            source_transaction,
            financial_account_name=source_account.name,
            counterparty_financial_account_id=destination_account.id,
            counterparty_financial_account_name=destination_account.name,
        ),
        destination_transaction=serialize_ledger_movement(
            destination_transaction,
            financial_account_name=destination_account.name,
            counterparty_financial_account_id=source_account.id,
            counterparty_financial_account_name=source_account.name,
        ),
    )


def delete_transfer(
    db: Session,
    user_id: UUID,
    transfer_group_id: UUID,
) -> None:
    transactions = (
        db.query(Transaction)
        .filter(
            Transaction.user_id == user_id,
            Transaction.transfer_group_id == transfer_group_id,
            Transaction.transaction_type == TransactionType.transfer,
        )
        .all()
    )
    if not transactions:
        raise HTTPException(status_code=404, detail="Transfer not found")

    directions = {transaction.balance_direction for transaction in transactions}
    if len(transactions) != 2 or directions != {
        BalanceDirection.inflow,
        BalanceDirection.outflow,
    }:
        raise HTTPException(status_code=409, detail="Transfer group is invalid")

    for transaction in transactions:
        db.delete(transaction)
    db.commit()
