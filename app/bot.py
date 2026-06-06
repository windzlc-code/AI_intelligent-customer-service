from __future__ import annotations

import contextlib
import asyncio
from datetime import datetime, timezone, timedelta
import html
import socket
from typing import Any

from aiohttp import TCPConnector
from aiohttp.resolver import ThreadedResolver
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
    BotCommandScopeDefault,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    Update,
)

from .db import now_ts
from .service import CustomerServiceStore
from .defaults import (
    AUTO_HANDOFF_TIMEOUT_TEXT,
    FEEDBACK_BUTTON_TEXT,
    FUZZY_MATCH_REPLY_TEXT,
    FEEDBACK_PROMPT_TEXT,
    FEEDBACK_THANKS_TEXT,
    OTHER_BUTTON_TEXT,
    OTHER_ACK_TEXT,
    OTHER_HANDOFF_TEXT,
    PAYMENT_AFTER_INPUT_TEXT,
    PAYMENT_BUTTON_TEXT,
    PAYMENT_HANDOFF_TEXT,
    PAYMENT_LINK_URL,
    PAYMENT_USERNAME_MISSING_TEXT,
    TOPIC_HANDOFF_NOTICE_TEXT,
)


ADMIN_PENDING = "人工服务处理"
ADMIN_MY = "建议反馈处理"
ADMIN_RECENT = "最近会话记录"
ADMIN_ALL = "全部會話"
ADMIN_CLEAR = "清除當前會話"
ADMIN_RELEASE = ADMIN_CLEAR
ADMIN_HANDOFF_PAGE_SIZE = 5
ADMIN_LIST_LOOKBACK_DAYS = 7
ADMIN_LIST_USER_LIMIT = 10

USER_COMMANDS = [
    BotCommand(command="start", description="開始使用"),
    BotCommand(command="feedback", description=FEEDBACK_BUTTON_TEXT),
    BotCommand(command="other", description=OTHER_BUTTON_TEXT),
    BotCommand(command="end", description="結束人工服務"),
]
ADMIN_COMMAND = BotCommand(command="admin", description="管理員人工端")


def user_full_name(message: Message) -> str:
    user = message.from_user
    if not user:
        return ""
    name = " ".join([part for part in [user.first_name, user.last_name] if part]).strip()
    if name:
        return name
    if user.username:
        return f"@{user.username}"
    return ""


def username(message: Message) -> str:
    return str(message.from_user.username or "") if message.from_user else ""


def message_type_and_file_id(message: Message) -> tuple[str, str]:
    if message.photo:
        return "photo", message.photo[-1].file_id
    if message.voice:
        return "voice", message.voice.file_id
    if message.document:
        return "document", message.document.file_id
    if message.video:
        return "video", message.video.file_id
    if message.audio:
        return "audio", message.audio.file_id
    if message.sticker:
        return "sticker", message.sticker.file_id
    return "text", ""


def display_message_text(message: Message) -> str:
    return str(message.text or message.caption or "").strip()


def normalize_payment_username(value: Any) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def is_payment_username_match(message: Message) -> bool:
    if not message.from_user:
        return False
    expected = normalize_payment_username(message.from_user.username)
    received = normalize_payment_username(display_message_text(message))
    return bool(expected and received and expected == received)


def html_escape(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


def merge_bot_commands(existing: list[Any], required: list[BotCommand]) -> list[BotCommand]:
    merged: list[BotCommand] = []
    seen: set[str] = set()
    required_by_name = {command.command: command for command in required}
    for command in existing:
        name = str(getattr(command, "command", "") or "").strip()
        if not name or name in seen:
            continue
        merged.append(required_by_name.get(name) or command)
        seen.add(name)
    for command in required:
        if command.command not in seen:
            merged.append(command)
            seen.add(command.command)
    return merged[:100]


def format_message_time(timestamp: Any) -> str:
    try:
        value = int(timestamp)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return "-"
    return datetime.fromtimestamp(value, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")


def format_short_time(timestamp: Any) -> str:
    try:
        value = int(timestamp)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return "-"
    return datetime.fromtimestamp(value, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")


def fixed_width(text: Any, width: int) -> str:
    value = str(text or "-").replace("\n", " ").strip() or "-"
    if len(value) > width:
        value = value[: max(1, width - 1)] + "…"
    return value.ljust(width)


def admin_identity_button(user_id: Any, display_name: Any, width: int = 18) -> str:
    user_id_text = str(user_id)
    display = str(display_name or "").replace("\n", " ").strip()
    if not display or display == user_id_text:
        return f"ID {user_id_text}"
    if len(display) > width:
        display = display[: max(1, width - 1)] + "…"
    return f"ID {user_id_text} · {display}"


def admin_table(lines: list[str]) -> str:
    return "\n".join(f"<code>{html_escape(line)}</code>" for line in lines)


class TelegramCustomerBot:
    def __init__(self, store: CustomerServiceStore) -> None:
        self.store = store
        self.dispatcher = Dispatcher()
        self.router = Router()
        self.router.message(CommandStart())(self.start)
        self.router.message(Command("admin"))(self.admin_home)
        self.router.message(Command("payment"))(self.payment_command)
        self.router.message(Command("feedback"))(self.feedback_command)
        self.router.message(Command("other"))(self.other_command)
        self.router.message(Command("end"))(self.end_handoff_command)
        self.router.callback_query(F.data == "user:handoff:start")(self.user_handoff_start_callback)
        self.router.callback_query(F.data == "user:handoff:end")(self.user_handoff_end_callback)
        self.router.callback_query(F.data.startswith("user:topic:"))(self.user_topic_callback)
        self.router.callback_query(F.data.startswith("user:preset:"))(self.user_preset_callback)
        self.router.callback_query(F.data.startswith("admin_handoff_page:"))(self.admin_handoff_page_callback)
        self.router.callback_query(F.data.startswith("admin_handoff_detail:"))(self.admin_handoff_detail_callback)
        self.router.callback_query(F.data.startswith("admin_handoff_reply:"))(self.admin_handoff_reply_callback)
        self.router.callback_query(F.data.startswith("admin_handoff_ignore:"))(self.admin_handoff_ignore_callback)
        self.router.callback_query(F.data.startswith("admin_handoff_back:"))(self.admin_handoff_back_callback)
        self.router.callback_query(F.data.startswith("admin_feedback_page:"))(self.admin_feedback_page_callback)
        self.router.callback_query(F.data.startswith("admin_feedback_detail:"))(self.admin_feedback_detail_callback)
        self.router.callback_query(F.data.startswith("admin_feedback_reply:"))(self.admin_feedback_reply_callback)
        self.router.callback_query(F.data.startswith("admin_feedback_ignore:"))(self.admin_feedback_ignore_callback)
        self.router.callback_query(F.data.startswith("admin_recent_page:"))(self.admin_recent_page_callback)
        self.router.callback_query(F.data.startswith("admin_recent_detail:"))(self.admin_recent_detail_callback)
        self.router.callback_query(F.data.startswith("claim:"))(self.claim_callback)
        self.router.callback_query(F.data.startswith("view_feedback:"))(self.view_feedback_callback)
        self.router.callback_query(F.data.startswith("view:"))(self.view_callback)
        self.router.callback_query(F.data.startswith("release:"))(self.release_callback)
        self.router.message()(self.handle_message)
        self.dispatcher.include_router(self.router)

    def make_bot(self) -> Bot:
        config = self.store.get_bot_config()
        token = str(config.get("bot_token") or "").strip()
        if not token:
            raise RuntimeError("Bot token is not configured")
        session = AiohttpSession()
        session._connector_type = TCPConnector
        session._connector_init.update({"resolver": ThreadedResolver(), "family": socket.AF_INET})
        return Bot(token=token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    async def setup_bot_commands(self, bot: Bot) -> None:
        base_commands = await self.sync_default_commands(bot)
        enabled_admin_ids = set(self.store.enabled_admin_ids())
        known_user_ids = set(self.store.list_telegram_user_ids())
        known_chat_ids = set(self.store.list_telegram_user_ids()) | set(self.store.list_telegram_admin_ids())
        for user_id in sorted(known_user_ids - enabled_admin_ids):
            await self.set_user_commands(bot, user_id, base_commands)
        for chat_id in sorted(known_chat_ids - known_user_ids - enabled_admin_ids):
            await self.clear_chat_commands(bot, chat_id)
        for admin_id in sorted(enabled_admin_ids):
            await self.set_admin_commands(bot, admin_id, enabled=True, base_commands=base_commands)

    async def sync_default_commands(self, bot: Bot) -> list[BotCommand]:
        try:
            base_commands = list(await bot.get_my_commands(scope=BotCommandScopeDefault()))
            commands = merge_bot_commands(base_commands, USER_COMMANDS)
            await bot.set_my_commands(commands)
            await bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
            return commands
        except Exception:
            return USER_COMMANDS

    async def set_admin_commands(self, bot: Bot, admin_id: int, enabled: bool, base_commands: list[BotCommand] | None = None) -> None:
        scope = BotCommandScopeChat(chat_id=int(admin_id))
        if not enabled:
            await self.clear_chat_commands(bot, admin_id)
            return
        try:
            commands_base = base_commands if base_commands is not None else await self.sync_default_commands(bot)
            commands = [command for command in commands_base if command.command != ADMIN_COMMAND.command]
            commands.append(ADMIN_COMMAND)
            await bot.set_my_commands(commands, scope=scope)
        except Exception:
            pass

    async def set_user_commands(self, bot: Bot, user_id: int, base_commands: list[BotCommand] | None = None) -> None:
        try:
            commands_base = base_commands if base_commands is not None else await self.sync_default_commands(bot)
            commands = [command for command in commands_base if command.command != ADMIN_COMMAND.command]
            await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=int(user_id)))
        except Exception:
            pass

    async def clear_chat_commands(self, bot: Bot, chat_id: int) -> None:
        with contextlib.suppress(Exception):
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=int(chat_id)))

    async def sync_admin_commands_for_id(self, admin_id: int, enabled: bool) -> None:
        bot = self.make_bot()
        try:
            await self.set_admin_commands(bot, admin_id, enabled)
        finally:
            await bot.session.close()

    async def sync_user_commands_for_id(self, user_id: int) -> None:
        bot = self.make_bot()
        try:
            base_commands = await self.sync_default_commands(bot)
            if self.store.is_authorized_admin(user_id):
                await self.set_admin_commands(bot, user_id, enabled=True, base_commands=base_commands)
            else:
                await self.set_user_commands(bot, user_id, base_commands=base_commands)
        finally:
            await bot.session.close()

    async def feed_update(self, update_payload: dict[str, Any]) -> None:
        bot = self.make_bot()
        try:
            update = Update.model_validate(update_payload, context={"bot": bot})
            await self.dispatcher.feed_update(bot, update)
        finally:
            await bot.session.close()

    def user_menu(self) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text=FEEDBACK_BUTTON_TEXT, callback_data="user:topic:feedback")],
            [InlineKeyboardButton(text=OTHER_BUTTON_TEXT, callback_data="user:topic:other")],
        ]
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def handoff_menu(self) -> InlineKeyboardMarkup:
        config = self.store.get_bot_config()
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=str(config["end_handoff_button_text"]), callback_data="user:handoff:end")]]
        )

    def payment_handoff_menu(self) -> InlineKeyboardMarkup:
        config = self.store.get_bot_config()
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="付費連結", url=PAYMENT_LINK_URL)],
                [InlineKeyboardButton(text=str(config["end_handoff_button_text"]), callback_data="user:handoff:end")],
            ]
        )

    def admin_menu(self, admin_id: int | None = None):
        from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

        pending_count = len(self.pending_handoff_items())
        feedback_count = len(self.feedback_conversation_items())
        rows = [
            [KeyboardButton(text=self.admin_button_text(ADMIN_PENDING, pending_count)), KeyboardButton(text=self.admin_button_text(ADMIN_MY, feedback_count))],
            [KeyboardButton(text=ADMIN_RECENT)],
        ]
        return ReplyKeyboardMarkup(
            keyboard=rows,
            resize_keyboard=True,
        )

    def admin_button_text(self, label: str, count: int) -> str:
        return f"{label}（{count}）" if count > 0 else label

    def admin_button_matches(self, text: str, label: str) -> bool:
        return text == label or text.startswith(f"{label} ") or text.startswith(f"{label}（")

    async def start(self, message: Message) -> None:
        if not message.from_user:
            return
        user_id = int(message.from_user.id)
        config = self.store.get_bot_config()
        if self.store.is_authorized_admin(user_id):
            self.store.update_admin_seen(user_id, user_full_name(message), username(message))
            if message.bot:
                await self.set_admin_commands(message.bot, user_id, enabled=True)
            await message.answer("您是已授權管理員，請發送 /admin 進入人工端。", reply_markup=self.admin_menu(user_id))
            return
        self.store.update_user_seen(user_id, user_full_name(message), username(message))
        if message.bot:
            await self.set_user_commands(message.bot, user_id)
        self.store.get_or_create_conversation(user_id)
        await self.remove_reply_keyboard(message)
        await message.answer(str(config["welcome_text"]), reply_markup=self.user_menu())

    async def payment_command(self, message: Message) -> None:
        await self.user_topic_command(message, "payment")

    async def feedback_command(self, message: Message) -> None:
        await self.user_topic_command(message, "feedback")

    async def other_command(self, message: Message) -> None:
        await self.user_topic_command(message, "other")

    async def end_handoff_command(self, message: Message) -> None:
        if not message.from_user:
            return
        user_id = int(message.from_user.id)
        config = self.store.get_bot_config()
        if not self.store.is_authorized_user(user_id):
            await message.answer(str(config["unauthorized_text"]), reply_markup=ReplyKeyboardRemove())
            return
        conversation = self.store.get_or_create_conversation(user_id)
        if not str(conversation["status"]).startswith("handoff"):
            await message.answer("目前沒有進行中的人工服務。", reply_markup=self.user_menu())
            return
        conversation = self.store.close_handoff(user_id)
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", str(config["handoff_close_text"]))
        await message.answer(str(config["handoff_close_text"]), reply_markup=self.user_menu())
        await self.notify_admins_handoff_closed(message, conversation)

    async def admin_home(self, message: Message) -> None:
        if not message.from_user:
            return
        admin_id = int(message.from_user.id)
        if not self.store.is_authorized_admin(admin_id):
            await message.answer("當前 Telegram ID 未授權使用管理員端。")
            return
        self.store.update_admin_seen(admin_id, user_full_name(message), username(message))
        if message.bot:
            await self.set_admin_commands(message.bot, admin_id, enabled=True)
        await message.answer("管理員端已開啟。請選擇要查看或接管的會話。", reply_markup=self.admin_menu(admin_id))
        await self.send_conversation_list(message, scope="pending")

    async def user_topic_command(self, message: Message, topic: str) -> None:
        if not message.from_user:
            return
        user_id = int(message.from_user.id)
        config = self.store.get_bot_config()
        if not self.store.is_authorized_user(user_id):
            await message.answer(str(config["unauthorized_text"]), reply_markup=ReplyKeyboardRemove())
            return
        self.store.update_user_seen(user_id, user_full_name(message), username(message))
        display_name = self.store.get_display_name_for_user(user_id, user_full_name(message))
        conversation = self.store.get_or_create_conversation(user_id)
        if topic == "payment":
            self.store.add_message(conversation["id"], "user", user_id, display_name, "command", "/payment")
            await self.open_topic_handoff_from_message(message, PAYMENT_HANDOFF_TEXT, "handoff_payment_waiting", PAYMENT_BUTTON_TEXT)
            return
        if topic == "other":
            self.store.add_message(conversation["id"], "user", user_id, display_name, "command", "/other")
            await self.open_topic_handoff_from_message(message, OTHER_HANDOFF_TEXT, "handoff_open", OTHER_BUTTON_TEXT)
            return
        if topic == "feedback":
            conversation = self.store.set_conversation_status(user_id, "feedback_waiting")
            self.store.add_message(conversation["id"], "user", user_id, display_name, "command", "/feedback")
            self.store.add_message(conversation["id"], "bot", None, "Bot", "text", FEEDBACK_PROMPT_TEXT)
            await message.answer(FEEDBACK_PROMPT_TEXT, reply_markup=self.user_menu())

    async def handle_message(self, message: Message) -> None:
        if not message.from_user:
            return
        sender_id = int(message.from_user.id)
        if self.store.is_authorized_admin(sender_id):
            self.store.update_admin_seen(sender_id, user_full_name(message), username(message))
            if await self.handle_admin_message(message):
                return
        if self.store.is_authorized_user(sender_id):
            self.store.update_user_seen(sender_id, user_full_name(message), username(message))
            await self.handle_user_message(message)
            return
        config = self.store.get_bot_config()
        await message.answer(str(config["unauthorized_text"]), reply_markup=ReplyKeyboardRemove())

    async def handle_user_message(self, message: Message) -> None:
        assert message.from_user is not None
        user_id = int(message.from_user.id)
        text = str(message.text or "").strip()
        config = self.store.get_bot_config()
        conversation = self.store.get_or_create_conversation(user_id)
        if text == str(config["handoff_button_text"]):
            conversation = self.store.open_handoff(user_id)
            self.store.add_message(conversation["id"], "bot", None, "Bot", "text", str(config["handoff_open_text"]))
            await message.answer(str(config["handoff_open_text"]), reply_markup=self.handoff_menu())
            await self.notify_admins_handoff_open(message, conversation)
            return
        if text == str(config["end_handoff_button_text"]) and str(conversation["status"]).startswith("handoff"):
            conversation = self.store.close_handoff(user_id)
            self.store.add_message(conversation["id"], "bot", None, "Bot", "text", str(config["handoff_close_text"]))
            await message.answer(str(config["handoff_close_text"]), reply_markup=self.user_menu())
            await self.notify_admins_handoff_closed(message, conversation)
            return
        if str(conversation["status"]) == "handoff_payment_waiting":
            await self.handle_payment_input_message(message, conversation)
            return
        if str(conversation["status"]).startswith("handoff"):
            await self.record_and_forward_user_message(message, conversation)
            if self.should_send_other_handoff_ack(int(conversation["id"])):
                self.store.add_message(conversation["id"], "bot", None, "Bot", "text", OTHER_ACK_TEXT)
                await message.answer(OTHER_ACK_TEXT, reply_markup=self.handoff_menu())
            return
        if str(conversation["status"]) == "feedback_waiting":
            await self.handle_feedback_message(message, conversation)
            return
        for item in self.store.list_preset_replies(enabled_only=True):
            if text == str(item["button_text"]):
                self.store.add_message(conversation["id"], "user", user_id, self.store.get_display_name_for_user(user_id, user_full_name(message)), "text", text, message.message_id)
                self.store.add_message(conversation["id"], "bot", None, "Bot", "text", str(item["reply_text"]))
                await message.answer(str(item["reply_text"]), reply_markup=self.user_menu())
                return
        display_name = self.store.get_display_name_for_user(user_id, user_full_name(message))
        msg_type, file_id = message_type_and_file_id(message)
        self.store.add_message(conversation["id"], "user", user_id, display_name, msg_type, display_message_text(message), message.message_id, file_id)
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", FUZZY_MATCH_REPLY_TEXT)
        await message.answer(FUZZY_MATCH_REPLY_TEXT, reply_markup=self.user_menu())

    async def user_topic_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        user_id = int(query.from_user.id)
        if not self.store.is_authorized_user(user_id):
            await query.answer("未授权", show_alert=True)
            return
        self.store.update_user_seen(user_id, query.from_user.full_name, str(query.from_user.username or ""))
        topic = str(query.data or "").rsplit(":", 1)[1]
        display_name = self.store.get_display_name_for_user(user_id, query.from_user.full_name)
        conversation = self.store.get_or_create_conversation(user_id)
        if topic == "payment":
            self.store.add_message(conversation["id"], "user", user_id, display_name, "callback", PAYMENT_BUTTON_TEXT)
            await self.open_topic_handoff_from_query(query, PAYMENT_HANDOFF_TEXT, "handoff_payment_waiting", PAYMENT_BUTTON_TEXT)
            return
        if topic == "other":
            self.store.add_message(conversation["id"], "user", user_id, display_name, "callback", OTHER_BUTTON_TEXT)
            await self.open_topic_handoff_from_query(query, OTHER_HANDOFF_TEXT, "handoff_open", OTHER_BUTTON_TEXT)
            return
        if topic == "feedback":
            conversation = self.store.set_conversation_status(user_id, "feedback_waiting")
            self.store.add_message(conversation["id"], "user", user_id, display_name, "callback", FEEDBACK_BUTTON_TEXT)
            self.store.add_message(conversation["id"], "bot", None, "Bot", "text", FEEDBACK_PROMPT_TEXT)
            await query.answer()
            await query.message.answer(FEEDBACK_PROMPT_TEXT, reply_markup=self.user_menu())
            return
        await query.answer("按钮已失效", show_alert=True)

    async def remove_reply_keyboard(self, message: Message) -> None:
        cleanup = await message.answer("\u2060", reply_markup=ReplyKeyboardRemove())
        with contextlib.suppress(Exception):
            await cleanup.delete()

    async def user_handoff_start_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        user_id = int(query.from_user.id)
        if not self.store.is_authorized_user(user_id):
            await query.answer("未授权", show_alert=True)
            return
        self.store.update_user_seen(user_id, query.from_user.full_name, str(query.from_user.username or ""))
        conversation = self.store.open_handoff(user_id)
        config = self.store.get_bot_config()
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", str(config["handoff_open_text"]))
        await query.answer("已转接")
        await query.message.answer(str(config["handoff_open_text"]), reply_markup=self.handoff_menu())
        await self.notify_admins_handoff_open_from_query(query, conversation)

    async def user_handoff_end_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        user_id = int(query.from_user.id)
        if not self.store.is_authorized_user(user_id):
            await query.answer("未授权", show_alert=True)
            return
        conversation = self.store.close_handoff(user_id)
        config = self.store.get_bot_config()
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", str(config["handoff_close_text"]))
        await query.answer("已结束")
        await query.message.answer(str(config["handoff_close_text"]), reply_markup=self.user_menu())
        await self.notify_admins_handoff_closed_from_query(query, conversation)

    async def user_preset_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        user_id = int(query.from_user.id)
        if not self.store.is_authorized_user(user_id):
            await query.answer("未授权", show_alert=True)
            return
        preset_id = int(str(query.data or "").rsplit(":", 1)[1])
        item = next((x for x in self.store.list_preset_replies(enabled_only=True) if int(x["id"]) == preset_id), None)
        if item is None:
            await query.answer("按钮已失效", show_alert=True)
            return
        display_name = self.store.get_display_name_for_user(user_id, query.from_user.full_name)
        conversation = self.store.get_or_create_conversation(user_id)
        self.store.add_message(conversation["id"], "user", user_id, display_name, "callback", str(item["button_text"]))
        if str(item["button_text"]) == PAYMENT_BUTTON_TEXT:
            await self.open_topic_handoff_from_query(query, PAYMENT_HANDOFF_TEXT, "handoff_payment_waiting", PAYMENT_BUTTON_TEXT)
            return
        if str(item["button_text"]) == OTHER_BUTTON_TEXT:
            await self.open_topic_handoff_from_query(query, OTHER_HANDOFF_TEXT, "handoff_open", OTHER_BUTTON_TEXT)
            return
        if str(item["button_text"]) == FEEDBACK_BUTTON_TEXT:
            conversation = self.store.set_conversation_status(user_id, "feedback_waiting")
            self.store.add_message(conversation["id"], "bot", None, "Bot", "text", FEEDBACK_PROMPT_TEXT)
            await query.answer()
            await query.message.answer(FEEDBACK_PROMPT_TEXT, reply_markup=self.user_menu())
            return
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", str(item["reply_text"]))
        await query.answer()
        await query.message.answer(str(item["reply_text"]) or str(item["button_text"]), reply_markup=self.user_menu())

    async def open_topic_handoff_from_query(self, query: CallbackQuery, prompt: str, status: str = "handoff_open", topic_label: str = "") -> None:
        if not query.from_user or not query.message:
            return
        user_id = int(query.from_user.id)
        conversation = self.store.open_handoff(user_id)
        if status != "handoff_open":
            conversation = self.store.set_conversation_status(user_id, status)
        user_notice = prompt
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", user_notice)
        await query.answer("已转接")
        await query.message.answer(user_notice, reply_markup=self.handoff_menu())
        await self.notify_admins_handoff_open_from_query(query, conversation, topic_label)

    async def open_topic_handoff_from_message(self, message: Message, prompt: str, status: str = "handoff_open", topic_label: str = "") -> None:
        if not message.from_user:
            return
        user_id = int(message.from_user.id)
        conversation = self.store.open_handoff(user_id)
        if status != "handoff_open":
            conversation = self.store.set_conversation_status(user_id, status)
        user_notice = prompt
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", user_notice)
        await message.answer(user_notice, reply_markup=self.handoff_menu())
        await self.notify_admins_handoff_open(message, conversation, topic_label)

    async def handle_payment_input_message(self, message: Message, conversation: dict[str, Any]) -> None:
        if message.from_user and not normalize_payment_username(message.from_user.username):
            self.store.add_message(
                conversation["id"],
                "user",
                int(message.from_user.id),
                self.store.get_display_name_for_user(int(message.from_user.id), user_full_name(message)),
                "text",
                display_message_text(message),
                message.message_id,
            )
            self.store.add_message(conversation["id"], "bot", None, "Bot", "text", PAYMENT_USERNAME_MISSING_TEXT)
            await message.answer(PAYMENT_USERNAME_MISSING_TEXT, reply_markup=self.handoff_menu())
            return
        if not is_payment_username_match(message):
            if message.from_user:
                self.store.add_message(
                    conversation["id"],
                    "user",
                    int(message.from_user.id),
                    self.store.get_display_name_for_user(int(message.from_user.id), user_full_name(message)),
                    "text",
                    display_message_text(message),
                    message.message_id,
                )
            self.store.add_message(conversation["id"], "bot", None, "Bot", "text", PAYMENT_HANDOFF_TEXT)
            await message.answer(PAYMENT_HANDOFF_TEXT, reply_markup=self.handoff_menu())
            return
        await self.record_and_forward_user_message(message, conversation)
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", PAYMENT_AFTER_INPUT_TEXT)
        await message.answer(PAYMENT_AFTER_INPUT_TEXT, reply_markup=self.payment_handoff_menu())
        closed = self.store.close_handoff(int(message.from_user.id))
        await self.notify_admins_handoff_auto_closed(message, closed, PAYMENT_BUTTON_TEXT)

    def should_send_other_handoff_ack(self, conversation_id: int) -> bool:
        messages = self.store.list_messages(conversation_id, limit=100)
        other_start_index = -1
        for index, item in enumerate(messages):
            if item["direction"] != "user":
                continue
            if item["message_type"] == "callback" and item["text"] == OTHER_BUTTON_TEXT:
                other_start_index = index
            if item["message_type"] == "command" and item["text"] == "/other":
                other_start_index = index
        if other_start_index < 0:
            return False
        after_start = messages[other_start_index + 1 :]
        if any(item["direction"] == "bot" and item["text"] == OTHER_ACK_TEXT for item in after_start):
            return False
        return any(item["direction"] == "user" and int(item["forwarded_to_admins"]) == 1 for item in after_start)

    async def handle_feedback_message(self, message: Message, conversation: dict[str, Any]) -> None:
        assert message.from_user is not None
        user_id = int(message.from_user.id)
        display_name = self.store.get_display_name_for_user(user_id, user_full_name(message))
        msg_type, file_id = message_type_and_file_id(message)
        saved = self.store.add_message(
            conversation["id"],
            "user",
            user_id,
            display_name,
            msg_type,
            display_message_text(message),
            message.message_id,
            file_id,
            forwarded_to_admins=True,
        )
        await self.forward_to_admins(
            message,
            conversation,
            display_name,
            msg_type,
            display_message_text(message),
            int(saved["created_at"]),
            allow_claim=False,
            title="用戶建議/心得",
        )
        conversation = self.store.set_conversation_status(user_id, "bot")
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", FEEDBACK_THANKS_TEXT)
        await message.answer(FEEDBACK_THANKS_TEXT, reply_markup=self.user_menu())

    async def record_and_forward_user_message(self, message: Message, conversation: dict[str, Any]) -> None:
        assert message.from_user is not None
        user_id = int(message.from_user.id)
        msg_type, file_id = message_type_and_file_id(message)
        display_name = self.store.get_display_name_for_user(user_id, user_full_name(message))
        text = display_message_text(message)
        saved = self.store.add_message(
            conversation["id"],
            "user",
            user_id,
            display_name,
            msg_type,
            text,
            message.message_id,
            file_id,
            forwarded_to_admins=True,
        )
        await self.forward_to_admins(message, conversation, display_name, msg_type, text, int(saved["created_at"]))

    async def forward_to_admins(
        self,
        message: Message,
        conversation: dict[str, Any],
        display_name: str,
        msg_type: str,
        text: str,
        created_at: int,
        allow_claim: bool = True,
        title: str = "",
    ) -> None:
        if not message.bot:
            return
        prefix = (
            (f"{html_escape(title)}\n" if title else "") +
            f"用戶：<b>{html_escape(display_name)}</b>\n"
            f"Telegram ID：<code>{conversation['telegram_user_id']}</code>\n"
            f"發送時間：<code>{format_message_time(created_at)}</code>\n"
            f"類型：{html_escape(msg_type)}"
        )
        if msg_type == "text" and text:
            prefix += f"\n\n{html_escape(text)}"
        buttons = [InlineKeyboardButton(text="查看歷史", callback_data=f"view:{conversation['id']}")]
        if allow_claim:
            buttons.append(InlineKeyboardButton(text="接管", callback_data=f"claim:{conversation['id']}"))
        markup = InlineKeyboardMarkup(
            inline_keyboard=[buttons]
        )
        for admin_id in self.store.enabled_admin_ids():
            with contextlib.suppress(Exception):
                await message.bot.send_message(admin_id, prefix, reply_markup=markup)
                if msg_type != "text":
                    await message.bot.copy_message(admin_id, message.chat.id, message.message_id)

    async def notify_admins_handoff_open(self, message: Message, conversation: dict[str, Any], topic_label: str = "") -> None:
        if not message.bot or not message.from_user:
            return
        display_name = self.store.get_display_name_for_user(int(message.from_user.id), user_full_name(message))
        topic_line = f"入口：<b>{html_escape(topic_label)}</b>\n" if topic_label else ""
        text = (
            f"新人工會話\n"
            f"{topic_line}"
            f"用戶：<b>{html_escape(display_name)}</b>\n"
            f"Telegram ID：<code>{conversation['telegram_user_id']}</code>"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="接管", callback_data=f"claim:{conversation['id']}")]]
        )
        for admin_id in self.store.enabled_admin_ids():
            with contextlib.suppress(Exception):
                await message.bot.send_message(admin_id, text, reply_markup=markup)

    async def notify_admins_handoff_open_from_query(self, query: CallbackQuery, conversation: dict[str, Any], topic_label: str = "") -> None:
        if not query.bot or not query.from_user:
            return
        display_name = self.store.get_display_name_for_user(int(query.from_user.id), query.from_user.full_name)
        topic_line = f"入口：<b>{html_escape(topic_label)}</b>\n" if topic_label else ""
        text = (
            f"新人工會話\n"
            f"{topic_line}"
            f"用戶：<b>{html_escape(display_name)}</b>\n"
            f"Telegram ID：<code>{conversation['telegram_user_id']}</code>"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="接管", callback_data=f"claim:{conversation['id']}")]]
        )
        for admin_id in self.store.enabled_admin_ids():
            with contextlib.suppress(Exception):
                await query.bot.send_message(admin_id, text, reply_markup=markup)

    async def notify_admins_handoff_closed(self, message: Message, conversation: dict[str, Any]) -> None:
        if not message.bot or not message.from_user:
            return
        display_name = self.store.get_display_name_for_user(int(message.from_user.id), user_full_name(message))
        text = f"Telegram ID {conversation['telegram_user_id']} 已由用戶結束：{html_escape(display_name)}"
        for admin_id in self.store.enabled_admin_ids():
            with contextlib.suppress(Exception):
                await message.bot.send_message(admin_id, text)

    async def notify_admins_handoff_closed_from_query(self, query: CallbackQuery, conversation: dict[str, Any]) -> None:
        if not query.bot or not query.from_user:
            return
        display_name = self.store.get_display_name_for_user(int(query.from_user.id), query.from_user.full_name)
        text = f"Telegram ID {conversation['telegram_user_id']} 已由用戶結束：{html_escape(display_name)}"
        for admin_id in self.store.enabled_admin_ids():
            with contextlib.suppress(Exception):
                await query.bot.send_message(admin_id, text)

    async def notify_admins_handoff_auto_closed(self, message: Message, conversation: dict[str, Any], topic_label: str = "") -> None:
        if not message.bot or not message.from_user:
            return
        display_name = self.store.get_display_name_for_user(int(message.from_user.id), user_full_name(message))
        topic_line = f"\n類型：{html_escape(topic_label)}" if topic_label else ""
        text = f"Telegram ID {conversation['telegram_user_id']} 人工服務已結束：{html_escape(display_name)}{topic_line}"
        for admin_id in self.store.enabled_admin_ids():
            with contextlib.suppress(Exception):
                await message.bot.send_message(admin_id, text)

    async def close_idle_handoffs_once(self, bot: Bot) -> int:
        config = self.store.get_bot_config()
        timeout_minutes = int(config.get("handoff_timeout_minutes") or 30)
        closed = self.store.close_idle_handoffs(timeout_minutes * 60)
        for conversation in closed:
            display_name = self.store.get_display_name_for_user(
                int(conversation["telegram_user_id"]),
                str(conversation.get("latest_name") or ""),
            )
            self.store.add_message(
                int(conversation["id"]),
                "bot",
                None,
                "Bot",
                "text",
                AUTO_HANDOFF_TIMEOUT_TEXT,
            )
            with contextlib.suppress(Exception):
                await bot.send_message(
                    int(conversation["telegram_user_id"]),
                    AUTO_HANDOFF_TIMEOUT_TEXT,
                    reply_markup=self.user_menu(),
                )
            admin_text = f"Telegram ID {conversation['telegram_user_id']} 因長時間未收到新訊息已自動結束：{html_escape(display_name)}"
            for admin_id in self.store.enabled_admin_ids():
                with contextlib.suppress(Exception):
                    await bot.send_message(admin_id, admin_text)
        return len(closed)

    async def idle_handoff_monitor(self, bot: Bot, stop_event: asyncio.Event, interval_seconds: int = 30) -> None:
        while not stop_event.is_set():
            with contextlib.suppress(Exception):
                await self.close_idle_handoffs_once(bot)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def cleanup_old_conversations_once(self) -> int:
        config = self.store.get_bot_config()
        retention_days = int(config.get("conversation_retention_days") or 0)
        return self.store.delete_old_conversations(retention_days)

    async def conversation_cleanup_monitor(self, stop_event: asyncio.Event, interval_seconds: int = 3600) -> None:
        while not stop_event.is_set():
            with contextlib.suppress(Exception):
                await self.cleanup_old_conversations_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def handle_admin_message(self, message: Message) -> bool:
        assert message.from_user is not None
        text = str(message.text or "").strip()
        admin_id = int(message.from_user.id)
        if self.admin_button_matches(text, ADMIN_PENDING):
            await self.send_conversation_list(message, scope="pending")
            return True
        if self.admin_button_matches(text, ADMIN_MY):
            await self.send_conversation_list(message, scope="feedback")
            return True
        if self.admin_button_matches(text, ADMIN_RECENT):
            await self.send_conversation_list(message, scope="recent")
            return True
        if text == ADMIN_RELEASE:
            current = self.store.get_admin_current_conversation(admin_id)
            if not current:
                await message.answer("當前沒有已接管會話。", reply_markup=self.admin_menu(admin_id))
                return True
            try:
                self.store.delete_current_conversation(int(current["id"]), admin_id)
                await message.answer(f"已清除當前用戶 ID {current['telegram_user_id']}。", reply_markup=self.admin_menu(admin_id))
            except ValueError as exc:
                await message.answer(str(exc), reply_markup=self.admin_menu(admin_id))
            return True
        if text.startswith("/"):
            return False
        current = self.store.get_admin_current_conversation(admin_id)
        if not current:
            await message.answer("請先在管理員端選擇並接管一個會話，再發送回覆。", reply_markup=self.admin_menu(admin_id))
            return True
        if current["claimed_by_admin_id"] is not None and int(current["claimed_by_admin_id"]) != admin_id:
            await message.answer("該會話已被其他管理員接管。", reply_markup=self.admin_menu(admin_id))
            return True
        if not text:
            await message.answer("管理員回覆目前僅支援文字。", reply_markup=self.admin_menu(admin_id))
            return True
        await self.reply_to_user_from_admin(message, current, text)
        return True

    async def reply_to_user_from_admin(self, message: Message, conversation: dict[str, Any], text: str) -> None:
        assert message.from_user is not None
        admin_id = int(message.from_user.id)
        admin_name = user_full_name(message) or str(admin_id)
        self.store.add_message(
            int(conversation["id"]),
            "admin",
            admin_id,
            admin_name,
            "text",
            text,
            message.message_id,
        )
        await message.bot.send_message(int(conversation["telegram_user_id"]), f"人工客服：\n{text}")
        await message.answer("已發送給用戶。", reply_markup=self.admin_menu(admin_id))

    async def send_conversation_list(self, message: Message, scope: str) -> None:
        assert message.from_user is not None
        admin_id = int(message.from_user.id)
        if scope == "feedback":
            await self.send_feedback_conversation_list(message)
            return
        if scope == "pending":
            await self.send_handoff_conversation_list(message, page=0)
            return
        if scope == "recent":
            await self.send_recent_handoff_history_list(message, page=0)
            return
        all_items = self.store.list_active_conversations()
        items = all_items
        if not items:
            await message.answer("暫無會話。", reply_markup=self.admin_menu(admin_id))
            return
        for item in items[:20]:
            display = item.get("latest_name") or item.get("remark_name") or str(item["telegram_user_id"])
            text = (
                f"用戶 ID：<code>{item['telegram_user_id']}</code>\n"
                f"用戶：<b>{html_escape(display)}</b>\n"
                f"Telegram ID：<code>{item['telegram_user_id']}</code>\n"
                f"最近活動：<code>{format_message_time(item['updated_at'])}</code>\n"
                f"狀態：{html_escape(item['status'])}"
            )
            buttons = [InlineKeyboardButton(text="查看歷史", callback_data=f"view:{item['id']}")]
            if item["claimed_by_admin_id"] is None or item["claimed_by_admin_id"] == admin_id:
                buttons.append(InlineKeyboardButton(text="接管", callback_data=f"claim:{item['id']}"))
            if item["claimed_by_admin_id"] == admin_id:
                buttons.append(InlineKeyboardButton(text=ADMIN_CLEAR, callback_data=f"release:{item['id']}"))
            await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons]))

    def recent_unique_user_items(self, items: list[dict[str, Any]], timestamp_key: str) -> list[dict[str, Any]]:
        cutoff = now_ts() - ADMIN_LIST_LOOKBACK_DAYS * 24 * 3600
        result: list[dict[str, Any]] = []
        seen_user_ids: set[int] = set()
        for item in items:
            try:
                timestamp = int(item.get(timestamp_key) or 0)
                user_id = int(item["telegram_user_id"])
            except (KeyError, TypeError, ValueError):
                continue
            if timestamp < cutoff or user_id in seen_user_ids:
                continue
            result.append(item)
            seen_user_ids.add(user_id)
            if len(result) >= ADMIN_LIST_USER_LIMIT:
                break
        return result

    def pending_handoff_items(self) -> list[dict[str, Any]]:
        items = self.store.list_handoff_processing_conversations()
        return self.recent_unique_user_items(items, "latest_handoff_at")

    def feedback_conversation_items(self) -> list[dict[str, Any]]:
        return self.recent_unique_user_items(self.store.list_feedback_conversations(), "latest_feedback_at")

    def recent_handoff_history_items(self) -> list[dict[str, Any]]:
        return self.store.list_recent_handoff_history_conversations(ADMIN_LIST_LOOKBACK_DAYS, ADMIN_LIST_USER_LIMIT)

    def clamp_admin_page(self, page: int, total: int) -> int:
        if total <= 0:
            return 0
        max_page = max(0, (total - 1) // ADMIN_HANDOFF_PAGE_SIZE)
        return max(0, min(int(page), max_page))

    def admin_handoff_list_view(self, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
        items = self.pending_handoff_items()
        total = len(items)
        page = self.clamp_admin_page(page, total)
        total_pages = max(1, (total + ADMIN_HANDOFF_PAGE_SIZE - 1) // ADMIN_HANDOFF_PAGE_SIZE)
        start = page * ADMIN_HANDOFF_PAGE_SIZE
        page_items = items[start : start + ADMIN_HANDOFF_PAGE_SIZE]
        if not page_items:
            return (
                "人工服务处理\n\n暂无待处理会话。",
                InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="刷新", callback_data="admin_handoff_page:0")]]),
            )

        rows = ["序 ID           用户             时间        状态", "-- ------------ ---------------- ---------- --------"]
        buttons: list[list[InlineKeyboardButton]] = []
        for index, item in enumerate(page_items, start=start + 1):
            display = item.get("latest_name") or item.get("remark_name") or str(item["telegram_user_id"])
            rows.append(
                f"{str(index).rjust(2)} "
                f"{str(item['telegram_user_id']).ljust(12)} "
                f"{fixed_width(display, 16)} "
                f"{format_short_time(item.get('latest_handoff_at') or item['updated_at'])}  "
                f"{fixed_width(item['status'], 8)}"
            )
            buttons.append(
                [
                    InlineKeyboardButton(text=admin_identity_button(item["telegram_user_id"], display), callback_data=f"admin_handoff_detail:{item['id']}:{page}"),
                    InlineKeyboardButton(text="回复", callback_data=f"admin_handoff_reply:{item['id']}:{page}"),
                    InlineKeyboardButton(text="忽略", callback_data=f"admin_handoff_ignore:{item['id']}:{page}"),
                ]
            )

        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="上一页", callback_data=f"admin_handoff_page:{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton(text="下一页", callback_data=f"admin_handoff_page:{page + 1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="刷新", callback_data=f"admin_handoff_page:{page}")])
        text = f"人工服务处理（{total}）  第 {page + 1}/{total_pages} 页\n{admin_table(rows)}"
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    async def send_handoff_conversation_list(self, message: Message, page: int = 0) -> None:
        assert message.from_user is not None
        text, markup = self.admin_handoff_list_view(page)
        await message.answer(text, reply_markup=markup)

    async def edit_handoff_conversation_list(self, query: CallbackQuery, page: int = 0) -> None:
        if not query.message:
            return
        text, markup = self.admin_handoff_list_view(page)
        await query.message.edit_text(text, reply_markup=markup)

    def admin_handoff_detail_view(self, conversation_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
        conversation = self.store.get_conversation(conversation_id)
        if not conversation:
            return (
                "该会话已经不存在。",
                InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="返回", callback_data=f"admin_handoff_page:{page}")]]),
            )
        user_id = int(conversation["telegram_user_id"])
        display = self.store.get_display_name_for_user(user_id)
        user_messages = self.store.list_recent_handoff_history_messages(conversation_id, ADMIN_LIST_LOOKBACK_DAYS, limit=10)[-3:]
        lines = [
            "人工服务处理",
            f"用户：<b>{html_escape(display)}</b>",
            f"ID：<code>{user_id}</code>",
            f"最近活动：<code>{format_message_time(conversation['updated_at'])}</code>",
            f"状态：{html_escape(conversation['status'])}",
        ]
        if user_messages:
            lines.append("\n最近用户消息：")
            for item in user_messages:
                body = item["text"] or f"[{item['message_type']}]"
                lines.append(f"[{format_message_time(item['created_at'])}] {html_escape(body)}")
        else:
            lines.append("\n暂无用户人工消息。")
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="回复", callback_data=f"admin_handoff_reply:{conversation_id}:{page}")],
                [InlineKeyboardButton(text="查看历史", callback_data=f"view:{conversation_id}")],
                [InlineKeyboardButton(text="忽略", callback_data=f"admin_handoff_ignore:{conversation_id}:{page}")],
                [InlineKeyboardButton(text="返回", callback_data=f"admin_handoff_page:{page}")],
            ]
        )
        return "\n".join(lines), markup

    async def admin_handoff_page_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not self.store.is_authorized_admin(int(query.from_user.id)):
            await query.answer("未授权", show_alert=True)
            return
        page = int(str(query.data or "").split(":", 1)[1] or 0)
        await query.answer()
        await self.edit_handoff_conversation_list(query, page)

    async def admin_handoff_detail_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not self.store.is_authorized_admin(int(query.from_user.id)):
            await query.answer("未授权", show_alert=True)
            return
        _, conversation_id, page = str(query.data or "").split(":", 2)
        text, markup = self.admin_handoff_detail_view(int(conversation_id), int(page))
        await query.answer()
        if query.message:
            await query.message.edit_text(text, reply_markup=markup)

    async def admin_handoff_ignore_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        admin_id = int(query.from_user.id)
        if not self.store.is_authorized_admin(admin_id):
            await query.answer("未授权", show_alert=True)
            return
        _, conversation_id, page = str(query.data or "").split(":", 2)
        conversation = self.store.get_conversation(int(conversation_id))
        if not conversation:
            await query.answer("该会话已经不存在。", show_alert=True)
            return
        was_active_handoff = str(conversation["status"]).startswith("handoff")
        if was_active_handoff:
            try:
                conversation = self.store.ignore_handoff_conversation(int(conversation_id), admin_id)
            except ValueError as exc:
                await query.answer(str(exc), show_alert=True)
                return
        reviewed = self.store.mark_handoff_messages_reviewed(int(conversation_id))
        config = self.store.get_bot_config()
        user_id = int(conversation["telegram_user_id"])
        if was_active_handoff:
            self.store.add_message(int(conversation["id"]), "bot", None, "Bot", "text", str(config["handoff_close_text"]))
            with contextlib.suppress(Exception):
                await query.bot.send_message(user_id, str(config["handoff_close_text"]), reply_markup=self.user_menu())
        await query.answer("已忽略" if reviewed else "暂无未处理人工消息")
        await self.edit_handoff_conversation_list(query, int(page))

    async def admin_handoff_reply_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        admin_id = int(query.from_user.id)
        if not self.store.is_authorized_admin(admin_id):
            await query.answer("未授权", show_alert=True)
            return
        _, conversation_id, page = str(query.data or "").split(":", 2)
        try:
            existing = self.store.get_conversation(int(conversation_id))
            if existing and str(existing["status"]).startswith("handoff"):
                conversation = self.store.claim_conversation(int(conversation_id), admin_id)
            else:
                conversation = self.store.set_admin_current_conversation(int(conversation_id), admin_id)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        display = self.store.get_display_name_for_user(int(conversation["telegram_user_id"]))
        text = (
            f"正在回复 ID <code>{conversation['telegram_user_id']}</code>\n"
            f"用户：<b>{html_escape(display)}</b>\n\n"
            "请直接发送文字，Bot 会转发给该用户。"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="查看历史", callback_data=f"view:{conversation['id']}")],
                [InlineKeyboardButton(text="忽略", callback_data=f"admin_handoff_ignore:{conversation['id']}:{page}")],
                [InlineKeyboardButton(text="返回", callback_data=f"admin_handoff_detail:{conversation['id']}:{page}")],
            ]
        )
        await query.answer("已进入回复")
        await query.message.edit_text(text, reply_markup=markup)

    async def admin_handoff_back_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not self.store.is_authorized_admin(int(query.from_user.id)):
            await query.answer("未授权", show_alert=True)
            return
        page = int(str(query.data or "").split(":", 1)[1] or 0)
        await query.answer()
        await self.edit_handoff_conversation_list(query, page)

    async def send_feedback_conversation_list(self, message: Message) -> None:
        text, markup = self.admin_feedback_list_view(page=0)
        await message.answer(text, reply_markup=markup)

    def admin_feedback_list_view(self, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
        items = self.feedback_conversation_items()
        total = len(items)
        page = self.clamp_admin_page(page, total)
        total_pages = max(1, (total + ADMIN_HANDOFF_PAGE_SIZE - 1) // ADMIN_HANDOFF_PAGE_SIZE)
        start = page * ADMIN_HANDOFF_PAGE_SIZE
        page_items = items[start : start + ADMIN_HANDOFF_PAGE_SIZE]
        if not page_items:
            return (
                "建议反馈处理\n\n暂无待处理建议反馈。",
                InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="刷新", callback_data="admin_feedback_page:0")]]),
            )

        rows = ["序 ID           用户             数  时间", "-- ------------ ---------------- -- ----------"]
        buttons: list[list[InlineKeyboardButton]] = []
        for index, item in enumerate(page_items, start=start + 1):
            display = item.get("latest_name") or item.get("remark_name") or str(item["telegram_user_id"])
            count = int(item.get("feedback_message_count") or 0)
            rows.append(
                f"{str(index).rjust(2)} "
                f"{str(item['telegram_user_id']).ljust(12)} "
                f"{fixed_width(display, 16)} "
                f"{str(count).rjust(2)}  "
                f"{format_short_time(item['latest_feedback_at'])}"
            )
            buttons.append(
                [
                    InlineKeyboardButton(text=admin_identity_button(item["telegram_user_id"], display), callback_data=f"admin_feedback_detail:{item['id']}:{page}"),
                    InlineKeyboardButton(text="回复", callback_data=f"admin_feedback_reply:{item['id']}:{page}"),
                    InlineKeyboardButton(text="忽略", callback_data=f"admin_feedback_ignore:{item['id']}:{page}"),
                ]
            )

        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="上一页", callback_data=f"admin_feedback_page:{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton(text="下一页", callback_data=f"admin_feedback_page:{page + 1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="刷新", callback_data=f"admin_feedback_page:{page}")])
        text = f"建议反馈处理（{total}）  第 {page + 1}/{total_pages} 页\n{admin_table(rows)}"
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    async def edit_feedback_conversation_list(self, query: CallbackQuery, page: int = 0) -> None:
        if not query.message:
            return
        text, markup = self.admin_feedback_list_view(page)
        await query.message.edit_text(text, reply_markup=markup)

    def admin_feedback_detail_view(self, conversation_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
        conversation = self.store.get_conversation(conversation_id)
        if not conversation:
            return (
                "该建议反馈已经不存在。",
                InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="返回", callback_data=f"admin_feedback_page:{page}")]]),
            )
        user_id = int(conversation["telegram_user_id"])
        display = self.store.get_display_name_for_user(user_id)
        messages = self.store.list_feedback_messages(conversation_id, limit=50)[:20]
        lines = [
            "建议反馈处理",
            f"用户：<b>{html_escape(display)}</b>",
            f"ID：<code>{user_id}</code>",
            f"最近活动：<code>{format_message_time(conversation['updated_at'])}</code>",
        ]
        if messages:
            lines.append("\n反馈内容：")
            for item in messages:
                body = item["text"] or f"[{item['message_type']}]"
                lines.append(f"[{format_message_time(item['created_at'])}] {html_escape(body)}")
        else:
            lines.append("\n暂无未处理建议反馈。")
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="回复", callback_data=f"admin_feedback_reply:{conversation_id}:{page}")],
                [InlineKeyboardButton(text="忽略", callback_data=f"admin_feedback_ignore:{conversation_id}:{page}")],
                [InlineKeyboardButton(text="返回", callback_data=f"admin_feedback_page:{page}")],
            ]
        )
        return "\n".join(lines), markup

    async def admin_feedback_page_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not self.store.is_authorized_admin(int(query.from_user.id)):
            await query.answer("未授权", show_alert=True)
            return
        page = int(str(query.data or "").split(":", 1)[1] or 0)
        await query.answer()
        await self.edit_feedback_conversation_list(query, page)

    async def admin_feedback_detail_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not self.store.is_authorized_admin(int(query.from_user.id)):
            await query.answer("未授权", show_alert=True)
            return
        _, conversation_id, page = str(query.data or "").split(":", 2)
        text, markup = self.admin_feedback_detail_view(int(conversation_id), int(page))
        await query.answer()
        if query.message:
            await query.message.edit_text(text, reply_markup=markup)

    async def admin_feedback_reply_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        admin_id = int(query.from_user.id)
        if not self.store.is_authorized_admin(admin_id):
            await query.answer("未授权", show_alert=True)
            return
        _, conversation_id, page = str(query.data or "").split(":", 2)
        try:
            conversation = self.store.set_admin_current_conversation(int(conversation_id), admin_id)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        display = self.store.get_display_name_for_user(int(conversation["telegram_user_id"]))
        text = (
            f"正在回复建议反馈 ID <code>{conversation['telegram_user_id']}</code>\n"
            f"用户：<b>{html_escape(display)}</b>\n\n"
            "请直接发送文字，Bot 会转发给该用户。\n"
            "发送后还需要点击“忽略”，该反馈才会从待处理中移除。"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="忽略", callback_data=f"admin_feedback_ignore:{conversation['id']}:{page}")],
                [InlineKeyboardButton(text="返回", callback_data=f"admin_feedback_detail:{conversation['id']}:{page}")],
            ]
        )
        await query.answer("已进入回复")
        await query.message.edit_text(text, reply_markup=markup)

    async def admin_feedback_ignore_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        admin_id = int(query.from_user.id)
        if not self.store.is_authorized_admin(admin_id):
            await query.answer("未授权", show_alert=True)
            return
        _, conversation_id, page = str(query.data or "").split(":", 2)
        reviewed = self.store.mark_feedback_messages_reviewed(int(conversation_id))
        await query.answer("已忽略" if reviewed else "暂无未处理反馈")
        await self.edit_feedback_conversation_list(query, int(page))

    def admin_recent_handoff_history_list_view(self, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
        items = self.recent_handoff_history_items()
        total = len(items)
        page = self.clamp_admin_page(page, total)
        total_pages = max(1, (total + ADMIN_HANDOFF_PAGE_SIZE - 1) // ADMIN_HANDOFF_PAGE_SIZE)
        start = page * ADMIN_HANDOFF_PAGE_SIZE
        page_items = items[start : start + ADMIN_HANDOFF_PAGE_SIZE]
        if not page_items:
            return (
                "最近会话记录\n\n最近 7 天内暂无人工服务聊天记录。",
                InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="刷新", callback_data="admin_recent_page:0")]]),
            )

        rows = ["序 ID           用户             数  时间", "-- ------------ ---------------- -- ----------"]
        buttons: list[list[InlineKeyboardButton]] = []
        for index, item in enumerate(page_items, start=start + 1):
            display = item.get("latest_name") or item.get("remark_name") or str(item["telegram_user_id"])
            count = int(item.get("handoff_message_count") or 0)
            rows.append(
                f"{str(index).rjust(2)} "
                f"{str(item['telegram_user_id']).ljust(12)} "
                f"{fixed_width(display, 16)} "
                f"{str(count).rjust(2)}  "
                f"{format_short_time(item['latest_handoff_at'])}"
            )
            buttons.append(
                [
                    InlineKeyboardButton(text=admin_identity_button(item["telegram_user_id"], display), callback_data=f"admin_recent_detail:{item['id']}:{page}"),
                ]
            )

        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="上一页", callback_data=f"admin_recent_page:{page - 1}"))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton(text="下一页", callback_data=f"admin_recent_page:{page + 1}"))
        if nav:
            buttons.append(nav)
        buttons.append([InlineKeyboardButton(text="刷新", callback_data=f"admin_recent_page:{page}")])
        text = f"最近会话记录（最近 7 天，最多 10 个用户）  第 {page + 1}/{total_pages} 页\n{admin_table(rows)}"
        return text, InlineKeyboardMarkup(inline_keyboard=buttons)

    async def send_recent_handoff_history_list(self, message: Message, page: int = 0) -> None:
        text, markup = self.admin_recent_handoff_history_list_view(page)
        await message.answer(text, reply_markup=markup)

    async def edit_recent_handoff_history_list(self, query: CallbackQuery, page: int = 0) -> None:
        if not query.message:
            return
        text, markup = self.admin_recent_handoff_history_list_view(page)
        await query.message.edit_text(text, reply_markup=markup)

    def admin_recent_handoff_history_detail_view(self, conversation_id: int, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
        conversation = self.store.get_conversation(conversation_id)
        if not conversation:
            return (
                "该会话记录已经不存在。",
                InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="返回", callback_data=f"admin_recent_page:{page}")]]),
            )
        user_id = int(conversation["telegram_user_id"])
        display = self.store.get_display_name_for_user(user_id)
        messages = self.store.list_recent_handoff_history_messages(conversation_id, ADMIN_LIST_LOOKBACK_DAYS, limit=50)[-20:]
        lines = [
            "最近会话记录",
            f"用户：<b>{html_escape(display)}</b>",
            f"ID：<code>{user_id}</code>",
            "最近 7 天人工服务消息：",
        ]
        if messages:
            for item in messages:
                body = item["text"] or f"[{item['message_type']}]"
                lines.append(f"[{format_message_time(item['created_at'])}] {html_escape(body)}")
        else:
            lines.append("暂无人工服务聊天信息。")
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="返回", callback_data=f"admin_recent_page:{page}")],
            ]
        )
        return "\n".join(lines), markup

    async def admin_recent_page_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not self.store.is_authorized_admin(int(query.from_user.id)):
            await query.answer("未授权", show_alert=True)
            return
        page = int(str(query.data or "").split(":", 1)[1] or 0)
        await query.answer()
        await self.edit_recent_handoff_history_list(query, page)

    async def admin_recent_detail_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not self.store.is_authorized_admin(int(query.from_user.id)):
            await query.answer("未授权", show_alert=True)
            return
        _, conversation_id, page = str(query.data or "").split(":", 2)
        text, markup = self.admin_recent_handoff_history_detail_view(int(conversation_id), int(page))
        await query.answer()
        if query.message:
            await query.message.edit_text(text, reply_markup=markup)

    async def send_feedback_conversation_cards(self, message: Message) -> None:
        assert message.from_user is not None
        admin_id = int(message.from_user.id)
        items = self.store.list_feedback_conversations()
        if not items:
            await message.answer("暂无建议反馈。", reply_markup=self.admin_menu(admin_id))
            return
        for item in items[:20]:
            display = item.get("latest_name") or item.get("remark_name") or str(item["telegram_user_id"])
            count = int(item.get("feedback_message_count") or 0)
            text = (
                f"建议反馈 ID <code>{item['telegram_user_id']}</code>\n"
                f"用户：<b>{html_escape(display)}</b>\n"
                f"Telegram ID：<code>{item['telegram_user_id']}</code>\n"
                f"反馈消息：<code>{count}</code>\n"
                f"最近反馈：<code>{format_message_time(item['latest_feedback_at'])}</code>"
            )
            buttons = [InlineKeyboardButton(text="查看歷史", callback_data=f"view_feedback:{item['id']}")]
            await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[buttons]))

    async def claim_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        admin_id = int(query.from_user.id)
        if not self.store.is_authorized_admin(admin_id):
            await query.answer("未授权", show_alert=True)
            return
        conversation_id = int(str(query.data or "").split(":", 1)[1])
        try:
            conversation = self.store.claim_conversation(conversation_id, admin_id)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer("已接管")
        await query.message.answer(f"已接管用戶 ID {conversation['telegram_user_id']}，現在發送文字即可回覆該用戶。", reply_markup=self.admin_menu(admin_id))
        await self.send_history(query.message, conversation_id)

    async def view_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        if not self.store.is_authorized_admin(int(query.from_user.id)):
            await query.answer("未授权", show_alert=True)
            return
        conversation_id = int(str(query.data or "").split(":", 1)[1])
        await query.answer("正在載入")
        await self.send_history(query.message, conversation_id)

    async def view_feedback_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        if not self.store.is_authorized_admin(int(query.from_user.id)):
            await query.answer("未授权", show_alert=True)
            return
        conversation_id = int(str(query.data or "").split(":", 1)[1])
        await query.answer("正在載入")
        await self.send_feedback_history(query.message, conversation_id)

    async def release_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        admin_id = int(query.from_user.id)
        conversation_id = int(str(query.data or "").split(":", 1)[1])
        conversation = self.store.get_conversation(conversation_id)
        user_label = conversation["telegram_user_id"] if conversation else conversation_id
        try:
            self.store.delete_current_conversation(conversation_id, admin_id)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer("已清除")
        await query.message.answer(f"已清除當前用戶 ID {user_label}。", reply_markup=self.admin_menu(admin_id))

    async def send_history(self, message: Message, conversation_id: int) -> None:
        messages = [
            item
            for item in self.store.list_messages(conversation_id, limit=50)
            if item["direction"] == "user" and int(item["forwarded_to_admins"]) == 1
        ][:20]
        if not messages:
            await message.answer("暫無用戶人工訊息。")
            return
        conversation = self.store.get_conversation(conversation_id)
        user_label = conversation["telegram_user_id"] if conversation else conversation_id
        lines = [f"用戶 ID {user_label} 最近用戶訊息："]
        for item in messages:
            sender = item["sender_display_name"] or item["direction"]
            body = item["text"] or f"[{item['message_type']}]"
            lines.append(f"[{format_message_time(item['created_at'])}] {sender}: {body}")
        await message.answer(html_escape("\n".join(lines)))

    async def send_feedback_history(self, message: Message, conversation_id: int) -> None:
        messages = self.store.list_feedback_messages(conversation_id, limit=50)[:20]
        if not messages:
            await message.answer("暫無建議反饋訊息。")
            return
        conversation = self.store.get_conversation(conversation_id)
        user_label = conversation["telegram_user_id"] if conversation else conversation_id
        lines = [f"建议反馈 ID {user_label} 最近用户消息："]
        for item in messages:
            sender = item["sender_display_name"] or item["direction"]
            body = item["text"] or f"[{item['message_type']}]"
            lines.append(f"[{format_message_time(item['created_at'])}] {sender}: {body}")
        await message.answer(html_escape("\n".join(lines)))
