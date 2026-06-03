from __future__ import annotations

from fastapi.testclient import TestClient


def test_webhook_secret_and_dispatch(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "webhook.db"))
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin123")

    import app.main as main_module

    client = TestClient(main_module.create_app())
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin123"}).status_code == 200
    client.put(
        "/api/admin/bot-config",
        json={
            "bot_token": "123456:test-token",
        },
    )
    secret = client.get("/api/admin/bot-config").json()["webhook_secret"]

    calls = []

    async def fake_feed_update(payload):
        calls.append(payload)

    monkeypatch.setattr(main_module.telegram_bot, "feed_update", fake_feed_update)

    assert client.post("/telegram/webhook/wrong", json={"update_id": 1}).status_code == 404
    response = client.post(f"/telegram/webhook/{secret}", json={"update_id": 2})
    assert response.status_code == 200
    assert calls == [{"update_id": 2}]
