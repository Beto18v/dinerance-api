from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import get_current_user_id
from app.database.session import get_db
from app.schemas.cashflow import CashflowForecastRead, SafeToSpendRead
from app.services.cashflow_service import (
    get_cashflow_forecast,
    get_safe_to_spend,
)
from app.services.user_service import ensure_active_user

router = APIRouter(prefix="/cashflow", tags=["Cashflow"])


@router.get("/forecast", response_model=CashflowForecastRead)
def get_cashflow_forecast_endpoint(
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    ensure_active_user(db, user_id)
    return get_cashflow_forecast(db, user_id)


@router.get("/safe-to-spend", response_model=SafeToSpendRead)
def get_safe_to_spend_endpoint(
    month_offset: int = Query(0, ge=0, le=11),
    user_id=Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    ensure_active_user(db, user_id)
    return get_safe_to_spend(
        db,
        user_id,
        month_offset=month_offset,
    )
