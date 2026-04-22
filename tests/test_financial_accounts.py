from datetime import datetime, timezone


def create_configured_user(client):
    return client.post(
        "/users/",
        json={
            "name": "Test User",
            "email": "test@example.com",
            "base_currency": "COP",
            "timezone": "UTC",
        },
    )


def create_category(client, name: str = "Food", direction: str = "expense"):
    return client.post(
        "/categories/",
        json={"name": name, "direction": direction, "parent_id": None},
    )


def test_financial_account_crud_and_default_switching(client):
    created = create_configured_user(client)
    assert created.status_code == 200

    listed = client.get("/financial-accounts/")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    main_account = listed.json()[0]
    assert main_account["name"] == "Main account"
    assert main_account["currency"] == "COP"
    assert main_account["is_default"] is True

    fetched = client.get(f"/financial-accounts/{main_account['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == main_account["id"]

    created_wallet = client.post(
        "/financial-accounts/",
        json={
            "name": "  Daily   wallet  ",
            "currency": "USD",
            "is_default": False,
        },
    )
    assert created_wallet.status_code == 200
    assert created_wallet.json()["name"] == "Daily wallet"
    assert created_wallet.json()["currency"] == "USD"
    assert created_wallet.json()["is_default"] is False

    updated_wallet = client.put(
        f"/financial-accounts/{created_wallet.json()['id']}",
        json={"name": "Cash wallet", "is_default": True},
    )
    assert updated_wallet.status_code == 200
    assert updated_wallet.json()["name"] == "Cash wallet"
    assert updated_wallet.json()["is_default"] is True

    listed_after_update = client.get("/financial-accounts/")
    assert listed_after_update.status_code == 200
    assert [item["id"] for item in listed_after_update.json() if item["is_default"]] == [
        created_wallet.json()["id"]
    ]
    assert listed_after_update.json()[0]["id"] == created_wallet.json()["id"]

    refreshed_main = client.get(f"/financial-accounts/{main_account['id']}")
    assert refreshed_main.status_code == 200
    assert refreshed_main.json()["is_default"] is False


def test_financial_account_delete_rules_and_default_reassignment(client):
    created = create_configured_user(client)
    assert created.status_code == 200

    main_account = client.get("/financial-accounts/").json()[0]

    deleting_only_account = client.delete(f"/financial-accounts/{main_account['id']}")
    assert deleting_only_account.status_code == 409
    assert deleting_only_account.json()["detail"] == "At least one financial account is required"

    wallet_account = client.post(
        "/financial-accounts/",
        json={"name": "Wallet", "is_default": False},
    )
    assert wallet_account.status_code == 200

    category = create_category(client, name="Transport")
    assert category.status_code == 200

    created_transaction = client.post(
        "/transactions/",
        json={
            "category_id": category.json()["id"],
            "amount": "15.25",
            "currency": "COP",
            "description": "Taxi",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created_transaction.status_code == 200
    assert created_transaction.json()["financial_account_id"] == main_account["id"]

    deleting_account_with_transactions = client.delete(
        f"/financial-accounts/{main_account['id']}"
    )
    assert deleting_account_with_transactions.status_code == 409
    assert (
        deleting_account_with_transactions.json()["detail"]
        == "Financial account has 1 transaction"
    )

    savings_account = client.post(
        "/financial-accounts/",
        json={"name": "Savings", "is_default": False},
    )
    assert savings_account.status_code == 200

    set_wallet_default = client.put(
        f"/financial-accounts/{wallet_account.json()['id']}",
        json={"is_default": True},
    )
    assert set_wallet_default.status_code == 200
    assert set_wallet_default.json()["is_default"] is True

    deleted_wallet = client.delete(f"/financial-accounts/{wallet_account.json()['id']}")
    assert deleted_wallet.status_code == 204

    listed_after_delete = client.get("/financial-accounts/")
    assert listed_after_delete.status_code == 200
    assert {item["id"] for item in listed_after_delete.json()} == {
        main_account["id"],
        savings_account.json()["id"],
    }
    assert [item["id"] for item in listed_after_delete.json() if item["is_default"]] == [
        main_account["id"]
    ]


def test_list_transactions_can_filter_by_financial_account(client):
    created = create_configured_user(client)
    assert created.status_code == 200

    default_account = client.get("/financial-accounts/").json()[0]
    extra_account = client.post(
        "/financial-accounts/",
        json={"name": "Wallet", "is_default": False},
    )
    assert extra_account.status_code == 200

    category = create_category(client)
    assert category.status_code == 200

    default_transaction = client.post(
        "/transactions/",
        json={
            "category_id": category.json()["id"],
            "amount": "25.00",
            "currency": "COP",
            "description": "Lunch",
            "occurred_at": "2026-03-05T12:00:00Z",
        },
    )
    assert default_transaction.status_code == 200
    assert default_transaction.json()["financial_account_id"] == default_account["id"]

    wallet_transaction = client.post(
        "/transactions/",
        json={
            "category_id": category.json()["id"],
            "financial_account_id": extra_account.json()["id"],
            "amount": "18.00",
            "currency": "COP",
            "description": "Snacks",
            "occurred_at": "2026-03-06T12:00:00Z",
        },
    )
    assert wallet_transaction.status_code == 200

    filtered = client.get(
        f"/transactions/?financial_account_id={extra_account.json()['id']}"
    )
    assert filtered.status_code == 200
    assert filtered.json()["total_count"] == 1
    assert len(filtered.json()["items"]) == 1
    assert filtered.json()["items"][0]["id"] == wallet_transaction.json()["id"]
    assert filtered.json()["items"][0]["financial_account_id"] == extra_account.json()["id"]
    assert filtered.json()["summary"] == {
        "active_categories_count": 1,
        "skipped_transactions": 0,
        "income_totals": [],
        "expense_totals": [{"currency": "COP", "amount": "18.00"}],
        "balance_totals": [{"currency": "COP", "amount": "-18.00"}],
    }


def test_financial_account_currency_cannot_change_after_transactions_exist(client):
    created = create_configured_user(client)
    assert created.status_code == 200

    main_account = client.get("/financial-accounts/").json()[0]
    category = create_category(client, name="Utilities")
    assert category.status_code == 200

    created_transaction = client.post(
        "/transactions/",
        json={
            "category_id": category.json()["id"],
            "financial_account_id": main_account["id"],
            "amount": "15.25",
            "currency": "COP",
            "description": "Water",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created_transaction.status_code == 200

    updated = client.put(
        f"/financial-accounts/{main_account['id']}",
        json={"currency": "USD"},
    )
    assert updated.status_code == 409
    assert (
        updated.json()["detail"]
        == "Financial account currency cannot change after transactions exist"
    )
