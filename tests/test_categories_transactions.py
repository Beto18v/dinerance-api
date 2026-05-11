import uuid
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


def test_create_category(client):
    # Ensure profile exists
    create_configured_user(client)

    resp = client.post(
        "/categories/",
        json={"name": "Salary", "direction": "income", "parent_id": None},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Salary"
    assert data["direction"] == "income"


def test_create_transaction_requires_existing_category(client):
    # Ensure profile exists
    create_configured_user(client)

    resp = client.post(
        "/transactions/",
        json={
            "category_id": str(uuid.uuid4()),
            "amount": "10.50",
            "currency": "COP",
            "description": "Coffee",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert resp.status_code == 404


def test_create_and_list_transactions(client):
    create_configured_user(client)

    cat = client.post(
        "/categories/",
        json={"name": "Food", "direction": "expense", "parent_id": None},
    ).json()

    tx = client.post(
        "/transactions/",
        json={
            "category_id": cat["id"],
            "amount": "25.00",
            "currency": "COP",
            "description": "Lunch",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert tx.status_code == 200
    default_account = client.get("/financial-accounts/")
    assert default_account.status_code == 200

    listed = client.get("/transactions/")
    assert listed.status_code == 200
    assert listed.json() == {
        "items": [
            {
                "id": listed.json()["items"][0]["id"],
                "category_id": cat["id"],
                "financial_account_id": default_account.json()[0]["id"],
                "financial_account_name": "Main account",
                "transaction_type": "expense",
                "transfer_group_id": None,
                "amount": "25.00",
                "currency": "COP",
                "fx_rate": "1.00000000",
                "fx_rate_date": listed.json()["items"][0]["fx_rate_date"],
                "fx_rate_source": "identity",
                "base_currency": "COP",
                "amount_in_base_currency": "25.00",
                "description": "Lunch",
                "occurred_at": listed.json()["items"][0]["occurred_at"],
                "created_at": listed.json()["items"][0]["created_at"],
            }
        ],
        "total_count": 1,
        "limit": 50,
        "offset": 0,
        "summary": {
            "active_categories_count": 1,
            "skipped_transactions": 0,
            "income_totals": [],
            "expense_totals": [{"currency": "COP", "amount": "25.00"}],
            "balance_totals": [{"currency": "COP", "amount": "-25.00"}],
        },
    }


def test_create_user_provisions_default_financial_account(client):
    created = create_configured_user(client)
    assert created.status_code == 200

    listed = client.get("/financial-accounts/")
    assert listed.status_code == 200
    assert listed.json() == [
        {
            "id": listed.json()[0]["id"],
            "name": "Main account",
            "currency": "COP",
            "is_default": True,
            "created_at": listed.json()[0]["created_at"],
        }
    ]


def test_default_financial_account_currency_follows_base_currency_before_transactions(
    client,
):
    created = create_configured_user(client)
    assert created.status_code == 200

    updated = client.put("/users/me", json={"base_currency": "USD"})
    assert updated.status_code == 200

    listed = client.get("/financial-accounts/")
    assert listed.status_code == 200
    assert listed.json()[0]["currency"] == "USD"


def test_list_transactions_supports_parent_category_date_filters_and_pagination(client):
    create_configured_user(client)

    parent = client.post(
        "/categories/",
        json={"name": "Home", "direction": "expense", "parent_id": None},
    )
    assert parent.status_code == 200

    child = client.post(
        "/categories/",
        json={
            "name": "Groceries",
            "direction": "expense",
            "parent_id": parent.json()["id"],
        },
    )
    assert child.status_code == 200

    unrelated = client.post(
        "/categories/",
        json={"name": "Transport", "direction": "expense", "parent_id": None},
    )
    assert unrelated.status_code == 200

    for payload in (
        {
            "category_id": parent.json()["id"],
            "amount": "80.00",
            "currency": "COP",
            "description": "Rent",
            "occurred_at": "2026-03-01T12:00:00Z",
        },
        {
            "category_id": child.json()["id"],
            "amount": "25.00",
            "currency": "COP",
            "description": "Market",
            "occurred_at": "2026-03-05T12:00:00Z",
        },
        {
            "category_id": child.json()["id"],
            "amount": "15.00",
            "currency": "COP",
            "description": "Old market",
            "occurred_at": "2026-02-27T12:00:00Z",
        },
        {
            "category_id": unrelated.json()["id"],
            "amount": "10.00",
            "currency": "COP",
            "description": "Bus",
            "occurred_at": "2026-03-06T12:00:00Z",
        },
    ):
        created = client.post("/transactions/", json=payload)
        assert created.status_code == 200

    listed = client.get(
        f"/transactions/?parent_category_id={parent.json()['id']}"
        "&start_date=2026-03-01T00:00:00Z"
        "&end_date=2026-03-31T23:59:59Z"
        "&limit=1"
        "&offset=1"
    )
    assert listed.status_code == 200
    assert listed.json() == {
        "items": [
            {
                "id": listed.json()["items"][0]["id"],
                "category_id": parent.json()["id"],
                "financial_account_id": listed.json()["items"][0]["financial_account_id"],
                "financial_account_name": "Main account",
                "transaction_type": "expense",
                "transfer_group_id": None,
                "amount": "80.00",
                "currency": "COP",
                "fx_rate": "1.00000000",
                "fx_rate_date": listed.json()["items"][0]["fx_rate_date"],
                "fx_rate_source": "identity",
                "base_currency": "COP",
                "amount_in_base_currency": "80.00",
                "description": "Rent",
                "occurred_at": "2026-03-01T12:00:00Z",
                "created_at": listed.json()["items"][0]["created_at"],
            }
        ],
        "total_count": 2,
        "limit": 1,
        "offset": 1,
        "summary": {
            "active_categories_count": 2,
            "skipped_transactions": 0,
            "income_totals": [],
            "expense_totals": [{"currency": "COP", "amount": "105.00"}],
            "balance_totals": [{"currency": "COP", "amount": "-105.00"}],
        },
    }


def test_list_transactions_can_skip_total_count_and_summary_for_lightweight_pages(client):
    create_configured_user(client)

    category = client.post(
        "/categories/",
        json={"name": "Food", "direction": "expense", "parent_id": None},
    )
    assert category.status_code == 200

    created = client.post(
        "/transactions/",
        json={
            "category_id": category.json()["id"],
            "amount": "25.00",
            "currency": "COP",
            "description": "Lunch",
            "occurred_at": "2026-03-05T12:00:00Z",
        },
    )
    assert created.status_code == 200

    listed = client.get(
        "/transactions/?limit=1&offset=0"
        "&include_total_count=false"
        "&include_summary=false"
    )
    assert listed.status_code == 200
    assert listed.json() == {
        "items": [
            {
                "id": created.json()["id"],
                "category_id": category.json()["id"],
                "financial_account_id": created.json()["financial_account_id"],
                "financial_account_name": "Main account",
                "transaction_type": "expense",
                "transfer_group_id": None,
                "amount": "25.00",
                "currency": "COP",
                "fx_rate": "1.00000000",
                "fx_rate_date": created.json()["fx_rate_date"],
                "fx_rate_source": "identity",
                "base_currency": "COP",
                "amount_in_base_currency": "25.00",
                "description": "Lunch",
                "occurred_at": "2026-03-05T12:00:00Z",
                "created_at": created.json()["created_at"],
            }
        ],
        "total_count": None,
        "limit": 1,
        "offset": 0,
        "summary": None,
    }


def test_create_transaction_rejects_negative_amount(client):
    create_configured_user(client)

    category = client.post(
        "/categories/",
        json={"name": "Food", "direction": "expense", "parent_id": None},
    )
    assert category.status_code == 200

    created = client.post(
        "/transactions/",
        json={
            "category_id": category.json()["id"],
            "amount": "-25.00",
            "currency": "COP",
            "description": "Invalid",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 422
    assert created.json()["detail"] == "Transaction amount must be greater than zero"


def test_create_transaction_rejects_unsupported_transfer_type(client):
    create_configured_user(client)

    created = client.post(
        "/transactions/",
        json={
            "category_id": None,
            "transaction_type": "transfer",
            "amount": "25.00",
            "currency": "COP",
            "description": "Move money",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 409
    assert (
        created.json()["detail"]
        == "Only income and expense transaction types are supported right now"
    )


def test_create_transaction_normalizes_stale_income_expense_type_from_category(client):
    create_configured_user(client)

    income_category = client.post(
        "/categories/",
        json={"name": "Salary", "direction": "income", "parent_id": None},
    )
    assert income_category.status_code == 200

    created = client.post(
        "/transactions/",
        json={
            "category_id": income_category.json()["id"],
            "transaction_type": "expense",
            "amount": "2500.00",
            "currency": "COP",
            "description": "Payroll",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 200
    assert created.json()["transaction_type"] == "income"


def test_category_crud(client):
    create_configured_user(client)

    created = client.post(
        "/categories/",
        json={"name": "Groceries", "direction": "expense", "parent_id": None},
    )
    assert created.status_code == 200
    category_id = created.json()["id"]

    fetched = client.get(f"/categories/{category_id}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Groceries"

    updated = client.put(
        f"/categories/{category_id}",
        json={"name": "Supermarket"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Supermarket"

    deleted = client.delete(f"/categories/{category_id}")
    assert deleted.status_code == 204

    missing = client.get(f"/categories/{category_id}")
    assert missing.status_code == 404


def test_create_duplicate_category_returns_conflict(client):
    create_configured_user(client)

    created = client.post(
        "/categories/",
        json={"name": "Groceries", "direction": "expense", "parent_id": None},
    )
    assert created.status_code == 200

    duplicated = client.post(
        "/categories/",
        json={"name": " groceries ", "direction": "income", "parent_id": None},
    )
    assert duplicated.status_code == 409
    assert duplicated.json()["detail"] == "Category already exists"


def test_update_category_to_duplicate_name_returns_conflict(client):
    create_configured_user(client)

    first_category = client.post(
        "/categories/",
        json={"name": "Groceries", "direction": "expense", "parent_id": None},
    )
    assert first_category.status_code == 200

    second_category = client.post(
        "/categories/",
        json={"name": "Transport", "direction": "expense", "parent_id": None},
    )
    assert second_category.status_code == 200

    updated = client.put(
        f"/categories/{second_category.json()['id']}",
        json={"name": " groceries "},
    )
    assert updated.status_code == 409
    assert updated.json()["detail"] == "Category already exists"


def test_create_subcategory_under_subcategory_returns_conflict(client):
    create_configured_user(client)

    parent = client.post(
        "/categories/",
        json={"name": "Home", "direction": "expense", "parent_id": None},
    )
    assert parent.status_code == 200

    child = client.post(
        "/categories/",
        json={
            "name": "Groceries",
            "direction": "expense",
            "parent_id": parent.json()["id"],
        },
    )
    assert child.status_code == 200

    nested_child = client.post(
        "/categories/",
        json={
            "name": "Vegetables",
            "direction": "expense",
            "parent_id": child.json()["id"],
        },
    )
    assert nested_child.status_code == 409
    assert nested_child.json()["detail"] == "Parent category must be top-level"


def test_group_category_with_children_cannot_become_subcategory(client):
    create_configured_user(client)

    group = client.post(
        "/categories/",
        json={"name": "Home", "direction": "expense", "parent_id": None},
    )
    assert group.status_code == 200

    child = client.post(
        "/categories/",
        json={
            "name": "Groceries",
            "direction": "expense",
            "parent_id": group.json()["id"],
        },
    )
    assert child.status_code == 200

    other_parent = client.post(
        "/categories/",
        json={"name": "Fixed costs", "direction": "expense", "parent_id": None},
    )
    assert other_parent.status_code == 200

    updated = client.put(
        f"/categories/{group.json()['id']}",
        json={"parent_id": other_parent.json()["id"]},
    )
    assert updated.status_code == 409
    assert updated.json()["detail"] == "Category already acts as a group"


def test_create_subcategory_with_different_direction_returns_conflict(client):
    create_configured_user(client)

    income_group = client.post(
        "/categories/",
        json={"name": "Salary", "direction": "income", "parent_id": None},
    )
    assert income_group.status_code == 200

    expense_child = client.post(
        "/categories/",
        json={
            "name": "Groceries",
            "direction": "expense",
            "parent_id": income_group.json()["id"],
        },
    )
    assert expense_child.status_code == 409
    assert expense_child.json()["detail"] == "Parent category must have same direction"


def test_group_direction_cannot_change_while_it_has_subcategories(client):
    create_configured_user(client)

    group = client.post(
        "/categories/",
        json={"name": "Home", "direction": "expense", "parent_id": None},
    )
    assert group.status_code == 200

    child = client.post(
        "/categories/",
        json={
            "name": "Groceries",
            "direction": "expense",
            "parent_id": group.json()["id"],
        },
    )
    assert child.status_code == 200

    updated = client.put(
        f"/categories/{group.json()['id']}",
        json={"direction": "income"},
    )
    assert updated.status_code == 409
    assert (
        updated.json()["detail"]
        == "Group direction cannot change while it has subcategories"
    )


def test_transaction_crud(client):
    create_configured_user(client)

    cat = client.post(
        "/categories/",
        json={"name": "Transport", "direction": "expense", "parent_id": None},
    ).json()

    created = client.post(
        "/transactions/",
        json={
            "category_id": cat["id"],
            "amount": "15.25",
            "currency": "COP",
            "description": "Taxi",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 200
    transaction_id = created.json()["id"]

    fetched = client.get(f"/transactions/{transaction_id}")
    assert fetched.status_code == 200
    assert fetched.json()["description"] == "Taxi"

    updated = client.put(
        f"/transactions/{transaction_id}",
        json={"description": "Cab"},
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Cab"

    deleted = client.delete(f"/transactions/{transaction_id}")
    assert deleted.status_code == 204

    missing = client.get(f"/transactions/{transaction_id}")
    assert missing.status_code == 404


def test_update_transaction_rejects_zero_amount(client):
    create_configured_user(client)

    category = client.post(
        "/categories/",
        json={"name": "Transport", "direction": "expense", "parent_id": None},
    )
    assert category.status_code == 200

    created = client.post(
        "/transactions/",
        json={
            "category_id": category.json()["id"],
            "amount": "15.25",
            "currency": "COP",
            "description": "Taxi",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 200

    updated = client.put(
        f"/transactions/{created.json()['id']}",
        json={"amount": "0.00"},
    )
    assert updated.status_code == 422
    assert updated.json()["detail"] == "Transaction amount must be greater than zero"


def test_update_transaction_recalculates_type_when_category_direction_changes(client):
    create_configured_user(client)

    expense_category = client.post(
        "/categories/",
        json={"name": "Transport", "direction": "expense", "parent_id": None},
    )
    assert expense_category.status_code == 200

    income_category = client.post(
        "/categories/",
        json={"name": "Salary", "direction": "income", "parent_id": None},
    )
    assert income_category.status_code == 200

    created = client.post(
        "/transactions/",
        json={
            "category_id": expense_category.json()["id"],
            "amount": "15.25",
            "currency": "COP",
            "description": "Taxi",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 200
    assert created.json()["transaction_type"] == "expense"

    updated = client.put(
        f"/transactions/{created.json()['id']}",
        json={"category_id": income_category.json()["id"]},
    )
    assert updated.status_code == 200
    assert updated.json()["category_id"] == income_category.json()["id"]
    assert updated.json()["transaction_type"] == "income"


def test_update_transaction_normalizes_stale_income_expense_type_from_category(client):
    create_configured_user(client)

    expense_category = client.post(
        "/categories/",
        json={"name": "Transport", "direction": "expense", "parent_id": None},
    )
    assert expense_category.status_code == 200

    income_category = client.post(
        "/categories/",
        json={"name": "Salary", "direction": "income", "parent_id": None},
    )
    assert income_category.status_code == 200

    created = client.post(
        "/transactions/",
        json={
            "category_id": expense_category.json()["id"],
            "amount": "15.25",
            "currency": "COP",
            "description": "Taxi",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 200
    assert created.json()["transaction_type"] == "expense"

    updated = client.put(
        f"/transactions/{created.json()['id']}",
        json={
            "category_id": income_category.json()["id"],
            "transaction_type": "expense",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["category_id"] == income_category.json()["id"]
    assert updated.json()["transaction_type"] == "income"


def test_delete_category_with_transactions_returns_conflict(client):
    create_configured_user(client)

    category = client.post(
        "/categories/",
        json={"name": "Utilities", "direction": "expense", "parent_id": None},
    )
    assert category.status_code == 200

    created = client.post(
        "/transactions/",
        json={
            "category_id": category.json()["id"],
            "amount": "120000.00",
            "currency": "COP",
            "description": "Water",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert created.status_code == 200

    deleted = client.delete(f"/categories/{category.json()['id']}")
    assert deleted.status_code == 409
    assert deleted.json()["detail"] == "Category has transactions"


def test_delete_group_category_with_children_returns_conflict(client):
    create_configured_user(client)

    parent = client.post(
        "/categories/",
        json={"name": "Home", "direction": "expense", "parent_id": None},
    )
    assert parent.status_code == 200

    child = client.post(
        "/categories/",
        json={
            "name": "Groceries",
            "direction": "expense",
            "parent_id": parent.json()["id"],
        },
    )
    assert child.status_code == 200

    deleted = client.delete(f"/categories/{parent.json()['id']}")
    assert deleted.status_code == 409
    assert deleted.json()["detail"] == "Category has subcategories"
