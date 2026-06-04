from __future__ import annotations

import asyncio

from app.bot import ADMIN_PENDING, ADMIN_RELEASE, TelegramCustomerBot
from app.db import db, init_db, now_ts
from app.defaults import (
    AUTO_HANDOFF_TIMEOUT_TEXT,
    FEEDBACK_BUTTON_TEXT,
    FEEDBACK_PROMPT_TEXT,
    FEEDBACK_THANKS_TEXT,
    OTHER_ACK_TEXT,
    OTHER_BUTTON_TEXT,
    OTHER_HANDOFF_TEXT,
    PAYMENT_AFTER_INPUT_TEXT,
    PAYMENT_BUTTON_TEXT,
    PAYMENT_HANDOFF_TEXT,
    PAYMENT_LINK_URL,
    TOPIC_HANDOFF_NOTICE_TEXT,
)
from app.service import CustomerServiceStore


USER_ID = 1001
ADMIN_ID = 9001


class FakeUser:
    def __init__(self, user_id: int, first_name: str, username: str = "") -> None:
        self.id = user_id
        self.first_name = first_name
        self.last_name = ""
        self.username = username
        self.full_name = first_name


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.copied: list[dict] = []
        self.commands: list[dict] = []
        self.deleted_commands: list[dict] = []
        self.default_commands = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return FakeSentMessage()

    async def copy_message(self, chat_id, from_chat_id, message_id):
        self.copied.append({"chat_id": chat_id, "from_chat_id": from_chat_id, "message_id": message_id})

    async def set_my_commands(self, commands, scope=None):
        self.commands.append({"commands": commands, "scope": scope})

    async def get_my_commands(self, scope=None):
        return self.default_commands

    async def delete_my_commands(self, scope=None):
        self.deleted_commands.append({"scope": scope})


class FakeSentMessage:
    async def delete(self):
        return None


class FakeChat:
    def __init__(self, chat_id: int) -> None:
        self.id = chat_id


class FakeMessage:
    def __init__(
        self,
        from_user: FakeUser,
        bot: FakeBot,
        text: str = "",
        message_id: int = 1,
    ) -> None:
        self.from_user = from_user
        self.bot = bot
        self.text = text
        self.caption = ""
        self.message_id = message_id
        self.chat = FakeChat(from_user.id)
        self.photo = None
        self.voice = None
        self.document = None
        self.video = None
        self.audio = None
        self.sticker = None
        self.answers: list[dict] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append({"text": text, "reply_markup": reply_markup})
        return FakeSentMessage()


class FakeQuery:
    def __init__(self, data: str, from_user: FakeUser, message: FakeMessage, bot: FakeBot) -> None:
        self.data = data
        self.from_user = from_user
        self.message = message
        self.bot = bot
        self.answers: list[dict] = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append({"text": text, "show_alert": show_alert})


def setup_bot(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "bot.db"))
    init_db()
    store = CustomerServiceStore()
    store.upsert_telegram_user(USER_ID, "后台备注", True)
    store.upsert_telegram_admin(ADMIN_ID, "人工客服", True)
    return TelegramCustomerBot(store), store


def topic_callback_id(text: str) -> str:
    return {
        PAYMENT_BUTTON_TEXT: "user:topic:payment",
        FEEDBACK_BUTTON_TEXT: "user:topic:feedback",
        OTHER_BUTTON_TEXT: "user:topic:other",
    }[text]


def reply_keyboard_labels(markup) -> list[str]:
    return [button.text for row in markup.keyboard for button in row]


def test_user_menu_only_shows_three_topic_buttons(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)

    labels = [row[0].text for row in bot.user_menu().inline_keyboard]

    assert labels == [PAYMENT_BUTTON_TEXT, FEEDBACK_BUTTON_TEXT, OTHER_BUTTON_TEXT]
    assert store.get_bot_config()["handoff_button_text"] not in labels


def test_bot_commands_include_admin_for_enabled_admins(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    fake_bot.default_commands = [
        type("Command", (), {"command": "start", "description": "开始"})(),
        type("Command", (), {"command": "help", "description": "帮助"})(),
    ]

    asyncio.run(bot.setup_bot_commands(fake_bot))
    asyncio.run(bot.set_admin_commands(fake_bot, ADMIN_ID, enabled=False))

    assert len(fake_bot.commands) == 3
    default_commands = [item.command for item in fake_bot.commands[0]["commands"]]
    user_scope = fake_bot.commands[1]
    user_commands = [item.command for item in user_scope["commands"]]
    admin_scope = fake_bot.commands[2]
    admin_commands = [item.command for item in admin_scope["commands"]]

    assert default_commands == ["start", "help", "payment", "feedback", "other", "end"]
    assert user_commands == ["start", "help", "payment", "feedback", "other", "end"]
    assert getattr(user_scope["scope"], "chat_id", None) == USER_ID
    assert admin_commands == ["start", "help", "payment", "feedback", "other", "end", "admin"]
    assert getattr(admin_scope["scope"], "chat_id", None) == ADMIN_ID
    assert getattr(fake_bot.deleted_commands[-1]["scope"], "chat_id", None) == ADMIN_ID


def test_payment_command_enters_handoff(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户", "tg_user")
    message = FakeMessage(user, fake_bot, "/payment", message_id=21)

    asyncio.run(bot.payment_command(message))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "handoff_payment_waiting"
    assert message.answers[-1]["text"].startswith(TOPIC_HANDOFF_NOTICE_TEXT)
    assert PAYMENT_HANDOFF_TEXT in message.answers[-1]["text"]
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert PAYMENT_BUTTON_TEXT in fake_bot.sent[-1]["text"]


def test_payment_and_other_buttons_prompt_then_auto_reply_after_first_input(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户", "tg_user")

    cases = (
        (PAYMENT_BUTTON_TEXT, PAYMENT_HANDOFF_TEXT, "handoff_payment_waiting", PAYMENT_AFTER_INPUT_TEXT),
        (OTHER_BUTTON_TEXT, OTHER_HANDOFF_TEXT, "handoff_other_waiting", OTHER_ACK_TEXT),
    )
    for button_text, expected_prompt, expected_status, expected_after_input in cases:
        message = FakeMessage(user, fake_bot)
        query = FakeQuery(topic_callback_id(button_text), user, message, fake_bot)

        asyncio.run(bot.user_topic_callback(query))

        conversation = store.get_or_create_conversation(USER_ID)
        assert conversation["status"] == expected_status
        assert message.answers[-1]["text"].startswith(TOPIC_HANDOFF_NOTICE_TEXT)
        assert expected_prompt in message.answers[-1]["text"]
        assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
        assert "新人工會話" in fake_bot.sent[-1]["text"]
        assert button_text in fake_bot.sent[-1]["text"]
        assert "Telegram 用户" in fake_bot.sent[-1]["text"]
        sent_count = len(fake_bot.sent)

        user_message = FakeMessage(user, fake_bot, "用户输入内容", message_id=22)
        asyncio.run(bot.handle_user_message(user_message))

        conversation = store.get_or_create_conversation(USER_ID)
        assert conversation["status"] == expected_status
        assert user_message.answers[-1]["text"] == expected_after_input
        assert len(fake_bot.sent) == sent_count + 1
        assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
        assert "用户输入内容" in fake_bot.sent[-1]["text"]
        assert expected_after_input not in fake_bot.sent[-1]["text"]

        if button_text == PAYMENT_BUTTON_TEXT:
            inline_keyboard = user_message.answers[-1]["reply_markup"].inline_keyboard
            assert inline_keyboard[0][0].url == PAYMENT_LINK_URL

        second_user_message = FakeMessage(user, fake_bot, "second input", message_id=23)
        asyncio.run(bot.handle_user_message(second_user_message))

        conversation = store.get_or_create_conversation(USER_ID)
        assert conversation["status"] == expected_status
        assert second_user_message.answers[-1]["text"] == expected_after_input
        assert expected_after_input not in fake_bot.sent[-1]["text"]

        store.close_handoff(USER_ID)


def test_feedback_button_collects_one_message_without_admin_forward(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户")
    query_message = FakeMessage(user, fake_bot)
    query = FakeQuery(topic_callback_id(FEEDBACK_BUTTON_TEXT), user, query_message, fake_bot)

    asyncio.run(bot.user_topic_callback(query))

    assert store.get_or_create_conversation(USER_ID)["status"] == "feedback_waiting"
    assert query_message.answers[-1]["text"] == FEEDBACK_PROMPT_TEXT

    user_message = FakeMessage(user, fake_bot, "建议内容", message_id=22)
    asyncio.run(bot.handle_user_message(user_message))

    assert store.get_or_create_conversation(USER_ID)["status"] == "bot"
    assert user_message.answers[-1]["text"] == FEEDBACK_THANKS_TEXT
    assert fake_bot.sent == []

    follow_up = FakeMessage(user, fake_bot, "你好", message_id=23)
    asyncio.run(bot.handle_user_message(follow_up))

    assert follow_up.answers[-1]["text"] == FEEDBACK_THANKS_TEXT
    assert follow_up.answers[-1]["reply_markup"] is not None
    assert fake_bot.sent == []


def test_handoff_message_is_forwarded_with_user_name_and_admin_can_reply(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户")
    admin = FakeUser(ADMIN_ID, "管理员")
    conversation = store.open_handoff(USER_ID)
    user_message = FakeMessage(user, fake_bot, "我需要人工协助", message_id=33)

    asyncio.run(bot.handle_user_message(user_message))

    messages = store.list_messages(conversation["id"])
    assert messages[-1]["forwarded_to_admins"] == 1
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert "用戶：" in fake_bot.sent[-1]["text"]
    assert "發送時間：" in fake_bot.sent[-1]["text"]
    assert "Telegram 用户" in fake_bot.sent[-1]["text"]
    assert "我需要人工协助" in fake_bot.sent[-1]["text"]

    store.claim_conversation(conversation["id"], ADMIN_ID)
    admin_message = FakeMessage(admin, fake_bot, "已经收到，请稍等", message_id=44)
    asyncio.run(bot.handle_admin_message(admin_message))

    assert fake_bot.sent[-1]["chat_id"] == USER_ID
    assert "人工客服" in fake_bot.sent[-1]["text"]
    assert "已经收到，请稍等" in fake_bot.sent[-1]["text"]
    assert admin_message.answers[-1]["text"] == "已發送給用戶。"


def test_user_manual_handoff_start_and_end_buttons(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户")
    message = FakeMessage(user, fake_bot)

    asyncio.run(bot.user_handoff_start_callback(FakeQuery("user:handoff:start", user, message, fake_bot)))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "handoff_open"
    assert message.answers[-1]["text"] == store.get_bot_config()["handoff_open_text"]
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert "新人工會話" in fake_bot.sent[-1]["text"]

    asyncio.run(bot.user_handoff_end_callback(FakeQuery("user:handoff:end", user, message, fake_bot)))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "bot"
    assert message.answers[-1]["text"] == store.get_bot_config()["handoff_close_text"]
    assert "已由用戶結束" in fake_bot.sent[-1]["text"]


def test_admin_claim_view_and_release_buttons(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    admin = FakeUser(ADMIN_ID, "管理员")
    conversation = store.open_handoff(USER_ID)
    store.add_message(conversation["id"], "user", USER_ID, "Telegram 用户", "text", "历史消息", forwarded_to_admins=True)
    store.add_message(conversation["id"], "user", USER_ID, "Telegram 用户", "text", "非人工消息")
    store.add_message(conversation["id"], "bot", None, "Bot", "text", PAYMENT_AFTER_INPUT_TEXT)
    store.add_message(conversation["id"], "user", USER_ID, "Telegram 用户", "callback", PAYMENT_BUTTON_TEXT)
    admin_message = FakeMessage(admin, fake_bot)

    assert ADMIN_RELEASE not in reply_keyboard_labels(bot.admin_menu(ADMIN_ID))

    asyncio.run(bot.view_callback(FakeQuery(f"view:{conversation['id']}", admin, admin_message, fake_bot)))

    assert admin_message.answers[-1]["text"].startswith(f"會話 #{conversation['id']} 最近用戶訊息")
    assert "[" in admin_message.answers[-1]["text"]
    assert "历史消息" in admin_message.answers[-1]["text"]
    assert "非人工消息" not in admin_message.answers[-1]["text"]
    assert PAYMENT_AFTER_INPUT_TEXT not in admin_message.answers[-1]["text"]
    assert PAYMENT_BUTTON_TEXT not in admin_message.answers[-1]["text"]

    list_message = FakeMessage(admin, fake_bot, ADMIN_PENDING)
    asyncio.run(bot.handle_admin_message(list_message))
    assert "最近活動：" in list_message.answers[-1]["text"]

    asyncio.run(bot.claim_callback(FakeQuery(f"claim:{conversation['id']}", admin, admin_message, fake_bot)))

    claimed = store.get_conversation(conversation["id"])
    assert claimed["status"] == "handoff_claimed"
    assert claimed["claimed_by_admin_id"] == ADMIN_ID
    assert "已接管會話" in admin_message.answers[-2]["text"]
    assert ADMIN_RELEASE in reply_keyboard_labels(admin_message.answers[-2]["reply_markup"])

    release_message = FakeMessage(admin, fake_bot, ADMIN_RELEASE)
    asyncio.run(bot.handle_admin_message(release_message))

    assert store.get_conversation(conversation["id"]) is None
    assert store.list_messages(conversation["id"]) == []
    assert store.get_admin_current_conversation(ADMIN_ID) is None
    assert "已清除當前會話" in release_message.answers[-1]["text"]
    assert ADMIN_RELEASE not in reply_keyboard_labels(release_message.answers[-1]["reply_markup"])


def test_idle_handoff_timeout_notifies_user_and_admin(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    conversation = store.open_handoff(USER_ID)
    old_ts = now_ts() - 3600
    with db() as conn:
        conn.execute("UPDATE bot_config SET handoff_timeout_minutes = 30 WHERE id = 1")
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (old_ts, conversation["id"]))

    closed_count = asyncio.run(bot.close_idle_handoffs_once(fake_bot))

    assert closed_count == 1
    assert store.get_conversation(conversation["id"])["status"] == "bot"
    assert fake_bot.sent[0]["chat_id"] == USER_ID
    assert fake_bot.sent[0]["text"] == AUTO_HANDOFF_TIMEOUT_TEXT
    assert fake_bot.sent[0]["reply_markup"] is not None
    assert fake_bot.sent[1]["chat_id"] == ADMIN_ID
    assert "自動結束" in fake_bot.sent[1]["text"]
