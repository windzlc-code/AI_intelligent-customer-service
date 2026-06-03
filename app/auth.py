from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Any

from fastapi import Cookie, Depends, HTTPException

from .db import db, now_ts


SESSION_COOKIE = "session_token"


def _pbkdf2_hash(password: str, *, salt: bytes, iterations: int = 200_000) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, int(iterations))
    return base64.b64encode(digest).decode("ascii")


def hash_password(password: str) -> str:
    if len(str(password or "")) < 6:
        raise ValueError("Password must be at least 6 characters.")
    salt = secrets.token_bytes(16)
    iterations = 200_000
    digest = _pbkdf2_hash(str(password), salt=salt, iterations=iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode('ascii')}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations_text, salt_b64, digest = str(stored or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        computed = _pbkdf2_hash(
            str(password or ""),
            salt=base64.b64decode(salt_b64.encode("ascii")),
            iterations=int(iterations_text),
        )
        return hmac.compare_digest(computed, digest)
    except Exception:
        return False


def ensure_default_admin() -> None:
    username = str(os.getenv("ADMIN_USERNAME") or "admin").strip()
    password = str(os.getenv("ADMIN_PASSWORD") or "admin123").strip()
    ts = now_ts()
    with db() as conn:
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO users(username, password_hash, role, is_disabled, created_at, updated_at)
                VALUES (?, ?, 'admin', 0, ?, ?)
                """,
                (username, hash_password(password), ts, ts),
            )


def create_session(conn, user_id: int, ttl_seconds: int = 14 * 24 * 3600) -> str:
    token = secrets.token_urlsafe(32)
    ts = now_ts()
    conn.execute(
        "INSERT INTO sessions(token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, int(user_id), ts + int(ttl_seconds), ts),
    )
    return token


def delete_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (str(token),))


def get_current_user(session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)) -> dict[str, Any]:
    token = str(session_token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    with db() as conn:
        row = conn.execute(
            """
            SELECT s.expires_at, u.* FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (token,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="Session expired")
        if int(row["expires_at"]) <= int(time.time()):
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            raise HTTPException(status_code=401, detail="Session expired")
        if int(row["is_disabled"]) == 1:
            raise HTTPException(status_code=403, detail="Account disabled")
        return dict(row)


def require_admin(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    if str(user.get("role") or "") != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user
