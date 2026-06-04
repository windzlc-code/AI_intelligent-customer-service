from __future__ import annotations

from app.db import db, init_db, now_ts
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


def test_idle_handoff_timeout_closes_all_handoff_statuses(monkeypatch, tmp_path):
    store = setup_db(monkeypatch, tmp_path)
    store.upsert_telegram_user(1001, "客户A", True)
    store.upsert_telegram_admin(9001, "客服A", True)
    conversation = store.open_handoff(1001)
    store.set_conversation_status(1001, "handoff_payment_waiting")
    store.claim_conversation(conversation["id"], 9001)
    old_ts = now_ts() - 3600
    with db() as conn:
        conn.execute("UPDATE conversations SET status = 'handoff_payment_waiting', updated_at = ? WHERE id = ?", (old_ts, conversation["id"]))
        conn.execute(
            """
            INSERT INTO admin_sessions(admin_telegram_id, current_conversation_id, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(admin_telegram_id) DO UPDATE SET current_conversation_id = excluded.current_conversation_id
            """,
            (9001, conversation["id"], old_ts),
        )

    closed = store.close_idle_handoffs(30 * 60)

    assert [item["id"] for item in closed] == [conversation["id"]]
    updated = store.get_conversation(conversation["id"])
    assert updated["status"] == "bot"
    assert updated["claimed_by_admin_id"] is None
    assert store.get_admin_current_conversation(9001) is None


def test_delete_old_conversations_keeps_active_handoffs(monkeypatch, tmp_path):
    store = setup_db(monkeypatch, tmp_path)
    store.upsert_telegram_user(1001, "客户A", True)
    store.upsert_telegram_user(1002, "客户B", True)
    store.upsert_telegram_user(1003, "客户C", True)
    old_closed = store.get_or_create_conversation(1001)
    old_active = store.open_handoff(1002)
    recent_closed = store.get_or_create_conversation(1003)
    old_ts = now_ts() - 40 * 24 * 3600
    recent_ts = now_ts()
    with db() as conn:
        conn.execute("UPDATE conversations SET status = 'bot', updated_at = ? WHERE id = ?", (old_ts, old_closed["id"]))
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (old_ts, old_active["id"]))
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (recent_ts, recent_closed["id"]))

    deleted = store.delete_old_conversations(30)

    assert deleted == 1
    assert store.get_conversation(old_closed["id"]) is None
    assert store.get_conversation(old_active["id"]) is not None
    assert store.get_conversation(recent_closed["id"]) is not None


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
