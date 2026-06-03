from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def get_db_path() -> str:
    raw = str(os.getenv("APP_DB_PATH") or "").strip()
    if raw:
        return str(Path(raw).expanduser().resolve())
    return str((Path(__file__).resolve().parent.parent / "data" / "app.db").resolve())


def now_ts() -> int:
    return int(time.time())


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    Path(get_db_path()).parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    Path(get_db_path()).parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              username TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              role TEXT NOT NULL DEFAULT 'admin',
              is_disabled INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL,
              expires_at INTEGER NOT NULL,
              created_at INTEGER NOT NULL,
              FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS bot_config (
              id INTEGER PRIMARY KEY CHECK (id = 1),
              bot_token TEXT NOT NULL DEFAULT '',
              webhook_secret TEXT NOT NULL DEFAULT '',
              public_webhook_url TEXT NOT NULL DEFAULT '',
              welcome_text TEXT NOT NULL DEFAULT '您好，请选择需要咨询的问题。',
              handoff_button_text TEXT NOT NULL DEFAULT '人工客服',
              end_handoff_button_text TEXT NOT NULL DEFAULT '结束人工服务',
              handoff_open_text TEXT NOT NULL DEFAULT '已为您转接人工客服，请直接发送您的问题。',
              handoff_close_text TEXT NOT NULL DEFAULT '人工服务已结束，您可以继续使用自助菜单。',
              unauthorized_text TEXT NOT NULL DEFAULT '当前 Telegram ID 未授权，请联系管理员添加。',
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS telegram_users (
              telegram_id INTEGER PRIMARY KEY,
              remark_name TEXT NOT NULL DEFAULT '',
              latest_name TEXT NOT NULL DEFAULT '',
              username TEXT NOT NULL DEFAULT '',
              is_enabled INTEGER NOT NULL DEFAULT 1,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS telegram_admins (
              telegram_id INTEGER PRIMARY KEY,
              display_name TEXT NOT NULL DEFAULT '',
              latest_name TEXT NOT NULL DEFAULT '',
              username TEXT NOT NULL DEFAULT '',
              is_enabled INTEGER NOT NULL DEFAULT 1,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS preset_replies (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              button_text TEXT NOT NULL,
              reply_text TEXT NOT NULL,
              sort_order INTEGER NOT NULL DEFAULT 100,
              is_enabled INTEGER NOT NULL DEFAULT 1,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              telegram_user_id INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'bot',
              claimed_by_admin_id INTEGER,
              opened_at INTEGER,
              closed_at INTEGER,
              created_at INTEGER NOT NULL,
              updated_at INTEGER NOT NULL,
              FOREIGN KEY(telegram_user_id) REFERENCES telegram_users(telegram_id) ON DELETE CASCADE,
              FOREIGN KEY(claimed_by_admin_id) REFERENCES telegram_admins(telegram_id) ON DELETE SET NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_user
            ON conversations(telegram_user_id);

            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id INTEGER NOT NULL,
              direction TEXT NOT NULL,
              sender_telegram_id INTEGER,
              sender_display_name TEXT NOT NULL DEFAULT '',
              message_type TEXT NOT NULL,
              text TEXT NOT NULL DEFAULT '',
              telegram_message_id INTEGER,
              telegram_file_id TEXT NOT NULL DEFAULT '',
              forwarded_to_admins INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL,
              FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS admin_sessions (
              admin_telegram_id INTEGER PRIMARY KEY,
              current_conversation_id INTEGER,
              updated_at INTEGER NOT NULL,
              FOREIGN KEY(admin_telegram_id) REFERENCES telegram_admins(telegram_id) ON DELETE CASCADE,
              FOREIGN KEY(current_conversation_id) REFERENCES conversations(id) ON DELETE SET NULL
            );
            """
        )
        ts = now_ts()
        conn.execute(
            """
            INSERT OR IGNORE INTO bot_config(id, webhook_secret, updated_at)
            VALUES (1, lower(hex(randomblob(16))), ?)
            """,
            (ts,),
        )
        if conn.execute("SELECT COUNT(*) FROM preset_replies").fetchone()[0] == 0:
            conn.executemany(
                """
                INSERT INTO preset_replies(button_text, reply_text, sort_order, is_enabled, created_at, updated_at)
                VALUES (?, ?, ?, 1, ?, ?)
                """,
                [
                    ("常见问题", "这里是常见问题说明。", 10, ts, ts),
                    ("业务咨询", "请描述您的业务问题。", 20, ts, ts),
                ],
            )
