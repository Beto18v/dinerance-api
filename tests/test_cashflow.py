from datetime import date

import pytest

from app.services.cashflow_service import _build_month_window


def create_configured_user(client, *, timezone_name: str = "UTC"):
    response = client.post(
        "/users/",
        json={
            "name": "Test User",
            "email": "test@example.com",
            "base_currency": "COP",
            "timezone": timezone_name,
        },
    )
    assert response.status_code == 200
    return response.json()


def create_category(client, *, name: str, direction: str) -> dict:
    response = client.post(
        "/categories/",
        json={"name": name, "direction": direction, "parent_id": None},
    )
    assert response.status_code == 200
    return response.json()


def create_obligation(
    client,
    *,
    name: str,
    amount: str,
    cadence: str,
    next_due_date: str,
    category_id: str,
    expected_financial_account_id: str | None = None,
    status: str | None = None,
):
    payload = {
        "name": name,
        "amount": amount,
        "cadence": cadence,
        "next_due_date": next_due_date,
        "category_id": category_id,
    }
    if expected_financial_account_id is not None:
        payload["expected_financial_account_id"] = expected_financial_account_id
    if status is not None:
        payload["status"] = status

    response = client.post("/obligations/", json=payload)
    assert response.status_code == 200
    return response.json()


def setup_user_with_balance(
    client,
    monkeypatch,
    reference_date: date,
    *,
    balance: str = "1000.00",
):
    cleanup = client.delete("/users/me")
    assert cleanup.status_code in (204, 404)

    create_configured_user(client, timezone_name="America/Bogota")
    default_account = client.get("/financial-accounts/").json()[0]

    opening_balance = client.post(
        "/adjustments/",
        json={
            "financial_account_id": default_account["id"],
            "balance_direction": "in",
            "amount": balance,
            "currency": "COP",
            "description": "Opening balance",
            "occurred_at": "2026-04-01T12:00:00Z",
        },
    )
    assert opening_balance.status_code == 200

    monkeypatch.setattr(
        "app.services.cashflow_service.resolve_obligation_reference_date",
        lambda _: reference_date,
    )

    return default_account


@pytest.mark.parametrize(
    "reference_date, month_offset, expected",
    [
        (date(2026, 7, 15), 0, (date(2026, 7, 15), date(2026, 7, 31), 17)),
        (date(2026, 7, 1), 0, (date(2026, 7, 1), date(2026, 7, 31), 31)),
        (date(2026, 7, 15), 1, (date(2026, 8, 1), date(2026, 8, 31), 31)),
        (date(2026, 7, 15), 2, (date(2026, 9, 1), date(2026, 9, 30), 30)),
        (date(2026, 1, 31), 1, (date(2026, 2, 1), date(2026, 2, 28), 28)),
        (date(2028, 2, 10), 0, (date(2028, 2, 10), date(2028, 2, 29), 20)),
        (date(2026, 4, 30), 1, (date(2026, 5, 1), date(2026, 5, 31), 31)),
    ],
)
def test_build_month_window(reference_date, month_offset, expected):
    assert _build_month_window(reference_date, month_offset) == expected


def test_cashflow_forecast_returns_three_monthly_windows(client, monkeypatch):
    setup_user_with_balance(client, monkeypatch, date(2026, 7, 15))

    response = client.get("/cashflow/forecast")
    assert response.status_code == 200

    data = response.json()
    assert data["reference_date"] == "2026-07-15"
    assert data["currency"] == "COP"
    assert data["current_balance"] == "1000.00"

    horizons = data["horizons"]
    assert len(horizons) == 3

    assert horizons[0] == {
        "month_offset": 0,
        "month_label": "Jul 2026",
        "days_in_window": 17,
        "scheduled_payments_count": 0,
        "confirmed_obligations_total": "0.00",
        "projected_balance": "1000.00",
        "safe_to_spend": "1000.00",
        "safe_to_spend_per_day": "58.82",
        "shortfall_amount": "0.00",
        "status": "covered",
    }
    assert horizons[1] == {
        "month_offset": 1,
        "month_label": "Aug 2026",
        "days_in_window": 31,
        "scheduled_payments_count": 0,
        "confirmed_obligations_total": "0.00",
        "projected_balance": "1000.00",
        "safe_to_spend": "1000.00",
        "safe_to_spend_per_day": "32.26",
        "shortfall_amount": "0.00",
        "status": "covered",
    }
    assert horizons[2] == {
        "month_offset": 2,
        "month_label": "Sep 2026",
        "days_in_window": 30,
        "scheduled_payments_count": 0,
        "confirmed_obligations_total": "0.00",
        "projected_balance": "1000.00",
        "safe_to_spend": "1000.00",
        "safe_to_spend_per_day": "33.33",
        "shortfall_amount": "0.00",
        "status": "covered",
    }

    safe_to_spend = data["safe_to_spend"]
    assert safe_to_spend["month_offset"] == 0
    assert safe_to_spend["month_label"] == "Jul 2026"
    assert safe_to_spend["days_in_window"] == 17
    assert safe_to_spend["projected_balance"] == "1000.00"
    assert safe_to_spend["safe_to_spend"] == "1000.00"
    assert safe_to_spend["safe_to_spend_per_day"] == "58.82"


def test_cashflow_forecast_excludes_overdue_and_includes_same_day_obligation(
    client,
    monkeypatch,
):
    default_account = setup_user_with_balance(client, monkeypatch, date(2026, 7, 15))
    fixed_costs = create_category(client, name="Fixed costs", direction="expense")

    create_obligation(
        client,
        name="Overdue",
        amount="100.00",
        cadence="monthly",
        next_due_date="2026-07-10",
        category_id=fixed_costs["id"],
        expected_financial_account_id=default_account["id"],
    )
    create_obligation(
        client,
        name="Today",
        amount="200.00",
        cadence="monthly",
        next_due_date="2026-07-15",
        category_id=fixed_costs["id"],
        expected_financial_account_id=default_account["id"],
    )

    response = client.get("/cashflow/forecast")
    assert response.status_code == 200

    window_0 = response.json()["horizons"][0]
    assert window_0["scheduled_payments_count"] == 1
    assert window_0["confirmed_obligations_total"] == "200.00"
    assert window_0["projected_balance"] == "800.00"


def test_safe_to_spend_per_day_partial_month(client, monkeypatch):
    setup_user_with_balance(
        client,
        monkeypatch,
        date(2026, 7, 20),
        balance="3000.00",
    )

    response = client.get("/cashflow/safe-to-spend?month_offset=0")
    assert response.status_code == 200

    data = response.json()
    assert data["month_offset"] == 0
    assert data["month_label"] == "Jul 2026"
    assert data["days_in_window"] == 12
    assert data["projected_balance"] == "3000.00"
    assert data["safe_to_spend"] == "3000.00"
    assert data["safe_to_spend_per_day"] == "250.00"


def test_safe_to_spend_per_day_full_month(client, monkeypatch):
    setup_user_with_balance(
        client,
        monkeypatch,
        date(2026, 7, 1),
        balance="3100.00",
    )

    response = client.get("/cashflow/safe-to-spend?month_offset=1")
    assert response.status_code == 200

    data = response.json()
    assert data["month_offset"] == 1
    assert data["month_label"] == "Aug 2026"
    assert data["days_in_window"] == 31
    assert data["projected_balance"] == "3100.00"
    assert data["safe_to_spend"] == "3100.00"
    assert data["safe_to_spend_per_day"] == "100.00"


def test_safe_to_spend_rejects_invalid_month_offset(client):
    response = client.get("/cashflow/safe-to-spend?month_offset=12")
    assert response.status_code == 422
