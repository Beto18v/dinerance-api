from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import false
from sqlalchemy.orm import Session

from app.models.financial_account import FinancialAccount
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.financial_account import FinancialAccountCreate, FinancialAccountUpdate

DEFAULT_FINANCIAL_ACCOUNT_NAME = "Main account"


def list_financial_accounts(db: Session, user_id: UUID) -> list[FinancialAccount]:
    user = _get_active_user(db, user_id)
    _, changed = ensure_default_financial_account(db, user)
    if changed:
        db.commit()

    return _list_user_financial_accounts(db, user_id)


def get_financial_account(db: Session, user_id: UUID, account_id: UUID) -> FinancialAccount:
    user = _get_active_user(db, user_id)
    _, changed = ensure_default_financial_account(db, user)
    if changed:
        db.commit()

    return get_financial_account_for_user(db, user_id, account_id)


def create_financial_account(
    db: Session,
    user_id: UUID,
    account_data: FinancialAccountCreate,
) -> FinancialAccount:
    user = _get_active_user(db, user_id)
    existing_accounts = _list_user_financial_accounts(db, user_id)
    should_be_default = account_data.is_default or len(existing_accounts) == 0

    account = FinancialAccount(
        user_id=user_id,
        name=account_data.name,
        currency=_resolve_account_currency(
            requested_currency=account_data.currency,
            fallback_currency=user.base_currency,
        ),
        is_default=False,
    )
    db.add(account)
    db.flush()

    if should_be_default:
        _set_default_financial_account(db, user_id, account)

    db.commit()
    db.refresh(account)
    return account


def update_financial_account(
    db: Session,
    user_id: UUID,
    account_id: UUID,
    account_data: FinancialAccountUpdate,
) -> FinancialAccount:
    user = _get_active_user(db, user_id)
    account = get_financial_account_for_user(db, user_id, account_id)
    updates = account_data.model_dump(exclude_unset=True)

    if "name" in updates and updates["name"] is not None:
        account.name = updates["name"]

    if "currency" in updates:
        requested_currency = updates["currency"]
        if requested_currency is None:
            raise HTTPException(status_code=422, detail="Currency is required")
        if requested_currency != account.currency:
            if financial_account_has_transactions(db, account.id):
                raise HTTPException(
                    status_code=409,
                    detail="Financial account currency cannot change after transactions exist",
                )
            account.currency = requested_currency

    if updates.get("is_default") is True:
        _set_default_financial_account(db, user_id, account)
    elif updates.get("is_default") is False and account.is_default:
        raise HTTPException(
            status_code=409,
            detail="A default financial account is required",
        )

    db.commit()
    db.refresh(account)
    return account


def delete_financial_account(db: Session, user_id: UUID, account_id: UUID) -> None:
    account = get_financial_account_for_user(db, user_id, account_id)
    remaining_accounts = _list_user_financial_accounts(
        db,
        user_id,
        exclude_account_id=account.id,
    )

    if not remaining_accounts:
        raise HTTPException(
            status_code=409,
            detail="At least one financial account is required",
        )

    replacement_default = (
        _select_replacement_default_account(db, remaining_accounts)
        if account.is_default
        else None
    )

    # Soft delete: mark as deleted instead of physically deleting
    account.deleted_at = datetime.now(timezone.utc)
    db.flush()

    if replacement_default is not None:
        _set_default_financial_account(db, user_id, replacement_default)

    db.commit()


def get_financial_account_for_user(
    db: Session,
    user_id: UUID,
    account_id: UUID,
) -> FinancialAccount:
    account = (
        db.query(FinancialAccount)
        .filter(
            FinancialAccount.id == account_id,
            FinancialAccount.user_id == user_id,
            FinancialAccount.deleted_at.is_(None),
        )
        .first()
    )
    if not account:
        raise HTTPException(status_code=404, detail="Financial account not found")
    return account


def ensure_default_financial_account(
    db: Session,
    user: User,
) -> tuple[FinancialAccount, bool]:
    accounts = _list_user_financial_accounts(db, user.id)

    if not accounts:
        account = FinancialAccount(
            user_id=user.id,
            name=DEFAULT_FINANCIAL_ACCOUNT_NAME,
            currency=user.base_currency,
            is_default=True,
        )
        db.add(account)
        db.flush()
        return account, True

    changed = False
    default_account: FinancialAccount | None = None

    for account in accounts:
        if account.currency is None and user.base_currency is not None:
            account.currency = user.base_currency
            changed = True

        if account.is_default and default_account is None:
            default_account = account
            continue

        if account.is_default:
            account.is_default = False
            changed = True

    if default_account is None:
        default_account = accounts[0]
        if not default_account.is_default:
            default_account.is_default = True
            changed = True

    return default_account, changed


def sync_default_financial_account_currency_with_user_base_currency(
    db: Session,
    user: User,
    *,
    previous_base_currency: str | None,
) -> bool:
    default_account, changed = ensure_default_financial_account(db, user)
    if user.base_currency is None:
        return changed

    if (
        default_account.currency is None
        or default_account.currency == previous_base_currency
    ) and default_account.currency != user.base_currency:
        default_account.currency = user.base_currency
        return True

    return changed


def financial_account_has_transactions(db: Session, account_id: UUID) -> bool:
    return get_financial_account_transaction_count(db, account_id) > 0


def get_financial_account_transaction_count(db: Session, account_id: UUID) -> int:
    return int(
        db.query(Transaction.id)
        .filter(Transaction.financial_account_id == account_id)
        .count()
    )


def _select_replacement_default_account(
    db: Session,
    accounts: list[FinancialAccount],
) -> FinancialAccount:
    account_with_transactions = next(
        (
            account
            for account in accounts
            if financial_account_has_transactions(db, account.id)
        ),
        None,
    )
    return account_with_transactions or accounts[0]


def _set_default_financial_account(
    db: Session,
    user_id: UUID,
    account: FinancialAccount,
) -> None:
    (
        db.query(FinancialAccount)
        .filter(
            FinancialAccount.user_id == user_id,
            FinancialAccount.id != account.id,
            FinancialAccount.is_default.is_(True),
        )
        .update({FinancialAccount.is_default: false()}, synchronize_session=False)
    )
    db.flush()
    account.is_default = True


def _get_active_user(db: Session, user_id: UUID) -> User:
    user: User | None = (
        db.query(User)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def _list_user_financial_accounts(
    db: Session,
    user_id: UUID,
    *,
    exclude_account_id: UUID | None = None,
) -> list[FinancialAccount]:
    query = db.query(FinancialAccount).filter(
        FinancialAccount.user_id == user_id,
        FinancialAccount.deleted_at.is_(None),
    )
    if exclude_account_id is not None:
        query = query.filter(FinancialAccount.id != exclude_account_id)

    return query.order_by(
        FinancialAccount.is_default.desc(),
        FinancialAccount.created_at.asc(),
        FinancialAccount.id.asc(),
    ).all()


def _resolve_account_currency(
    *,
    requested_currency: str | None,
    fallback_currency: str | None,
) -> str | None:
    if requested_currency is not None:
        return requested_currency
    return fallback_currency
