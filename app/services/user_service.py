from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.user import User
from app.models.transaction import Transaction
from app.schemas.user import UserBootstrap, UserCreate, UserUpdate
from app.services.exchange_rate_service import refresh_transaction_fx_snapshots_for_user
from app.services.financial_account_service import (
    ensure_default_financial_account,
    sync_default_financial_account_currency_with_user_base_currency,
)

DEFAULT_USER_NAME = "User"


def _clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None

    cleaned = value.strip()
    return cleaned or None


def _claim_email(claims: dict[str, Any]) -> str | None:
    email = claims.get("email")
    if not isinstance(email, str):
        return None

    cleaned = email.strip()
    return cleaned or None


def _claim_name(claims: dict[str, Any]) -> str | None:
    candidates: list[Any] = [
        claims.get("name"),
        claims.get("full_name"),
    ]

    user_metadata = claims.get("user_metadata")
    if isinstance(user_metadata, dict):
        candidates.extend(
            [
                user_metadata.get("full_name"),
                user_metadata.get("name"),
                user_metadata.get("display_name"),
            ]
        )

    for candidate in candidates:
        cleaned = _clean_name(candidate)
        if cleaned:
            return cleaned

    return None


def create_user(db: Session, user_id: UUID, user_data: UserCreate) -> User:
    user: User | None = db.query(User).filter(User.id == user_id).first()
    if user:
        update_data = user_data.model_dump(exclude_unset=True)
        if "email" in update_data and update_data["email"] is not None:
            update_data["email"] = str(update_data["email"])
        changed = _reactivate_user(user)
        changed = _apply_user_updates(
            db,
            user,
            update_data,
        ) or changed
        _, account_changed = ensure_default_financial_account(db, user)
        if changed or update_data or account_changed:
            db.commit()
            db.refresh(user)
        return user

    user = User(
        id=user_id,
        name=user_data.name,
        email=str(user_data.email),
        base_currency=user_data.base_currency,
        timezone=user_data.timezone,
    )
    db.add(user)
    ensure_default_financial_account(db, user)
    db.commit()
    db.refresh(user)
    return user


def get_current_active_user_from_claims(
    db: Session,
    user_id: UUID,
    claims: dict[str, Any],
) -> User:
    user: User | None = (
        db.query(User)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    email = _claim_email(claims)
    changed = False

    if email and user.email != email:
        user.email = email
        changed = True

    if changed:
        db.commit()
        db.refresh(user)

    return user


def ensure_active_user(db: Session, user_id: UUID) -> User:
    user: User | None = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def update_current_user(db: Session, user_id: UUID, user_data: UserUpdate) -> User:
    user: User | None = db.query(User).filter(User.id == user_id, User.deleted_at.is_(None)).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    updated_fields: dict[str, Any] = user_data.model_dump(exclude_unset=True)
    _apply_user_updates(db, user, updated_fields)
    ensure_default_financial_account(db, user)

    db.commit()
    db.refresh(user)
    return user


def bootstrap_current_user(
    db: Session,
    user_id: UUID,
    claims: dict[str, Any],
    user_data: UserBootstrap | None = None,
) -> User:
    user: User | None = db.query(User).filter(User.id == user_id).first()

    email = _claim_email(claims)
    if not email:
        raise HTTPException(status_code=400, detail="Authenticated user email is missing")

    requested_name = _clean_name(user_data.name) if user_data else None
    fallback_name = _claim_name(claims) or DEFAULT_USER_NAME

    if user:
        changed = _reactivate_user(user)

        if user.email != email:
            user.email = email
            changed = True

        if requested_name and user.name != requested_name:
            user.name = requested_name
            changed = True

        if user_data:
            bootstrap_updates = user_data.model_dump(exclude_unset=True)
            if bootstrap_updates:
                changed = _apply_user_updates(db, user, bootstrap_updates) or changed

        _, account_changed = ensure_default_financial_account(db, user)
        changed = account_changed or changed

        if changed:
            db.commit()
            db.refresh(user)

        return user

    user = User(
        id=user_id,
        name=requested_name or fallback_name,
        email=email,
        base_currency=user_data.base_currency if user_data else None,
        timezone=user_data.timezone if user_data else None,
    )
    db.add(user)
    ensure_default_financial_account(db, user)
    db.commit()
    db.refresh(user)
    return user


def delete_current_user(db: Session, user_id: UUID) -> None:
    user: User | None = (
        db.query(User)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Soft delete the local profile. Domain data remain available for a future
    # bootstrap/reactivation flow.
    user.deleted_at = datetime.now(timezone.utc)
    db.commit()


def _apply_user_updates(
    db: Session,
    user: User,
    updated_fields: dict[str, Any],
) -> bool:
    changed = False

    if "name" in updated_fields and isinstance(updated_fields["name"], str):
        if user.name != updated_fields["name"]:
            user.name = updated_fields["name"]
            changed = True

    if "email" in updated_fields and updated_fields["email"] is not None:
        normalized_email = str(updated_fields["email"])
        if user.email != normalized_email:
            user.email = normalized_email
            changed = True

    if "timezone" in updated_fields:
        timezone_name = updated_fields["timezone"]
        if timezone_name is None:
            raise HTTPException(status_code=422, detail="Timezone is required")
        if user.timezone != timezone_name:
            user.timezone = timezone_name
            changed = True

    if "base_currency" in updated_fields:
        base_currency = updated_fields["base_currency"]
        if base_currency is None:
            raise HTTPException(status_code=422, detail="Base currency is required")

        previous_base_currency = user.base_currency
        if user.base_currency != base_currency:
            has_transactions = _user_has_transactions(db, user.id)
            if previous_base_currency is not None and has_transactions:
                raise HTTPException(
                    status_code=409,
                    detail="Base currency cannot change after transactions exist",
                )

            first_base_currency_assignment = previous_base_currency is None
            user.base_currency = base_currency
            changed = True

            if first_base_currency_assignment and has_transactions:
                refresh_transaction_fx_snapshots_for_user(db, user)
            elif not first_base_currency_assignment:
                changed = (
                    sync_default_financial_account_currency_with_user_base_currency(
                        db,
                        user,
                        previous_base_currency=previous_base_currency,
                    )
                    or changed
                )

    return changed


def _user_has_transactions(db: Session, user_id: UUID) -> bool:
    return (
        db.query(Transaction.id)
        .filter(Transaction.user_id == user_id)
        .limit(1)
        .first()
        is not None
    )


def _reactivate_user(user: User) -> bool:
    if user.deleted_at is None:
        return False

    user.deleted_at = None
    return True
