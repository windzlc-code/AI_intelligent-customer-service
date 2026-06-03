from __future__ import annotations

from app.db import init_db
from app.service import CustomerServiceStore


def setup_db(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "app.db"))
    init_db()
    return CustomerServiceStore()


def test_user_and_admin_authorization(monkeypatch, tmp_path):
    store = setup_db(monkeypatch, tmp_path)

    assert not store.is_authorized_user(1001)
    assert not store.is_authorized_admin(9001)

    store.upsert_telegram_user(1001, "客户A", True)
    store.upsert_telegram_admin(9001, "客服A", True)

    assert store.is_authorized_user(1001)
    assert store.is_authorized_admin(9001)

    for fn in (store.upsert_telegram_user, store.upsert_telegram_admin):
        try:
            fn(0)
        except ValueError as exc:
            assert "positive integer" in str(exc)
        else:
            raise AssertionError("Telegram ID 0 should be rejected")


def test_handoff_state_and_claim_flow(monkeypatch, tmp_path):
    store = setup_db(monkeypatch, tmp_path)
    store.upsert_telegram_user(1001, "客户A", True)
    store.upsert_telegram_admin(9001, "客服A", True)
    store.upsert_telegram_admin(9002, "客服B", True)

    conversation = store.get_or_create_conversation(1001)
    assert conversation["status"] == "bot"

    opened = store.open_handoff(1001)
    assert opened["status"] == "handoff_open"

    claimed = store.claim_conversation(opened["id"], 9001)
    assert claimed["status"] == "handoff_claimed"
    assert claimed["claimed_by_admin_id"] == 9001

    try:
        store.claim_conversation(opened["id"], 9002)
    except ValueError as exc:
        assert "claimed" in str(exc)
    else:
        raise AssertionError("second admin should not be able to claim the conversation")

    closed = store.close_handoff(1001)
    assert closed["status"] == "bot"
    assert closed["claimed_by_admin_id"] is None


def test_message_records_user_name_and_file_id(monkeypatch, tmp_path):
    store = setup_db(monkeypatch, tmp_path)
    store.upsert_telegram_user(1001, "后台备注", True)
    store.update_user_seen(1001, "Telegram 昵称", "tg_user")
    conversation = store.open_handoff(1001)

    display_name = store.get_display_name_for_user(1001, "Telegram 昵称")
    message = store.add_message(
        conversation["id"],
        "user",
        1001,
        display_name,
        "photo",
        "图片说明",
        telegram_message_id=55,
        telegram_file_id="photo-file-id",
        forwarded_to_admins=True,
    )

    assert message["sender_display_name"] == "Telegram 昵称"
    assert message["message_type"] == "photo"
    assert message["telegram_file_id"] == "photo-file-id"
    assert message["forwarded_to_admins"] == 1
    assert store.list_messages(conversation["id"])[0]["text"] == "图片说明"


def test_preset_replies_replace(monkeypatch, tmp_path):
    store = setup_db(monkeypatch, tmp_path)
    replies = store.replace_preset_replies(
        [
            {"button_text": "价格", "reply_text": "价格说明", "sort_order": 1, "is_enabled": True},
            {"button_text": "停用", "reply_text": "不会出现在菜单", "sort_order": 2, "is_enabled": False},
        ]
    )

    assert [item["button_text"] for item in replies] == ["价格", "停用"]
    assert [item["button_text"] for item in store.list_preset_replies(enabled_only=True)] == ["价格"]
