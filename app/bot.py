from __future__ import annotations

import contextlib
import asyncio
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
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    Update,
)

from .service import CustomerServiceStore
from .defaults import (
    AUTO_HANDOFF_TIMEOUT_TEXT,
    FEEDBACK_BUTTON_TEXT,
    FEEDBACK_PROMPT_TEXT,
    FEEDBACK_THANKS_TEXT,
    OTHER_BUTTON_TEXT,
    OTHER_ACK_TEXT,
    OTHER_HANDOFF_TEXT,
    PAYMENT_AFTER_INPUT_TEXT,
    PAYMENT_BUTTON_TEXT,
    PAYMENT_HANDOFF_TEXT,
    PAYMENT_LINK_URL,
    TOPIC_HANDOFF_NOTICE_TEXT,
)


ADMIN_PENDING = "待處理會話"
ADMIN_MY = "我的會話"
ADMIN_ALL = "全部會話"
ADMIN_CLEAR = "清除當前會話"
ADMIN_RELEASE = ADMIN_CLEAR


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


def html_escape(text: Any) -> str:
    return html.escape(str(text or ""), quote=False)


class TelegramCustomerBot:
    def __init__(self, store: CustomerServiceStore) -> None:
        self.store = store
        self.dispatcher = Dispatcher()
        self.router = Router()
        self.router.message(CommandStart())(self.start)
        self.router.message(Command("admin"))(self.admin_home)
        self.router.callback_query(F.data == "user:handoff:start")(self.user_handoff_start_callback)
        self.router.callback_query(F.data == "user:handoff:end")(self.user_handoff_end_callback)
        self.router.callback_query(F.data.startswith("user:topic:"))(self.user_topic_callback)
        self.router.callback_query(F.data.startswith("user:preset:"))(self.user_preset_callback)
        self.router.callback_query(F.data.startswith("claim:"))(self.claim_callback)
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

    async def feed_update(self, update_payload: dict[str, Any]) -> None:
        bot = self.make_bot()
        try:
            update = Update.model_validate(update_payload, context={"bot": bot})
            await self.dispatcher.feed_update(bot, update)
        finally:
            await bot.session.close()

    def user_menu(self) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = [
            [InlineKeyboardButton(text=PAYMENT_BUTTON_TEXT, callback_data="user:topic:payment")],
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

    def admin_menu(self):
        from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text=ADMIN_PENDING), KeyboardButton(text=ADMIN_MY)],
                [KeyboardButton(text=ADMIN_ALL), KeyboardButton(text=ADMIN_RELEASE)],
            ],
            resize_keyboard=True,
        )

    async def start(self, message: Message) -> None:
        if not message.from_user:
            return
        user_id = int(message.from_user.id)
        config = self.store.get_bot_config()
        if self.store.is_authorized_user(user_id):
            self.store.update_user_seen(user_id, user_full_name(message), username(message))
            self.store.get_or_create_conversation(user_id)
            await self.remove_reply_keyboard(message)
            await message.answer(str(config["welcome_text"]), reply_markup=self.user_menu())
            return
        if self.store.is_authorized_admin(user_id):
            self.store.update_admin_seen(user_id, user_full_name(message), username(message))
            await message.answer("您是已授權管理員，請發送 /admin 進入人工端。", reply_markup=self.admin_menu())
            return
        await message.answer(str(config["unauthorized_text"]), reply_markup=ReplyKeyboardRemove())

    async def admin_home(self, message: Message) -> None:
        if not message.from_user:
            return
        admin_id = int(message.from_user.id)
        if not self.store.is_authorized_admin(admin_id):
            await message.answer("當前 Telegram ID 未授權使用管理員端。")
            return
        self.store.update_admin_seen(admin_id, user_full_name(message), username(message))
        await message.answer("管理員端已開啟。請選擇要查看或接管的會話。", reply_markup=self.admin_menu())
        await self.send_conversation_list(message, scope="pending")

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
        if str(conversation["status"]) == "handoff_other_waiting":
            await self.handle_other_input_message(message, conversation)
            return
        if str(conversation["status"]).startswith("handoff"):
            await self.record_and_forward_user_message(message, conversation)
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
        last_bot_text = self.store.get_last_bot_text(conversation["id"])
        fallback_text = last_bot_text or str(config["welcome_text"])
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", fallback_text)
        await message.answer(fallback_text, reply_markup=self.user_menu())

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
            await self.open_topic_handoff_from_query(query, OTHER_HANDOFF_TEXT, "handoff_other_waiting", OTHER_BUTTON_TEXT)
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
            await self.open_topic_handoff_from_query(query, OTHER_HANDOFF_TEXT, "handoff_other_waiting", OTHER_BUTTON_TEXT)
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
        user_notice = f"{TOPIC_HANDOFF_NOTICE_TEXT}\n\n{prompt}"
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", user_notice)
        await query.answer("已转接")
        await query.message.answer(user_notice, reply_markup=self.handoff_menu())
        await self.notify_admins_handoff_open_from_query(query, conversation, topic_label)

    async def handle_payment_input_message(self, message: Message, conversation: dict[str, Any]) -> None:
        await self.record_and_forward_user_message(message, conversation)
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", PAYMENT_AFTER_INPUT_TEXT)
        await message.answer(PAYMENT_AFTER_INPUT_TEXT, reply_markup=self.payment_handoff_menu())

    async def handle_other_input_message(self, message: Message, conversation: dict[str, Any]) -> None:
        await self.record_and_forward_user_message(message, conversation)
        self.store.add_message(conversation["id"], "bot", None, "Bot", "text", OTHER_ACK_TEXT)
        await message.answer(OTHER_ACK_TEXT, reply_markup=self.handoff_menu())

    async def handle_feedback_message(self, message: Message, conversation: dict[str, Any]) -> None:
        assert message.from_user is not None
        user_id = int(message.from_user.id)
        display_name = self.store.get_display_name_for_user(user_id, user_full_name(message))
        msg_type, file_id = message_type_and_file_id(message)
        self.store.add_message(
            conversation["id"],
            "user",
            user_id,
            display_name,
            msg_type,
            display_message_text(message),
            message.message_id,
            file_id,
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
        self.store.add_message(
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
        await self.forward_to_admins(message, conversation, display_name, msg_type, text)

    async def forward_to_admins(
        self,
        message: Message,
        conversation: dict[str, Any],
        display_name: str,
        msg_type: str,
        text: str,
    ) -> None:
        if not message.bot:
            return
        prefix = (
            f"用戶：<b>{html_escape(display_name)}</b>\n"
            f"Telegram ID：<code>{conversation['telegram_user_id']}</code>\n"
            f"會話：<code>#{conversation['id']}</code>\n"
            f"類型：{html_escape(msg_type)}"
        )
        if msg_type == "text" and text:
            prefix += f"\n\n{html_escape(text)}"
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="查看歷史", callback_data=f"view:{conversation['id']}"),
                    InlineKeyboardButton(text="接管", callback_data=f"claim:{conversation['id']}"),
                ]
            ]
        )
        for admin_id in self.store.enabled_admin_ids():
            await message.bot.send_message(admin_id, prefix, reply_markup=markup)
            if msg_type != "text":
                await message.bot.copy_message(admin_id, message.chat.id, message.message_id)

    async def notify_admins_handoff_open(self, message: Message, conversation: dict[str, Any]) -> None:
        if not message.bot or not message.from_user:
            return
        display_name = self.store.get_display_name_for_user(int(message.from_user.id), user_full_name(message))
        text = (
            f"新人工會話\n"
            f"用戶：<b>{html_escape(display_name)}</b>\n"
            f"Telegram ID：<code>{conversation['telegram_user_id']}</code>\n"
            f"會話：<code>#{conversation['id']}</code>"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="接管", callback_data=f"claim:{conversation['id']}")]]
        )
        for admin_id in self.store.enabled_admin_ids():
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
            f"Telegram ID：<code>{conversation['telegram_user_id']}</code>\n"
            f"會話：<code>#{conversation['id']}</code>"
        )
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="接管", callback_data=f"claim:{conversation['id']}")]]
        )
        for admin_id in self.store.enabled_admin_ids():
            await query.bot.send_message(admin_id, text, reply_markup=markup)

    async def notify_admins_handoff_closed(self, message: Message, conversation: dict[str, Any]) -> None:
        if not message.bot or not message.from_user:
            return
        display_name = self.store.get_display_name_for_user(int(message.from_user.id), user_full_name(message))
        text = f"會話 #{conversation['id']} 已由用戶結束：{html_escape(display_name)}"
        for admin_id in self.store.enabled_admin_ids():
            await message.bot.send_message(admin_id, text)

    async def notify_admins_handoff_closed_from_query(self, query: CallbackQuery, conversation: dict[str, Any]) -> None:
        if not query.bot or not query.from_user:
            return
        display_name = self.store.get_display_name_for_user(int(query.from_user.id), query.from_user.full_name)
        text = f"會話 #{conversation['id']} 已由用戶結束：{html_escape(display_name)}"
        for admin_id in self.store.enabled_admin_ids():
            await query.bot.send_message(admin_id, text)

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
            admin_text = f"會話 #{conversation['id']} 因長時間未收到新訊息已自動結束：{html_escape(display_name)}"
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
        if text in {ADMIN_PENDING, ADMIN_MY, ADMIN_ALL}:
            await self.send_conversation_list(message, scope={ADMIN_PENDING: "pending", ADMIN_MY: "mine", ADMIN_ALL: "all"}[text])
            return True
        if text == ADMIN_RELEASE:
            current = self.store.get_admin_current_conversation(admin_id)
            if not current:
                await message.answer("當前沒有已接管會話。", reply_markup=self.admin_menu())
                return True
            try:
                self.store.release_conversation(int(current["id"]), admin_id)
                await message.answer(f"已清除當前會話 #{current['id']}。", reply_markup=self.admin_menu())
            except ValueError as exc:
                await message.answer(str(exc), reply_markup=self.admin_menu())
            return True
        if text.startswith("/"):
            return False
        current = self.store.get_admin_current_conversation(admin_id)
        if not current:
            await message.answer("請先在管理員端選擇並接管一個會話，再發送回覆。", reply_markup=self.admin_menu())
            return True
        if current["claimed_by_admin_id"] is not None and int(current["claimed_by_admin_id"]) != admin_id:
            await message.answer("該會話已被其他管理員接管。", reply_markup=self.admin_menu())
            return True
        if not text:
            await message.answer("管理員回覆目前僅支援文字。", reply_markup=self.admin_menu())
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
        await message.answer("已發送給用戶。", reply_markup=self.admin_menu())

    async def send_conversation_list(self, message: Message, scope: str) -> None:
        assert message.from_user is not None
        admin_id = int(message.from_user.id)
        all_items = self.store.list_active_conversations() if scope != "all" else self.store.list_all_conversations()
        if scope == "pending":
            items = [item for item in all_items if item["claimed_by_admin_id"] is None]
        elif scope == "mine":
            items = [item for item in all_items if item["claimed_by_admin_id"] == admin_id]
        else:
            items = all_items
        if not items:
            await message.answer("暫無會話。", reply_markup=self.admin_menu())
            return
        for item in items[:20]:
            display = item.get("latest_name") or item.get("remark_name") or str(item["telegram_user_id"])
            text = (
                f"會話 <code>#{item['id']}</code>\n"
                f"用戶：<b>{html_escape(display)}</b>\n"
                f"Telegram ID：<code>{item['telegram_user_id']}</code>\n"
                f"狀態：{html_escape(item['status'])}"
            )
            buttons = [InlineKeyboardButton(text="查看歷史", callback_data=f"view:{item['id']}")]
            if item["claimed_by_admin_id"] is None or item["claimed_by_admin_id"] == admin_id:
                buttons.append(InlineKeyboardButton(text="接管", callback_data=f"claim:{item['id']}"))
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
        await query.message.answer(f"已接管會話 #{conversation['id']}，現在發送文字即可回覆該用戶。", reply_markup=self.admin_menu())
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

    async def release_callback(self, query: CallbackQuery) -> None:
        if not query.from_user or not query.message:
            return
        admin_id = int(query.from_user.id)
        conversation_id = int(str(query.data or "").split(":", 1)[1])
        try:
            self.store.release_conversation(conversation_id, admin_id)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await query.answer("已清除")
        await query.message.answer(f"已清除當前會話 #{conversation_id}。", reply_markup=self.admin_menu())

    async def send_history(self, message: Message, conversation_id: int) -> None:
        messages = [
            item
            for item in self.store.list_messages(conversation_id, limit=50)
            if item["direction"] == "user" and item["message_type"] != "callback"
        ][:20]
        if not messages:
            await message.answer("暫無用戶人工訊息。")
            return
        lines = [f"會話 #{conversation_id} 最近用戶訊息："]
        for item in messages:
            sender = item["sender_display_name"] or item["direction"]
            body = item["text"] or f"[{item['message_type']}]"
            lines.append(f"{sender}: {body}")
        await message.answer(html_escape("\n".join(lines)))
