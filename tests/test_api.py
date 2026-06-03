from __future__ import annotations

from fastapi.testclient import TestClient


def make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin123")
    from app.main import create_app

    return TestClient(create_app())


def login(client: TestClient):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    assert response.status_code == 200


def test_login_and_bot_config(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    assert client.get("/api/me").status_code == 401
    login(client)

    response = client.put(
        "/api/admin/bot-config",
        json={
            "bot_token": "123456:test-token",
        },
    )
    assert response.status_code == 200

    response = client.get("/api/admin/bot-config")
    assert response.status_code == 200
    assert response.json()["bot_token_masked"].startswith("1234")
    assert response.json()["bot_token"] == ""
    original_secret = response.json()["webhook_secret"]
    original_url = response.json()["public_webhook_url"]
    assert original_url.endswith(f"/telegram/webhook/{original_secret}")

    response = client.put(
        "/api/admin/bot-config",
        json={
            "bot_token": "123456:new-token",
            "webhook_secret": "secret-2",
            "public_webhook_url": "https://example.com/telegram/webhook/secret-2",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["webhook_secret"] == original_secret
    assert data["public_webhook_url"] == ""

    response = client.get("/api/admin/bot-config")
    assert response.json()["bot_token_masked"].startswith("1234")
    assert response.json()["webhook_secret"] == original_secret
    assert response.json()["public_webhook_url"] == original_url


def test_user_admin_and_reply_crud(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    login(client)

    response = client.post("/api/admin/users", json={"telegram_id": 1001, "remark_name": "客户A", "is_enabled": True})
    assert response.status_code == 200
    assert response.json()["telegram_id"] == 1001

    response = client.post("/api/admin/admins", json={"telegram_id": 9001, "display_name": "客服A", "is_enabled": True})
    assert response.status_code == 200
    assert response.json()["telegram_id"] == 9001

    response = client.put(
        "/api/admin/preset-replies",
        json={"items": [{"button_text": "价格", "reply_text": "价格说明", "sort_order": 1, "is_enabled": True}]},
    )
    assert response.status_code == 200
    assert response.json()[0]["button_text"] == "价格"

    assert client.get("/api/admin/users").json()[0]["remark_name"] == "客户A"
    assert client.get("/api/admin/admins").json()[0]["display_name"] == "客服A"
    assert client.get("/api/admin/preset-replies").json()[0]["reply_text"] == "价格说明"


def test_rejects_empty_or_zero_telegram_ids(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    login(client)

    response = client.post("/api/admin/users", json={"telegram_id": 0, "remark_name": "", "is_enabled": True})
    assert response.status_code == 422

    response = client.post("/api/admin/admins", json={"telegram_id": 0, "display_name": "", "is_enabled": True})
    assert response.status_code == 422
