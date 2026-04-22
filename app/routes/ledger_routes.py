from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user_id
from app.database.session import get_db
from app.schemas.ledger import LedgerActivityRead, LedgerBalancesRead
from app.services.ledger_service import (
    get_ledger_activity,
    get_ledger_adjustments,
    get_ledger_balances,
)
from app.services.user_service import ensure_active_user

router = APIRouter(prefix="/ledger", tags=["Ledger"])


@router.get("/balances", response_model=LedgerBalancesRead)
def get_ledger_balances_endpoint(
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    ensure_active_user(db, user_id)
    return get_ledger_balances(db, user_id)


@router.get("/activity", response_model=LedgerActivityRead)
def get_ledger_activity_endpoint(
    user_id=Depends(get_current_user_id),
    financial_account_id: UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    ensure_active_user(db, user_id)
    return get_ledger_activity(
        db,
        user_id,
        financial_account_id=financial_account_id,
        limit=limit,
    )


@router.get("/adjustments", response_model=LedgerActivityRead)
def get_ledger_adjustments_endpoint(
    user_id=Depends(get_current_user_id),
    financial_account_id: UUID | None = None,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    ensure_active_user(db, user_id)
    return get_ledger_adjustments(
        db,
        user_id,
        financial_account_id=financial_account_id,
        limit=limit,
    )
