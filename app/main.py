from __future__ import annotations

import os
import contextlib
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .auth import SESSION_COOKIE, create_session, delete_session, ensure_default_admin, require_admin, verify_password
from .db import db, init_db
from .service import CustomerServiceStore
from .bot import TelegramCustomerBot


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "web" / "static"
store = CustomerServiceStore()
telegram_bot = TelegramCustomerBot(store)


class LoginPayload(BaseModel):
    username: str
    password: str


class BotConfigPayload(BaseModel):
    bot_token: str = ""
    handoff_timeout_minutes: int | None = Field(default=None, ge=1, le=1440)
    conversation_retention_days: int | None = Field(default=None, ge=0, le=3650)


class TelegramUserPayload(BaseModel):
    telegram_id: int = Field(gt=0)
    remark_name: str = ""
    is_enabled: bool = True


class TelegramAdminPayload(BaseModel):
    telegram_id: int = Field(gt=0)
    display_name: str = ""
    is_enabled: bool = True


class PresetReplyPayload(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


def create_app() -> FastAPI:
    init_db()
    ensure_default_admin()
    app = FastAPI(title="Telegram Customer Service Bot", version="1.0.0")
    app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/admin")

    @app.get("/login", include_in_schema=False)
    async def login_page():
        return FileResponse(STATIC_DIR / "login.html")

    @app.get("/admin", include_in_schema=False)
    async def admin_page():
        return FileResponse(STATIC_DIR / "admin.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    @app.post("/api/auth/login")
    async def login(payload: LoginPayload):
        with db() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (payload.username,)).fetchone()
            if row is None or int(row["is_disabled"]) == 1 or not verify_password(payload.password, row["password_hash"]):
                raise HTTPException(status_code=401, detail="用户名或密码错误")
            token = create_session(conn, int(row["id"]))
        resp = JSONResponse({"ok": True})
        resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", max_age=14 * 24 * 3600)
        return resp

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            with db() as conn:
                delete_session(conn, token)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE)
        return resp

    @app.get("/api/me")
    async def me(user: dict[str, Any] = Depends(require_admin)):
        return {"id": user["id"], "username": user["username"], "role": user["role"]}

    @app.get("/api/admin/bot-config")
    async def get_bot_config(request: Request, user: dict[str, Any] = Depends(require_admin)):
        config = config_for_response(request)
        config["bot_token_masked"] = mask_secret(config.get("bot_token"))
        config["bot_token"] = ""
        return config

    @app.put("/api/admin/bot-config")
    async def update_bot_config(payload: BotConfigPayload, user: dict[str, Any] = Depends(require_admin)):
        data: dict[str, Any] = {"bot_token": payload.bot_token}
        if payload.handoff_timeout_minutes is not None:
            data["handoff_timeout_minutes"] = payload.handoff_timeout_minutes
        if payload.conversation_retention_days is not None:
            data["conversation_retention_days"] = payload.conversation_retention_days
        return store.update_bot_config(data)

    @app.get("/api/admin/users")
    async def list_users(user: dict[str, Any] = Depends(require_admin)):
        return store.list_telegram_users()

    @app.post("/api/admin/users")
    async def create_user(payload: TelegramUserPayload, user: dict[str, Any] = Depends(require_admin)):
        item = store.upsert_telegram_user(payload.telegram_id, payload.remark_name, payload.is_enabled)
        with contextlib.suppress(Exception):
            await telegram_bot.sync_user_commands_for_id(payload.telegram_id)
        return item

    @app.put("/api/admin/users/{telegram_id}")
    async def update_user(telegram_id: int, payload: TelegramUserPayload, user: dict[str, Any] = Depends(require_admin)):
        item = store.upsert_telegram_user(telegram_id, payload.remark_name, payload.is_enabled)
        with contextlib.suppress(Exception):
            await telegram_bot.sync_user_commands_for_id(telegram_id)
        return item

    @app.delete("/api/admin/users/{telegram_id}")
    async def delete_user(telegram_id: int, user: dict[str, Any] = Depends(require_admin)):
        with contextlib.suppress(Exception):
            await telegram_bot.sync_user_commands_for_id(telegram_id)
        store.delete_telegram_user(telegram_id)
        return {"ok": True}

    @app.get("/api/admin/admins")
    async def list_admins(user: dict[str, Any] = Depends(require_admin)):
        return store.list_telegram_admins()

    @app.post("/api/admin/admins")
    async def create_admin(payload: TelegramAdminPayload, user: dict[str, Any] = Depends(require_admin)):
        item = store.upsert_telegram_admin(payload.telegram_id, payload.display_name, payload.is_enabled)
        with contextlib.suppress(Exception):
            await telegram_bot.sync_admin_commands_for_id(payload.telegram_id, payload.is_enabled)
        return item

    @app.put("/api/admin/admins/{telegram_id}")
    async def update_admin(telegram_id: int, payload: TelegramAdminPayload, user: dict[str, Any] = Depends(require_admin)):
        item = store.upsert_telegram_admin(telegram_id, payload.display_name, payload.is_enabled)
        with contextlib.suppress(Exception):
            await telegram_bot.sync_admin_commands_for_id(telegram_id, payload.is_enabled)
        return item

    @app.delete("/api/admin/admins/{telegram_id}")
    async def delete_admin(telegram_id: int, user: dict[str, Any] = Depends(require_admin)):
        with contextlib.suppress(Exception):
            await telegram_bot.sync_admin_commands_for_id(telegram_id, False)
        store.delete_telegram_admin(telegram_id)
        return {"ok": True}

    @app.get("/api/admin/preset-replies")
    async def get_preset_replies(user: dict[str, Any] = Depends(require_admin)):
        return store.list_preset_replies()

    @app.put("/api/admin/preset-replies")
    async def update_preset_replies(payload: PresetReplyPayload, user: dict[str, Any] = Depends(require_admin)):
        return store.replace_preset_replies(payload.items)

    @app.get("/api/admin/conversations")
    async def get_conversations(user: dict[str, Any] = Depends(require_admin)):
        return store.list_all_conversations()

    @app.get("/api/admin/conversations/{conversation_id}/messages")
    async def get_messages(conversation_id: int, user: dict[str, Any] = Depends(require_admin)):
        return store.list_messages(conversation_id, limit=200)

    @app.post("/api/admin/conversations/cleanup")
    async def cleanup_conversations(user: dict[str, Any] = Depends(require_admin)):
        config = store.get_bot_config()
        deleted = store.delete_old_conversations(int(config.get("conversation_retention_days") or 0))
        return {"deleted": deleted}

    @app.post("/telegram/webhook/{secret}")
    async def telegram_webhook(secret: str, request: Request):
        config = store.get_bot_config()
        if secret != str(config.get("webhook_secret") or ""):
            raise HTTPException(status_code=404, detail="Not found")
        payload = await request.json()
        await telegram_bot.feed_update(payload)
        return {"ok": True}

    return app


def config_for_response(request: Request) -> dict[str, Any]:
    config = store.get_bot_config()
    config["public_webhook_url"] = str(request.url_for("telegram_webhook", secret=config["webhook_secret"]))
    return config


def mask_secret(value: Any) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return "*" * len(text)
    return f"{text[:4]}...{text[-4:]}"


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8098")), reload=True)
