from datetime import date, datetime, timezone
from decimal import Decimal

from app.models.exchange_rate import ExchangeRate


def create_configured_user(client):
    response = client.post(
        "/users/",
        json={
            "name": "Test User",
            "email": "test@example.com",
            "base_currency": "COP",
            "timezone": "UTC",
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


def create_income(
    client,
    *,
    financial_account_id: str,
    category_id: str,
    amount: str,
    currency: str = "COP",
    description: str,
    occurred_at: datetime,
):
    response = client.post(
        "/transactions/",
        json={
            "financial_account_id": financial_account_id,
            "category_id": category_id,
            "amount": amount,
            "currency": currency,
            "description": description,
            "occurred_at": occurred_at.isoformat(),
        },
    )
    assert response.status_code == 200
    return response.json()


def create_expense(
    client,
    *,
    financial_account_id: str,
    category_id: str,
    amount: str,
    currency: str = "COP",
    description: str,
    occurred_at: datetime,
):
    response = client.post(
        "/transactions/",
        json={
            "financial_account_id": financial_account_id,
            "category_id": category_id,
            "amount": amount,
            "currency": currency,
            "description": description,
            "occurred_at": occurred_at.isoformat(),
        },
    )
    assert response.status_code == 200
    return response.json()


def test_transfer_creates_atomic_legs_updates_ledger_and_stays_out_of_transactions_list(
    client,
):
    create_configured_user(client)
    income_category = create_category(client, name="Salary", direction="income")
    default_account = client.get("/financial-accounts/").json()[0]
    wallet_account = client.post(
        "/financial-accounts/",
        json={"name": "Wallet"},
    ).json()

    create_income(
        client,
        financial_account_id=default_account["id"],
        category_id=income_category["id"],
        amount="1000.00",
        description="Initial salary",
        occurred_at=datetime(2026, 3, 1, 12, tzinfo=timezone.utc),
    )

    created_transfer = client.post(
        "/transfers/",
        json={
            "source_financial_account_id": default_account["id"],
            "destination_financial_account_id": wallet_account["id"],
            "amount": "300.00",
            "currency": "COP",
            "description": "Move to wallet",
            "occurred_at": datetime(2026, 3, 2, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert created_transfer.status_code == 200

    transfer_data = created_transfer.json()
    assert transfer_data["transfer_group_id"]
    assert transfer_data["source_transaction"]["transaction_type"] == "transfer"
    assert transfer_data["source_transaction"]["balance_direction"] == "out"
    assert transfer_data["source_transaction"]["financial_account_id"] == default_account["id"]
    assert transfer_data["source_transaction"]["category_id"] is None
    assert (
        transfer_data["source_transaction"]["transfer_group_id"]
        == transfer_data["transfer_group_id"]
    )
    assert (
        transfer_data["source_transaction"]["counterparty_financial_account_id"]
        == wallet_account["id"]
    )
    assert transfer_data["destination_transaction"]["transaction_type"] == "transfer"
    assert transfer_data["destination_transaction"]["balance_direction"] == "in"
    assert (
        transfer_data["destination_transaction"]["financial_account_id"]
        == wallet_account["id"]
    )
    assert transfer_data["destination_transaction"]["category_id"] is None
    assert (
        transfer_data["destination_transaction"]["transfer_group_id"]
        == transfer_data["transfer_group_id"]
    )
    assert (
        transfer_data["destination_transaction"]["counterparty_financial_account_id"]
        == default_account["id"]
    )

    balances = client.get("/ledger/balances")
    assert balances.status_code == 200
    assert balances.json() == {
        "currency": "COP",
        "consolidated_balance": "1000.00",
        "skipped_transactions": 0,
        "accounts": [
            {
                "financial_account_id": default_account["id"],
                "financial_account_name": "Main account",
                "currency": "COP",
                "balance": "700.00",
                "balance_in_base_currency": "700.00",
            },
            {
                "financial_account_id": wallet_account["id"],
                "financial_account_name": "Wallet",
                "currency": "COP",
                "balance": "300.00",
                "balance_in_base_currency": "300.00",
            },
        ],
    }

    activity = client.get("/ledger/activity?limit=3")
    assert activity.status_code == 200
    activity_items = activity.json()["items"]
    assert len(activity_items) == 3
    transfer_items = [
        item
        for item in activity_items
        if item["transfer_group_id"] == transfer_data["transfer_group_id"]
    ]
    assert len(transfer_items) == 2
    assert {item["balance_direction"] for item in transfer_items} == {"in", "out"}
    assert {item["counterparty_financial_account_name"] for item in transfer_items} == {
        "Main account",
        "Wallet",
    }

    transactions = client.get("/transactions/")
    assert transactions.status_code == 200
    assert transactions.json()["total_count"] == 1
    assert len(transactions.json()["items"]) == 1
    assert transactions.json()["items"][0]["transaction_type"] == "income"

    hidden_transfer = client.get(
        f"/transactions/{transfer_data['source_transaction']['id']}"
    )
    assert hidden_transfer.status_code == 404


def test_delete_transfer_removes_both_legs_and_restores_balances(client):
    create_configured_user(client)
    income_category = create_category(client, name="Salary", direction="income")
    default_account = client.get("/financial-accounts/").json()[0]
    wallet_account = client.post(
        "/financial-accounts/",
        json={"name": "Wallet"},
    ).json()

    create_income(
        client,
        financial_account_id=default_account["id"],
        category_id=income_category["id"],
        amount="1000.00",
        description="Initial salary",
        occurred_at=datetime(2026, 3, 1, 12, tzinfo=timezone.utc),
    )
    created_transfer = client.post(
        "/transfers/",
        json={
            "source_financial_account_id": default_account["id"],
            "destination_financial_account_id": wallet_account["id"],
            "amount": "300.00",
            "currency": "COP",
            "description": "Move to wallet",
            "occurred_at": datetime(2026, 3, 2, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert created_transfer.status_code == 200

    deleted = client.delete(
        f"/transfers/{created_transfer.json()['transfer_group_id']}"
    )
    assert deleted.status_code == 204

    balances = client.get("/ledger/balances")
    assert balances.status_code == 200
    assert balances.json()["consolidated_balance"] == "1000.00"
    assert balances.json()["skipped_transactions"] == 0
    assert balances.json()["accounts"] == [
        {
            "financial_account_id": default_account["id"],
            "financial_account_name": "Main account",
            "currency": "COP",
            "balance": "1000.00",
            "balance_in_base_currency": "1000.00",
        },
        {
            "financial_account_id": wallet_account["id"],
            "financial_account_name": "Wallet",
            "currency": "COP",
            "balance": "0.00",
            "balance_in_base_currency": None,
        },
    ]

    activity = client.get("/ledger/activity?limit=5")
    assert activity.status_code == 200
    assert all(
        item["transfer_group_id"] != created_transfer.json()["transfer_group_id"]
        for item in activity.json()["items"]
    )

    missing = client.delete(f"/transfers/{created_transfer.json()['transfer_group_id']}")
    assert missing.status_code == 404


def test_adjustment_affects_ledger_but_not_monthly_analytics(client):
    create_configured_user(client)
    expense_category = create_category(client, name="Groceries", direction="expense")
    default_account = client.get("/financial-accounts/").json()[0]

    created_adjustment = client.post(
        "/adjustments/",
        json={
            "financial_account_id": default_account["id"],
            "balance_direction": "in",
            "amount": "500.00",
            "currency": "COP",
            "description": "Opening balance",
            "occurred_at": datetime(2026, 3, 1, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert created_adjustment.status_code == 200
    assert created_adjustment.json()["transaction_type"] == "adjustment"
    assert created_adjustment.json()["balance_direction"] == "in"
    assert created_adjustment.json()["category_id"] is None

    create_expense(
        client,
        financial_account_id=default_account["id"],
        category_id=expense_category["id"],
        amount="200.00",
        description="Groceries",
        occurred_at=datetime(2026, 3, 2, 12, tzinfo=timezone.utc),
    )

    balances = client.get("/ledger/balances")
    assert balances.status_code == 200
    assert balances.json()["consolidated_balance"] == "300.00"
    assert balances.json()["accounts"][0]["balance"] == "300.00"
    assert balances.json()["accounts"][0]["balance_in_base_currency"] == "300.00"

    adjustments = client.get("/ledger/adjustments?limit=5")
    assert adjustments.status_code == 200
    assert adjustments.json()["limit"] == 5
    assert [item["id"] for item in adjustments.json()["items"]] == [
        created_adjustment.json()["id"]
    ]
    assert all(
        item["transaction_type"] == "adjustment"
        for item in adjustments.json()["items"]
    )

    monthly_balance = client.get("/balance/monthly?year=2026&month=3")
    assert monthly_balance.status_code == 200
    assert monthly_balance.json()["current"] == {
        "month_start": "2026-03-01",
        "currency": "COP",
        "income": "0.00",
        "expense": "200.00",
        "balance": "-200.00",
        "skipped_transactions": 0,
    }

    analytics_summary = client.get("/analytics/summary?year=2026&month=3")
    assert analytics_summary.status_code == 200
    assert [item["description"] for item in analytics_summary.json()["recent_transactions"]] == [
        "Groceries"
    ]

    transactions = client.get("/transactions/")
    assert transactions.status_code == 200
    assert transactions.json()["total_count"] == 1
    assert transactions.json()["items"][0]["description"] == "Groceries"

    deleted_adjustment = client.delete(
        f"/adjustments/{created_adjustment.json()['id']}"
    )
    assert deleted_adjustment.status_code == 204

    balances_after_delete = client.get("/ledger/balances")
    assert balances_after_delete.status_code == 200
    assert balances_after_delete.json()["consolidated_balance"] == "-200.00"
    assert balances_after_delete.json()["accounts"][0]["balance"] == "-200.00"
    assert balances_after_delete.json()["accounts"][0]["balance_in_base_currency"] == (
        "-200.00"
    )


def test_transfer_rejects_same_account(client):
    create_configured_user(client)
    default_account = client.get("/financial-accounts/").json()[0]

    response = client.post(
        "/transfers/",
        json={
            "source_financial_account_id": default_account["id"],
            "destination_financial_account_id": default_account["id"],
            "amount": "300.00",
            "currency": "COP",
            "description": "Invalid move",
            "occurred_at": datetime(2026, 3, 2, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Source and destination accounts must be different"


def test_transfer_rejects_accounts_with_different_currencies(client):
    create_configured_user(client)
    default_account = client.get("/financial-accounts/").json()[0]
    usd_account = client.post(
        "/financial-accounts/",
        json={"name": "USD wallet", "currency": "USD"},
    ).json()

    response = client.post(
        "/transfers/",
        json={
            "source_financial_account_id": default_account["id"],
            "destination_financial_account_id": usd_account["id"],
            "amount": "300.00",
            "currency": "COP",
            "description": "Invalid move",
            "occurred_at": datetime(2026, 3, 2, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "Transfers between accounts with different currencies are not supported yet"
    )


def test_ledger_balances_convert_foreign_currency_accounts_to_user_base_currency(
    client,
    db_session,
):
    create_configured_user(client)
    usd_account = client.post(
        "/financial-accounts/",
        json={"name": "USD wallet", "currency": "USD"},
    ).json()

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

    created_adjustment = client.post(
        "/adjustments/",
        json={
            "financial_account_id": usd_account["id"],
            "balance_direction": "in",
            "amount": "100.00",
            "currency": "USD",
            "description": "Opening balance",
            "occurred_at": datetime(2026, 3, 1, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert created_adjustment.status_code == 200

    balances = client.get("/ledger/balances")
    assert balances.status_code == 200
    assert balances.json() == {
        "currency": "COP",
        "consolidated_balance": "400000.00",
        "skipped_transactions": 0,
        "accounts": [
            {
                "financial_account_id": balances.json()["accounts"][0][
                    "financial_account_id"
                ],
                "financial_account_name": "Main account",
                "currency": "COP",
                "balance": "0.00",
                "balance_in_base_currency": None,
            },
            {
                "financial_account_id": usd_account["id"],
                "financial_account_name": "USD wallet",
                "currency": "USD",
                "balance": "100.00",
                "balance_in_base_currency": "400000.00",
            },
        ],
    }


def test_ledger_balances_report_skipped_transactions_when_fx_is_missing(client):
    create_configured_user(client)
    usd_account = client.post(
        "/financial-accounts/",
        json={"name": "USD wallet", "currency": "USD"},
    ).json()

    created_adjustment = client.post(
        "/adjustments/",
        json={
            "financial_account_id": usd_account["id"],
            "balance_direction": "in",
            "amount": "100.00",
            "currency": "USD",
            "description": "Opening balance",
            "occurred_at": datetime(2026, 3, 1, 12, tzinfo=timezone.utc).isoformat(),
        },
    )
    assert created_adjustment.status_code == 200

    balances = client.get("/ledger/balances")
    assert balances.status_code == 200
    assert balances.json()["currency"] == "COP"
    assert balances.json()["consolidated_balance"] == "0.00"
    assert balances.json()["skipped_transactions"] == 1
    usd_balance = next(
        item
        for item in balances.json()["accounts"]
        if item["financial_account_id"] == usd_account["id"]
    )
    assert usd_balance["balance"] == "100.00"
    assert usd_balance["balance_in_base_currency"] is None
