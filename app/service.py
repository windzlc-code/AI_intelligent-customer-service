from __future__ import annotations

import sqlite3
from typing import Any

from .db import db, now_ts
from .defaults import (
    DEFAULT_END_HANDOFF_BUTTON_TEXT,
    DEFAULT_HANDOFF_BUTTON_TEXT,
    DEFAULT_HANDOFF_CLOSE_TEXT,
    DEFAULT_HANDOFF_OPEN_TEXT,
    DEFAULT_UNAUTHORIZED_TEXT,
    DEFAULT_WELCOME_TEXT,
)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def validate_telegram_id(telegram_id: int) -> int:
    value = int(telegram_id)
    if value <= 0:
        raise ValueError("Telegram ID must be a positive integer")
    return value


class CustomerServiceStore:
    def get_bot_config(self) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute("SELECT * FROM bot_config WHERE id = 1").fetchone()
            assert row is not None
            config = dict(row)
        if _looks_corrupt(config.get("welcome_text")):
            config["welcome_text"] = DEFAULT_WELCOME_TEXT
        if _looks_corrupt(config.get("handoff_button_text")):
            config["handoff_button_text"] = DEFAULT_HANDOFF_BUTTON_TEXT
        if _looks_corrupt(config.get("end_handoff_button_text")):
            config["end_handoff_button_text"] = DEFAULT_END_HANDOFF_BUTTON_TEXT
        if str(config.get("end_handoff_button_text") or "").strip() == "结束人工服务":
            config["end_handoff_button_text"] = DEFAULT_END_HANDOFF_BUTTON_TEXT
        if _looks_corrupt(config.get("handoff_open_text")):
            config["handoff_open_text"] = DEFAULT_HANDOFF_OPEN_TEXT
        if _looks_corrupt(config.get("handoff_close_text")):
            config["handoff_close_text"] = DEFAULT_HANDOFF_CLOSE_TEXT
        if _looks_corrupt(config.get("unauthorized_text")):
            config["unauthorized_text"] = DEFAULT_UNAUTHORIZED_TEXT
        return config

    def update_bot_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "bot_token",
            "webhook_secret",
            "public_webhook_url",
            "welcome_text",
            "handoff_button_text",
            "end_handoff_button_text",
            "handoff_open_text",
            "handoff_close_text",
            "unauthorized_text",
        }
        values = {key: str(payload.get(key) or "") for key in allowed if key in payload}
        if values.get("bot_token") == "":
            values.pop("bot_token", None)
        if not values:
            return self.get_bot_config()
        assignments = ", ".join([f"{key} = ?" for key in values] + ["updated_at = ?"])
        args = list(values.values()) + [now_ts()]
        with db() as conn:
            conn.execute(f"UPDATE bot_config SET {assignments} WHERE id = 1", args)
        return self.get_bot_config()

    def list_telegram_users(self) -> list[dict[str, Any]]:
        with db() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM telegram_users ORDER BY updated_at DESC")]

    def upsert_telegram_user(self, telegram_id: int, remark_name: str = "", is_enabled: bool = True) -> dict[str, Any]:
        telegram_id = validate_telegram_id(telegram_id)
        ts = now_ts()
        with db() as conn:
            conn.execute(
                """
                INSERT INTO telegram_users(telegram_id, remark_name, is_enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                  remark_name=excluded.remark_name,
                  is_enabled=excluded.is_enabled,
                  updated_at=excluded.updated_at
                """,
                (telegram_id, str(remark_name or ""), 1 if is_enabled else 0, ts, ts),
            )
            row = conn.execute("SELECT * FROM telegram_users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return dict(row)

    def delete_telegram_user(self, telegram_id: int) -> None:
        telegram_id = validate_telegram_id(telegram_id)
        with db() as conn:
            conn.execute("DELETE FROM telegram_users WHERE telegram_id = ?", (telegram_id,))

    def list_telegram_admins(self) -> list[dict[str, Any]]:
        with db() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM telegram_admins ORDER BY updated_at DESC")]

    def upsert_telegram_admin(self, telegram_id: int, display_name: str = "", is_enabled: bool = True) -> dict[str, Any]:
        telegram_id = validate_telegram_id(telegram_id)
        ts = now_ts()
        with db() as conn:
            conn.execute(
                """
                INSERT INTO telegram_admins(telegram_id, display_name, is_enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                  display_name=excluded.display_name,
                  is_enabled=excluded.is_enabled,
                  updated_at=excluded.updated_at
                """,
                (telegram_id, str(display_name or ""), 1 if is_enabled else 0, ts, ts),
            )
            row = conn.execute("SELECT * FROM telegram_admins WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return dict(row)

    def delete_telegram_admin(self, telegram_id: int) -> None:
        telegram_id = validate_telegram_id(telegram_id)
        with db() as conn:
            conn.execute("DELETE FROM telegram_admins WHERE telegram_id = ?", (telegram_id,))

    def is_authorized_user(self, telegram_id: int) -> bool:
        with db() as conn:
            row = conn.execute(
                "SELECT is_enabled FROM telegram_users WHERE telegram_id = ?",
                (int(telegram_id),),
            ).fetchone()
            return bool(row and int(row["is_enabled"]) == 1)

    def is_authorized_admin(self, telegram_id: int) -> bool:
        with db() as conn:
            row = conn.execute(
                "SELECT is_enabled FROM telegram_admins WHERE telegram_id = ?",
                (int(telegram_id),),
            ).fetchone()
            return bool(row and int(row["is_enabled"]) == 1)

    def update_user_seen(self, telegram_id: int, latest_name: str, username: str = "") -> None:
        ts = now_ts()
        with db() as conn:
            conn.execute(
                """
                UPDATE telegram_users
                SET latest_name = ?, username = ?, updated_at = ?
                WHERE telegram_id = ?
                """,
                (latest_name, username, ts, int(telegram_id)),
            )

    def update_admin_seen(self, telegram_id: int, latest_name: str, username: str = "") -> None:
        ts = now_ts()
        with db() as conn:
            conn.execute(
                """
                UPDATE telegram_admins
                SET latest_name = ?, username = ?, updated_at = ?
                WHERE telegram_id = ?
                """,
                (latest_name, username, ts, int(telegram_id)),
            )

    def get_display_name_for_user(self, telegram_id: int, telegram_name: str = "") -> str:
        with db() as conn:
            row = conn.execute("SELECT remark_name, latest_name FROM telegram_users WHERE telegram_id = ?", (int(telegram_id),)).fetchone()
        if telegram_name.strip():
            return telegram_name.strip()
        if row and str(row["latest_name"] or "").strip():
            return str(row["latest_name"]).strip()
        if row and str(row["remark_name"] or "").strip():
            return str(row["remark_name"]).strip()
        return str(telegram_id)

    def list_preset_replies(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        where = "WHERE is_enabled = 1" if enabled_only else ""
        with db() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    f"SELECT * FROM preset_replies {where} ORDER BY sort_order ASC, id ASC"
                )
            ]

    def replace_preset_replies(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ts = now_ts()
        with db() as conn:
            conn.execute("DELETE FROM preset_replies")
            for index, item in enumerate(items):
                button_text = str(item.get("button_text") or "").strip()
                reply_text = str(item.get("reply_text") or "").strip()
                if not button_text:
                    continue
                conn.execute(
                    """
                    INSERT INTO preset_replies(button_text, reply_text, sort_order, is_enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        button_text,
                        reply_text,
                        int(item.get("sort_order") or (index + 1) * 10),
                        1 if item.get("is_enabled", True) else 0,
                        ts,
                        ts,
                    ),
                )
        return self.list_preset_replies()

    def get_or_create_conversation(self, telegram_user_id: int) -> dict[str, Any]:
        ts = now_ts()
        with db() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO conversations(telegram_user_id, status, created_at, updated_at)
                VALUES (?, 'bot', ?, ?)
                """,
                (int(telegram_user_id), ts, ts),
            )
            row = conn.execute("SELECT * FROM conversations WHERE telegram_user_id = ?", (int(telegram_user_id),)).fetchone()
            return dict(row)

    def set_conversation_status(self, telegram_user_id: int, status: str) -> dict[str, Any]:
        ts = now_ts()
        with db() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO conversations(telegram_user_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (int(telegram_user_id), str(status), ts, ts),
            )
            conn.execute(
                """
                UPDATE conversations
                SET status = ?, claimed_by_admin_id = NULL, updated_at = ?
                WHERE telegram_user_id = ?
                """,
                (str(status), ts, int(telegram_user_id)),
            )
            row = conn.execute("SELECT * FROM conversations WHERE telegram_user_id = ?", (int(telegram_user_id),)).fetchone()
            return dict(row)

    def open_handoff(self, telegram_user_id: int) -> dict[str, Any]:
        ts = now_ts()
        with db() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET status = 'handoff_open', claimed_by_admin_id = NULL, opened_at = COALESCE(opened_at, ?),
                    closed_at = NULL, updated_at = ?
                WHERE telegram_user_id = ?
                """,
                (ts, ts, int(telegram_user_id)),
            )
            if conn.total_changes == 0:
                conn.execute(
                    """
                    INSERT INTO conversations(telegram_user_id, status, opened_at, created_at, updated_at)
                    VALUES (?, 'handoff_open', ?, ?, ?)
                    """,
                    (int(telegram_user_id), ts, ts, ts),
                )
            row = conn.execute("SELECT * FROM conversations WHERE telegram_user_id = ?", (int(telegram_user_id),)).fetchone()
            return dict(row)

    def close_handoff(self, telegram_user_id: int) -> dict[str, Any]:
        ts = now_ts()
        with db() as conn:
            conn.execute(
                """
                UPDATE conversations
                SET status = 'bot', claimed_by_admin_id = NULL, closed_at = ?, updated_at = ?
                WHERE telegram_user_id = ?
                """,
                (ts, ts, int(telegram_user_id)),
            )
            row = conn.execute("SELECT * FROM conversations WHERE telegram_user_id = ?", (int(telegram_user_id),)).fetchone()
            return dict(row)

    def get_conversation(self, conversation_id: int) -> dict[str, Any] | None:
        with db() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (int(conversation_id),)).fetchone()
            return row_to_dict(row)

    def claim_conversation(self, conversation_id: int, admin_id: int) -> dict[str, Any]:
        ts = now_ts()
        with db() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (int(conversation_id),)).fetchone()
            if row is None:
                raise ValueError("Conversation not found")
            current = row["claimed_by_admin_id"]
            if current is not None and int(current) != int(admin_id):
                raise ValueError("Conversation already claimed by another admin")
            conn.execute(
                """
                UPDATE conversations
                SET status = 'handoff_claimed', claimed_by_admin_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(admin_id), ts, int(conversation_id)),
            )
            conn.execute(
                """
                INSERT INTO admin_sessions(admin_telegram_id, current_conversation_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(admin_telegram_id) DO UPDATE SET
                  current_conversation_id=excluded.current_conversation_id,
                  updated_at=excluded.updated_at
                """,
                (int(admin_id), int(conversation_id), ts),
            )
            updated = conn.execute("SELECT * FROM conversations WHERE id = ?", (int(conversation_id),)).fetchone()
            return dict(updated)

    def release_conversation(self, conversation_id: int, admin_id: int) -> dict[str, Any]:
        ts = now_ts()
        with db() as conn:
            row = conn.execute("SELECT * FROM conversations WHERE id = ?", (int(conversation_id),)).fetchone()
            if row is None:
                raise ValueError("Conversation not found")
            if row["claimed_by_admin_id"] is not None and int(row["claimed_by_admin_id"]) != int(admin_id):
                raise ValueError("Conversation claimed by another admin")
            conn.execute(
                "UPDATE conversations SET status = 'handoff_open', claimed_by_admin_id = NULL, updated_at = ? WHERE id = ?",
                (ts, int(conversation_id)),
            )
            conn.execute(
                "UPDATE admin_sessions SET current_conversation_id = NULL, updated_at = ? WHERE admin_telegram_id = ?",
                (ts, int(admin_id)),
            )
            updated = conn.execute("SELECT * FROM conversations WHERE id = ?", (int(conversation_id),)).fetchone()
            return dict(updated)

    def get_admin_current_conversation(self, admin_id: int) -> dict[str, Any] | None:
        with db() as conn:
            row = conn.execute(
                """
                SELECT c.* FROM admin_sessions s
                JOIN conversations c ON c.id = s.current_conversation_id
                WHERE s.admin_telegram_id = ?
                """,
                (int(admin_id),),
            ).fetchone()
            return row_to_dict(row)

    def list_active_conversations(self) -> list[dict[str, Any]]:
        with db() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT c.*, u.remark_name, u.latest_name, u.username
                    FROM conversations c
                    JOIN telegram_users u ON u.telegram_id = c.telegram_user_id
                    WHERE c.status IN ('handoff_open', 'handoff_claimed')
                    ORDER BY c.updated_at DESC
                    """
                )
            ]

    def list_all_conversations(self) -> list[dict[str, Any]]:
        with db() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT c.*, u.remark_name, u.latest_name, u.username
                    FROM conversations c
                    JOIN telegram_users u ON u.telegram_id = c.telegram_user_id
                    ORDER BY c.updated_at DESC
                    """
                )
            ]

    def add_message(
        self,
        conversation_id: int,
        direction: str,
        sender_telegram_id: int | None,
        sender_display_name: str,
        message_type: str,
        text: str = "",
        telegram_message_id: int | None = None,
        telegram_file_id: str = "",
        forwarded_to_admins: bool = False,
    ) -> dict[str, Any]:
        ts = now_ts()
        with db() as conn:
            cur = conn.execute(
                """
                INSERT INTO messages(
                  conversation_id, direction, sender_telegram_id, sender_display_name, message_type,
                  text, telegram_message_id, telegram_file_id, forwarded_to_admins, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(conversation_id),
                    direction,
                    sender_telegram_id,
                    sender_display_name,
                    message_type,
                    text,
                    telegram_message_id,
                    telegram_file_id,
                    1 if forwarded_to_admins else 0,
                    ts,
                ),
            )
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, int(conversation_id)))
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
            return dict(row)

    def list_messages(self, conversation_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with db() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE conversation_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (int(conversation_id), int(limit)),
                )
            ][::-1]

    def enabled_admin_ids(self) -> list[int]:
        with db() as conn:
            return [
                int(row["telegram_id"])
                for row in conn.execute("SELECT telegram_id FROM telegram_admins WHERE is_enabled = 1")
            ]


def _looks_corrupt(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if any(marker in text for marker in ("鎴", "鐢", "锛", "绠", "€", "�")):
        return True
    question_count = text.count("?")
    return question_count >= 4 and question_count >= max(4, len(text) // 3)
