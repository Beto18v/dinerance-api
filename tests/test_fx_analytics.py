from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.exchange_rate import ExchangeRate
from app.models.user import User
from app.services.exchange_rate_service import resolve_transaction_fx_snapshot


def create_configured_user(client):
    created = client.post(
        "/users/",
        json={
            "name": "FX User",
            "email": "test@example.com",
            "base_currency": "COP",
            "timezone": "UTC",
        },
    )
    assert created.status_code == 200
    return created.json()


def create_category(client, *, name: str, direction: str) -> dict:
    response = client.post(
        "/categories/",
        json={"name": name, "direction": direction, "parent_id": None},
    )
    assert response.status_code == 200
    return response.json()


def test_exchange_rate_snapshot_supports_usd_to_cop_lookup(
    client,
    db_session,
):
    create_configured_user(client)
    user = db_session.query(User).first()
    assert user is not None

    db_session.add(
        ExchangeRate(
            base_currency="USD",
            quote_currency="COP",
            rate_date=date(2026, 3, 1),
            rate=Decimal("4000.00000000"),
            source="manual_test",
        )
    )
    db_session.commit()

    snapshot = resolve_transaction_fx_snapshot(
        db_session,
        user=user,
        transaction_currency="USD",
        occurred_at=datetime(2026, 3, 3, 12, tzinfo=timezone.utc),
        amount=Decimal("100.00"),
    )

    assert snapshot.base_currency == "COP"
    assert snapshot.fx_rate == Decimal("4000.00000000")
    assert snapshot.fx_rate_date == date(2026, 3, 1)
    assert snapshot.fx_rate_source == "manual_test"
    assert snapshot.amount_in_base_currency == Decimal("400000.00")


def test_transaction_snapshot_and_balance_use_identity_base_currency(client):
    create_configured_user(client)

    income_category = create_category(client, name="Salary", direction="income")
    expense_category = create_category(client, name="Travel", direction="expense")

    income_tx = client.post(
        "/transactions/",
        json={
            "category_id": income_category["id"],
            "amount": "1000000.00",
            "currency": "COP",
            "description": "Salary",
            "occurred_at": datetime(2026, 3, 1, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert income_tx.status_code == 200
    assert income_tx.json()["amount_in_base_currency"] == "1000000.00"
    assert income_tx.json()["fx_rate_source"] == "identity"

    expense_tx = client.post(
        "/transactions/",
        json={
            "category_id": expense_category["id"],
            "amount": "400000.00",
            "currency": "COP",
            "description": "Rent",
            "occurred_at": datetime(2026, 3, 3, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert expense_tx.status_code == 200
    assert expense_tx.json()["base_currency"] == "COP"
    assert expense_tx.json()["fx_rate"] == "1.00000000"
    assert expense_tx.json()["fx_rate_source"] == "identity"
    assert expense_tx.json()["amount_in_base_currency"] == "400000.00"

    balance = client.get("/balance/monthly?year=2026&month=3")
    assert balance.status_code == 200
    assert balance.json() == {
        "currency": "COP",
        "current": {
            "month_start": "2026-03-01",
            "currency": "COP",
            "income": "1000000.00",
            "expense": "400000.00",
            "balance": "600000.00",
            "skipped_transactions": 0,
        },
        "series": [
            {
                "month_start": "2026-03-01",
                "currency": "COP",
                "income": "1000000.00",
                "expense": "400000.00",
                "balance": "600000.00",
                "skipped_transactions": 0,
            }
        ],
    }


def test_foreign_currency_account_transaction_uses_fx_snapshot_for_base_analytics(
    client,
    db_session,
):
    create_configured_user(client)
    expense_category = create_category(client, name="Food", direction="expense")
    usd_account = client.post(
        "/financial-accounts/",
        json={"name": "USD wallet", "currency": "USD"},
    )
    assert usd_account.status_code == 200

    db_session.add(
        ExchangeRate(
            base_currency="USD",
            quote_currency="COP",
            rate_date=date(2026, 3, 1),
            rate=Decimal("4000.00000000"),
            source="manual_test",
        )
    )
    db_session.commit()

    usd_expense = client.post(
        "/transactions/",
        json={
            "category_id": expense_category["id"],
            "financial_account_id": usd_account.json()["id"],
            "amount": "10.00",
            "currency": "USD",
            "description": "Coffee",
            "occurred_at": datetime(2026, 3, 3, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert usd_expense.status_code == 200
    assert usd_expense.json()["base_currency"] == "COP"
    assert usd_expense.json()["fx_rate"] == "4000.00000000"
    assert usd_expense.json()["fx_rate_source"] == "manual_test"
    assert usd_expense.json()["amount_in_base_currency"] == "40000.00"

    balance = client.get("/balance/monthly?year=2026&month=3")
    assert balance.status_code == 200
    assert balance.json()["current"] == {
        "month_start": "2026-03-01",
        "currency": "COP",
        "income": "0.00",
        "expense": "40000.00",
        "balance": "-40000.00",
        "skipped_transactions": 0,
    }


def test_transaction_rejects_currency_different_from_financial_account_currency(client):
    create_configured_user(client)
    expense_category = create_category(client, name="Food", direction="expense")
    usd_account = client.post(
        "/financial-accounts/",
        json={"name": "USD wallet", "currency": "USD"},
    )
    assert usd_account.status_code == 200

    cop_expense = client.post(
        "/transactions/",
        json={
            "category_id": expense_category["id"],
            "financial_account_id": usd_account.json()["id"],
            "amount": "10.00",
            "currency": "COP",
            "description": "Coffee",
            "occurred_at": datetime(2026, 3, 11, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert cop_expense.status_code == 409
    assert (
        cop_expense.json()["detail"]
        == "Transactions must use the financial account currency"
    )


def test_base_currency_cannot_change_after_transactions_exist(client):
    create_configured_user(client)
    expense_category = create_category(client, name="Bills", direction="expense")

    created = client.post(
        "/transactions/",
        json={
            "category_id": expense_category["id"],
            "amount": "50000.00",
            "currency": "COP",
            "description": "Power",
            "occurred_at": datetime(2026, 3, 12, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 200

    updated = client.put("/users/me", json={"base_currency": "USD"})
    assert updated.status_code == 409
    assert updated.json()["detail"] == "Base currency cannot change after transactions exist"
