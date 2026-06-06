from __future__ import annotations

import asyncio

from app.bot import ADMIN_MY, ADMIN_PENDING, ADMIN_RECENT, ADMIN_RELEASE, TelegramCustomerBot
from app.db import db, init_db, now_ts
from app.defaults import (
    AUTO_HANDOFF_TIMEOUT_TEXT,
    FEEDBACK_BUTTON_TEXT,
    FUZZY_MATCH_REPLY_TEXT,
    FEEDBACK_PROMPT_TEXT,
    FEEDBACK_THANKS_TEXT,
    OTHER_ACK_TEXT,
    OTHER_BUTTON_TEXT,
    OTHER_HANDOFF_TEXT,
    PAYMENT_AFTER_INPUT_TEXT,
    PAYMENT_BUTTON_TEXT,
    PAYMENT_HANDOFF_TEXT,
    PAYMENT_LINK_URL,
    PAYMENT_USERNAME_MISSING_TEXT,
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
        self.fail_send_chat_ids: set[int] = set()

    async def send_message(self, chat_id, text, reply_markup=None):
        if int(chat_id) in self.fail_send_chat_ids:
            raise RuntimeError("chat not found")
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
        self.edits: list[dict] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append({"text": text, "reply_markup": reply_markup})
        return FakeSentMessage()

    async def edit_text(self, text, reply_markup=None):
        self.edits.append({"text": text, "reply_markup": reply_markup})
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


def setup_open_user_bot(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_DB_PATH", str(tmp_path / "open-user-bot.db"))
    init_db()
    store = CustomerServiceStore()
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


def inline_callback_data(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row if button.callback_data]


def inline_button_texts(markup) -> list[str]:
    return [button.text for row in markup.inline_keyboard for button in row]


def test_user_menu_only_shows_two_topic_buttons(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)

    labels = [row[0].text for row in bot.user_menu().inline_keyboard]

    assert labels == [FEEDBACK_BUTTON_TEXT, OTHER_BUTTON_TEXT]
    assert store.get_bot_config()["handoff_button_text"] not in labels


def test_unlisted_user_can_start_bot(monkeypatch, tmp_path):
    bot, store = setup_open_user_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "新用户", "new_user")
    message = FakeMessage(user, fake_bot, "/start", message_id=11)

    asyncio.run(bot.start(message))

    assert store.is_authorized_user(USER_ID)
    assert store.get_or_create_conversation(USER_ID)["telegram_user_id"] == USER_ID
    assert message.answers[-1]["text"] == store.get_bot_config()["welcome_text"]
    labels = [row[0].text for row in message.answers[-1]["reply_markup"].inline_keyboard]
    assert labels == [FEEDBACK_BUTTON_TEXT, OTHER_BUTTON_TEXT]


def test_bot_commands_include_admin_for_enabled_admins(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    fake_bot.default_commands = [
        type("Command", (), {"command": "start", "description": "开始"})(),
        type("Command", (), {"command": "help", "description": "帮助"})(),
    ]

    asyncio.run(bot.setup_bot_commands(fake_bot))
    asyncio.run(bot.set_admin_commands(fake_bot, ADMIN_ID, enabled=False))

    assert len(fake_bot.commands) == 4
    default_commands = [item.command for item in fake_bot.commands[0]["commands"]]
    private_commands = [item.command for item in fake_bot.commands[1]["commands"]]
    user_scope = fake_bot.commands[2]
    user_commands = [item.command for item in user_scope["commands"]]
    admin_scope = fake_bot.commands[3]
    admin_commands = [item.command for item in admin_scope["commands"]]

    assert default_commands == ["start", "help", "feedback", "other", "end"]
    assert private_commands == ["start", "help", "feedback", "other", "end"]
    assert getattr(fake_bot.commands[1]["scope"], "type", None) == "all_private_chats"
    assert user_commands == ["start", "help", "feedback", "other", "end"]
    assert getattr(user_scope["scope"], "chat_id", None) == USER_ID
    assert admin_commands == ["start", "help", "feedback", "other", "end", "admin"]
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
    assert message.answers[-1]["text"] == PAYMENT_HANDOFF_TEXT
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert PAYMENT_BUTTON_TEXT in fake_bot.sent[-1]["text"]


def test_other_feedback_and_end_commands(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户", "tg_user")

    other_message = FakeMessage(user, fake_bot, "/other", message_id=21)
    asyncio.run(bot.other_command(other_message))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "handoff_open"
    assert OTHER_HANDOFF_TEXT in other_message.answers[-1]["text"]
    assert OTHER_BUTTON_TEXT in fake_bot.sent[-1]["text"]

    end_message = FakeMessage(user, fake_bot, "/end", message_id=22)
    asyncio.run(bot.end_handoff_command(end_message))

    assert store.get_or_create_conversation(USER_ID)["status"] == "bot"
    assert end_message.answers[-1]["text"] == store.get_bot_config()["handoff_close_text"]
    assert "已由用戶結束" in fake_bot.sent[-1]["text"]

    feedback_message = FakeMessage(user, fake_bot, "/feedback", message_id=23)
    asyncio.run(bot.feedback_command(feedback_message))

    assert store.get_or_create_conversation(USER_ID)["status"] == "feedback_waiting"
    assert feedback_message.answers[-1]["text"] == FEEDBACK_PROMPT_TEXT


def test_payment_button_prompts_then_auto_replies_after_username(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户", "tg_user")

    message = FakeMessage(user, fake_bot)
    query = FakeQuery(topic_callback_id(PAYMENT_BUTTON_TEXT), user, message, fake_bot)

    asyncio.run(bot.user_topic_callback(query))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "handoff_payment_waiting"
    assert message.answers[-1]["text"] == PAYMENT_HANDOFF_TEXT
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert "新人工會話" in fake_bot.sent[-1]["text"]
    assert PAYMENT_BUTTON_TEXT in fake_bot.sent[-1]["text"]
    assert "Telegram 用户" in fake_bot.sent[-1]["text"]
    sent_count = len(fake_bot.sent)

    user_message = FakeMessage(user, fake_bot, "@tg_user", message_id=22)
    asyncio.run(bot.handle_user_message(user_message))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "bot"
    assert user_message.answers[-1]["text"] == PAYMENT_AFTER_INPUT_TEXT
    assert len(fake_bot.sent) == sent_count + 3
    assert fake_bot.sent[-3]["text"] == "底部菜单角标已更新。"
    assert reply_keyboard_labels(fake_bot.sent[-3]["reply_markup"]) == [f"{ADMIN_PENDING}（1）", ADMIN_MY, ADMIN_RECENT]
    assert fake_bot.sent[-2]["chat_id"] == ADMIN_ID
    assert "@tg_user" in fake_bot.sent[-2]["text"]
    assert PAYMENT_AFTER_INPUT_TEXT not in fake_bot.sent[-2]["text"]
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert "人工服務已結束" in fake_bot.sent[-1]["text"]
    assert PAYMENT_BUTTON_TEXT in fake_bot.sent[-1]["text"]

    inline_keyboard = user_message.answers[-1]["reply_markup"].inline_keyboard
    assert inline_keyboard[0][0].url == PAYMENT_LINK_URL
    assert inline_keyboard[1][0].text == store.get_bot_config()["end_handoff_button_text"]

    second_user_message = FakeMessage(user, fake_bot, "second input", message_id=23)
    asyncio.run(bot.handle_user_message(second_user_message))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "bot"
    assert second_user_message.answers[-1]["text"] == FUZZY_MATCH_REPLY_TEXT
    assert len(fake_bot.sent) == sent_count + 3


def test_other_button_opens_live_handoff_and_forwards_each_message(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户", "tg_user")
    message = FakeMessage(user, fake_bot)
    query = FakeQuery(topic_callback_id(OTHER_BUTTON_TEXT), user, message, fake_bot)

    asyncio.run(bot.user_topic_callback(query))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "handoff_open"
    assert message.answers[-1]["text"] == OTHER_HANDOFF_TEXT
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert "新人工會話" in fake_bot.sent[-1]["text"]
    assert OTHER_BUTTON_TEXT in fake_bot.sent[-1]["text"]
    sent_count = len(fake_bot.sent)

    first_user_message = FakeMessage(user, fake_bot, "第一条留言", message_id=22)
    asyncio.run(bot.handle_user_message(first_user_message))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "handoff_open"
    assert first_user_message.answers[-1]["text"] == OTHER_ACK_TEXT
    assert first_user_message.answers[-1]["reply_markup"] is not None
    assert len(fake_bot.sent) == sent_count + 2
    assert fake_bot.sent[-2]["text"] == "底部菜单角标已更新。"
    assert reply_keyboard_labels(fake_bot.sent[-2]["reply_markup"]) == [f"{ADMIN_PENDING}（1）", ADMIN_MY, ADMIN_RECENT]
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert "第一条留言" in fake_bot.sent[-1]["text"]

    second_user_message = FakeMessage(user, fake_bot, "第二条留言", message_id=23)
    asyncio.run(bot.handle_user_message(second_user_message))

    conversation = store.get_or_create_conversation(USER_ID)
    assert conversation["status"] == "handoff_open"
    assert second_user_message.answers == []
    assert len(fake_bot.sent) == sent_count + 3
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert "第二条留言" in fake_bot.sent[-1]["text"]
    messages = store.list_messages(conversation["id"])
    forwarded_texts = [item["text"] for item in messages if int(item["forwarded_to_admins"]) == 1]
    assert forwarded_texts[-2:] == ["第一条留言", "第二条留言"]
    bot_ack_texts = [item["text"] for item in messages if item["direction"] == "bot" and item["text"] == OTHER_ACK_TEXT]
    assert bot_ack_texts == [OTHER_ACK_TEXT]


def test_payment_requires_matching_telegram_username(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户", "tg_user")
    conversation = store.set_conversation_status(USER_ID, "handoff_payment_waiting")
    invalid_message = FakeMessage(user, fake_bot, "123", message_id=24)

    asyncio.run(bot.handle_user_message(invalid_message))

    assert store.get_or_create_conversation(USER_ID)["status"] == "handoff_payment_waiting"
    assert invalid_message.answers[-1]["text"] == PAYMENT_HANDOFF_TEXT
    assert fake_bot.sent == []
    messages = store.list_messages(conversation["id"])
    assert messages[-2]["text"] == "123"
    assert messages[-2]["forwarded_to_admins"] == 0
    assert messages[-1]["text"] == PAYMENT_HANDOFF_TEXT

    valid_message = FakeMessage(user, fake_bot, "tg_user", message_id=25)
    asyncio.run(bot.handle_user_message(valid_message))

    assert store.get_or_create_conversation(USER_ID)["status"] == "bot"
    assert valid_message.answers[-1]["text"] == PAYMENT_AFTER_INPUT_TEXT
    assert fake_bot.sent[-2]["chat_id"] == ADMIN_ID
    assert "tg_user" in fake_bot.sent[-2]["text"]
    assert "人工服務已結束" in fake_bot.sent[-1]["text"]


def test_payment_requires_user_to_have_telegram_username(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户")
    conversation = store.set_conversation_status(USER_ID, "handoff_payment_waiting")
    message = FakeMessage(user, fake_bot, "Telegram 用户", message_id=26)

    asyncio.run(bot.handle_user_message(message))

    assert store.get_or_create_conversation(USER_ID)["status"] == "handoff_payment_waiting"
    assert message.answers[-1]["text"] == PAYMENT_USERNAME_MISSING_TEXT
    assert fake_bot.sent == []
    messages = store.list_messages(conversation["id"])
    assert messages[-2]["text"] == "Telegram 用户"
    assert messages[-2]["forwarded_to_admins"] == 0
    assert messages[-1]["text"] == PAYMENT_USERNAME_MISSING_TEXT


def test_standby_any_message_replies_with_placeholder(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户", "tg_user")
    conversation = store.get_or_create_conversation(USER_ID)
    message = FakeMessage(user, fake_bot, "abc123", message_id=27)

    asyncio.run(bot.handle_user_message(message))

    assert store.get_or_create_conversation(USER_ID)["status"] == "bot"
    assert message.answers[-1]["text"] == FUZZY_MATCH_REPLY_TEXT
    assert fake_bot.sent == []
    messages = store.list_messages(conversation["id"])
    assert messages[-2]["text"] == "abc123"
    assert messages[-2]["forwarded_to_admins"] == 0
    assert messages[-1]["text"] == FUZZY_MATCH_REPLY_TEXT


def test_placeholder_reply_only_applies_in_standby(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    user = FakeUser(USER_ID, "Telegram 用户", "tg_user")
    conversation = store.open_handoff(USER_ID)
    message = FakeMessage(user, fake_bot, "我想問付款連結", message_id=28)

    asyncio.run(bot.handle_user_message(message))

    assert message.answers == []
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert "我想問付款連結" in fake_bot.sent[-1]["text"]
    messages = store.list_messages(conversation["id"])
    assert messages[-1]["text"] == "我想問付款連結"
    assert messages[-1]["forwarded_to_admins"] == 1


def test_feedback_button_collects_one_message_and_forwards_without_claim(monkeypatch, tmp_path):
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
    messages = store.list_messages(store.get_or_create_conversation(USER_ID)["id"])
    assert messages[-2]["forwarded_to_admins"] == 1
    assert fake_bot.sent[-1]["chat_id"] == ADMIN_ID
    assert "用戶建議/心得" in fake_bot.sent[-1]["text"]
    assert "建议内容" in fake_bot.sent[-1]["text"]
    callbacks = inline_callback_data(fake_bot.sent[-1]["reply_markup"])
    assert callbacks == [f"view:{store.get_or_create_conversation(USER_ID)['id']}"]

    follow_up = FakeMessage(user, fake_bot, "你好", message_id=23)
    asyncio.run(bot.handle_user_message(follow_up))

    assert follow_up.answers[-1]["text"] == FUZZY_MATCH_REPLY_TEXT
    assert follow_up.answers[-1]["reply_markup"] is not None
    assert len(fake_bot.sent) == 2
    assert fake_bot.sent[0]["text"] == "底部菜单角标已更新。"
    assert reply_keyboard_labels(fake_bot.sent[0]["reply_markup"]) == [ADMIN_PENDING, f"{ADMIN_MY}（1）", ADMIN_RECENT]


def test_admin_menu_has_human_feedback_and_recent_buttons_with_counts(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    admin = FakeUser(ADMIN_ID, "管理员")
    feedback_user_id = 1002
    store.upsert_telegram_user(feedback_user_id, "反馈用户", True)

    handoff = store.open_handoff(USER_ID)
    store.add_message(handoff["id"], "user", USER_ID, "Telegram 用户", "text", "人工消息", forwarded_to_admins=True)
    store.add_message(handoff["id"], "admin", ADMIN_ID, "管理员", "text", "人工回复")
    feedback = store.get_or_create_conversation(feedback_user_id)
    store.add_message(feedback["id"], "user", feedback_user_id, "反馈用户", "callback", FEEDBACK_BUTTON_TEXT)
    store.add_message(feedback["id"], "user", feedback_user_id, "反馈用户", "text", "建议内容", forwarded_to_admins=True)
    store.add_message(feedback["id"], "admin", ADMIN_ID, "管理员", "text", "反馈回复")

    labels = reply_keyboard_labels(bot.admin_menu(ADMIN_ID))
    assert labels == [f"{ADMIN_PENDING}（1）", f"{ADMIN_MY}（1）", ADMIN_RECENT]

    human_message = FakeMessage(admin, fake_bot, f"{ADMIN_PENDING}（1）")
    asyncio.run(bot.handle_admin_message(human_message))
    assert "人工服务处理（1）" in human_message.answers[-1]["text"]
    assert "<pre>" not in human_message.answers[-1]["text"]
    assert "序 ID" in human_message.answers[-1]["text"]
    assert "消 回" in human_message.answers[-1]["text"]
    assert " 1  1" in human_message.answers[-1]["text"]
    assert f"#{handoff['id']}" not in human_message.answers[-1]["text"]
    human_buttons = inline_button_texts(human_message.answers[-1]["reply_markup"])
    assert f"后台备注 · {USER_ID}" in human_buttons
    assert "回复" in human_buttons
    assert "忽略" in human_buttons
    assert inline_callback_data(human_message.answers[-1]["reply_markup"]) == [
        f"view:{handoff['id']}",
        f"admin_handoff_reply:{handoff['id']}:0",
        f"admin_handoff_ignore:{handoff['id']}:0",
        "admin_handoff_page:0",
    ]
    asyncio.run(bot.view_callback(FakeQuery(f"view:{handoff['id']}", admin, human_message, fake_bot)))
    assert human_message.answers[-1]["text"].startswith(f"用戶 ID {USER_ID} 最近用戶訊息")
    assert "人工消息" in human_message.answers[-1]["text"]

    asyncio.run(bot.admin_handoff_detail_callback(FakeQuery(f"admin_handoff_detail:{handoff['id']}:0", admin, human_message, fake_bot)))
    assert human_message.edits[-1]["text"].startswith("人工服务处理\n")
    assert f"#{handoff['id']}" not in human_message.edits[-1]["text"]
    assert human_message.edits[-1]["text"].count(str(USER_ID)) == 1
    assert inline_callback_data(human_message.edits[-1]["reply_markup"]) == [
        f"admin_handoff_reply:{handoff['id']}:0",
        f"view:{handoff['id']}",
        f"admin_handoff_ignore:{handoff['id']}:0",
        "admin_handoff_page:0",
    ]

    asyncio.run(bot.admin_handoff_reply_callback(FakeQuery(f"admin_handoff_reply:{handoff['id']}:0", admin, human_message, fake_bot)))
    assert "正在回复 ID" in human_message.edits[-1]["text"]
    assert store.get_admin_current_conversation(ADMIN_ID)["id"] == handoff["id"]

    feedback_message = FakeMessage(admin, fake_bot, f"{ADMIN_MY}（1）")
    asyncio.run(bot.handle_admin_message(feedback_message))
    assert "建议反馈处理（1）" in feedback_message.answers[-1]["text"]
    assert "<pre>" not in feedback_message.answers[-1]["text"]
    assert "序 ID" in feedback_message.answers[-1]["text"]
    assert "留 回" in feedback_message.answers[-1]["text"]
    assert " 1  1" in feedback_message.answers[-1]["text"]
    assert f"#{feedback['id']}" not in feedback_message.answers[-1]["text"]
    feedback_buttons = inline_button_texts(feedback_message.answers[-1]["reply_markup"])
    assert f"反馈用户 · {feedback_user_id}" in feedback_buttons
    assert "回复" in feedback_buttons
    assert "忽略" in feedback_buttons
    assert inline_callback_data(feedback_message.answers[-1]["reply_markup"]) == [
        f"admin_feedback_detail:{feedback['id']}:0",
        f"admin_feedback_reply:{feedback['id']}:0",
        f"admin_feedback_ignore:{feedback['id']}:0",
        "admin_feedback_page:0",
    ]

    asyncio.run(bot.admin_feedback_detail_callback(FakeQuery(f"admin_feedback_detail:{feedback['id']}:0", admin, feedback_message, fake_bot)))
    assert feedback_message.edits[-1]["text"].startswith("建议反馈处理\n")
    assert "建议内容" in feedback_message.edits[-1]["text"]
    assert feedback_message.edits[-1]["text"].count(str(feedback_user_id)) == 1
    assert inline_callback_data(feedback_message.edits[-1]["reply_markup"]) == [
        f"admin_feedback_reply:{feedback['id']}:0",
        f"admin_feedback_ignore:{feedback['id']}:0",
        "admin_feedback_page:0",
    ]
    assert reply_keyboard_labels(bot.admin_menu(ADMIN_ID)) == [f"{ADMIN_PENDING}（1）", f"{ADMIN_MY}（1）", ADMIN_RECENT]

    asyncio.run(bot.admin_feedback_reply_callback(FakeQuery(f"admin_feedback_reply:{feedback['id']}:0", admin, feedback_message, fake_bot)))
    assert "正在回复建议反馈 ID" in feedback_message.edits[-1]["text"]
    assert store.get_admin_current_conversation(ADMIN_ID)["id"] == feedback["id"]
    admin_reply = FakeMessage(admin, fake_bot, "反馈已收到", message_id=45)
    asyncio.run(bot.handle_admin_message(admin_reply))
    assert fake_bot.sent[-1]["chat_id"] == feedback_user_id
    assert "反馈已收到" in fake_bot.sent[-1]["text"]
    assert reply_keyboard_labels(bot.admin_menu(ADMIN_ID)) == [f"{ADMIN_PENDING}（1）", f"{ADMIN_MY}（1）", ADMIN_RECENT]

    asyncio.run(bot.admin_feedback_ignore_callback(FakeQuery(f"admin_feedback_ignore:{feedback['id']}:0", admin, feedback_message, fake_bot)))
    assert reply_keyboard_labels(bot.admin_menu(ADMIN_ID)) == [f"{ADMIN_PENDING}（1）", ADMIN_MY, ADMIN_RECENT]

    asyncio.run(bot.admin_handoff_ignore_callback(FakeQuery(f"admin_handoff_ignore:{handoff['id']}:0", admin, human_message, fake_bot)))
    assert reply_keyboard_labels(bot.admin_menu(ADMIN_ID)) == [ADMIN_PENDING, ADMIN_MY, ADMIN_RECENT]


def test_admin_lists_only_show_recent_ten_unique_users(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    admin = FakeUser(ADMIN_ID, "管理员")
    base_ts = now_ts()
    recent_handoff_ids = [3000 + index for index in range(12)]
    old_handoff_id = 3999
    recent_feedback_ids = [4000 + index for index in range(12)]
    old_feedback_id = 4999

    for index, user_id in enumerate(recent_handoff_ids):
        store.upsert_telegram_user(user_id, f"人工{index}", True)
        conversation = store.open_handoff(user_id)
        message = store.add_message(conversation["id"], "user", user_id, f"人工{index}", "text", f"人工内容{index}", forwarded_to_admins=True)
        with db() as conn:
            conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (base_ts - index, message["id"]))
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (base_ts - index, conversation["id"]))
    store.upsert_telegram_user(old_handoff_id, "过期人工", True)
    old_conversation = store.open_handoff(old_handoff_id)
    old_handoff_message = store.add_message(old_conversation["id"], "user", old_handoff_id, "过期人工", "text", "过期人工内容", forwarded_to_admins=True)
    with db() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (base_ts - 8 * 24 * 3600, old_handoff_message["id"]))
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (base_ts - 8 * 24 * 3600, old_conversation["id"]))

    for index, user_id in enumerate(recent_feedback_ids):
        store.upsert_telegram_user(user_id, f"反馈{index}", True)
        conversation = store.get_or_create_conversation(user_id)
        start = store.add_message(conversation["id"], "user", user_id, f"反馈{index}", "callback", FEEDBACK_BUTTON_TEXT)
        feedback = store.add_message(conversation["id"], "user", user_id, f"反馈{index}", "text", f"反馈内容{index}", forwarded_to_admins=True)
        with db() as conn:
            conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (base_ts - index - 1, start["id"]))
            conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (base_ts - index, feedback["id"]))
    store.upsert_telegram_user(old_feedback_id, "过期反馈", True)
    old_feedback_conversation = store.get_or_create_conversation(old_feedback_id)
    old_start = store.add_message(old_feedback_conversation["id"], "user", old_feedback_id, "过期反馈", "callback", FEEDBACK_BUTTON_TEXT)
    old_feedback = store.add_message(old_feedback_conversation["id"], "user", old_feedback_id, "过期反馈", "text", "过期反馈内容", forwarded_to_admins=True)
    with db() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (base_ts - 8 * 24 * 3600 - 1, old_start["id"]))
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (base_ts - 8 * 24 * 3600, old_feedback["id"]))

    assert reply_keyboard_labels(bot.admin_menu(ADMIN_ID)) == [f"{ADMIN_PENDING}（10）", f"{ADMIN_MY}（10）", ADMIN_RECENT]

    handoff_message = FakeMessage(admin, fake_bot, ADMIN_PENDING)
    asyncio.run(bot.handle_admin_message(handoff_message))
    handoff_buttons = inline_button_texts(handoff_message.answers[-1]["reply_markup"])
    handoff_page_two_text, handoff_page_two_markup = bot.admin_handoff_list_view(page=1)
    assert "3000" in handoff_message.answers[-1]["text"]
    assert "----------------------------------------" in handoff_message.answers[-1]["text"]
    assert "3009" in handoff_page_two_text
    assert "3010" not in handoff_message.answers[-1]["text"]
    assert "3010" not in handoff_page_two_text
    assert str(old_handoff_id) not in handoff_message.answers[-1]["text"]
    assert str(old_handoff_id) not in handoff_page_two_text
    assert "回复" in handoff_buttons
    assert "忽略" in handoff_buttons
    assert any(text.startswith("人工0 · 3000") for text in handoff_buttons)
    assert any(text.startswith("人工9 · 3009") for text in inline_button_texts(handoff_page_two_markup))
    assert not any("3010" in text for text in handoff_buttons + inline_button_texts(handoff_page_two_markup))

    feedback_message = FakeMessage(admin, fake_bot, ADMIN_MY)
    asyncio.run(bot.handle_admin_message(feedback_message))
    feedback_buttons = inline_button_texts(feedback_message.answers[-1]["reply_markup"])
    feedback_page_two_text, feedback_page_two_markup = bot.admin_feedback_list_view(page=1)
    assert "4000" in feedback_message.answers[-1]["text"]
    assert "----------------------------------------" in feedback_message.answers[-1]["text"]
    assert "4009" in feedback_page_two_text
    assert "4010" not in feedback_message.answers[-1]["text"]
    assert "4010" not in feedback_page_two_text
    assert str(old_feedback_id) not in feedback_message.answers[-1]["text"]
    assert str(old_feedback_id) not in feedback_page_two_text
    assert "回复" in feedback_buttons
    assert "忽略" in feedback_buttons
    assert any(text.startswith("反馈0 · 4000") for text in feedback_buttons)
    assert any(text.startswith("反馈9 · 4009") for text in inline_button_texts(feedback_page_two_markup))
    assert not any("4010" in text for text in feedback_buttons + inline_button_texts(feedback_page_two_markup))


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


def test_admin_forward_failure_does_not_block_user_reply(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    fake_bot.fail_send_chat_ids.add(ADMIN_ID)
    user = FakeUser(USER_ID, "Telegram 用户", "pay_user")
    conversation = store.set_conversation_status(USER_ID, "handoff_payment_waiting")
    user_message = FakeMessage(user, fake_bot, "pay_user", message_id=33)

    asyncio.run(bot.handle_user_message(user_message))

    assert user_message.answers[-1]["text"] == PAYMENT_AFTER_INPUT_TEXT
    assert fake_bot.sent == []
    messages = store.list_messages(conversation["id"])
    assert messages[-2]["text"] == "pay_user"
    assert messages[-2]["forwarded_to_admins"] == 1


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

    assert admin_message.answers[-1]["text"].startswith(f"用戶 ID {USER_ID} 最近用戶訊息")
    assert "[" in admin_message.answers[-1]["text"]
    assert "历史消息" in admin_message.answers[-1]["text"]
    assert "非人工消息" not in admin_message.answers[-1]["text"]
    assert PAYMENT_AFTER_INPUT_TEXT not in admin_message.answers[-1]["text"]
    assert PAYMENT_BUTTON_TEXT not in admin_message.answers[-1]["text"]

    list_message = FakeMessage(admin, fake_bot, ADMIN_PENDING)
    asyncio.run(bot.handle_admin_message(list_message))
    assert "<pre>" not in list_message.answers[-1]["text"]
    assert inline_callback_data(list_message.answers[-1]["reply_markup"]) == [
        f"view:{conversation['id']}",
        f"admin_handoff_reply:{conversation['id']}:0",
        f"admin_handoff_ignore:{conversation['id']}:0",
        "admin_handoff_page:0",
    ]

    asyncio.run(bot.claim_callback(FakeQuery(f"claim:{conversation['id']}", admin, admin_message, fake_bot)))

    claimed = store.get_conversation(conversation["id"])
    assert claimed["status"] == "handoff_claimed"
    assert claimed["claimed_by_admin_id"] == ADMIN_ID
    assert "已接管用戶 ID" in admin_message.answers[-2]["text"]
    assert all(not label.startswith(ADMIN_RELEASE) for label in reply_keyboard_labels(admin_message.answers[-2]["reply_markup"]))
    assert str(USER_ID) in bot.admin_handoff_list_view(page=0)[0]

    release_message = FakeMessage(admin, fake_bot, ADMIN_RELEASE)
    asyncio.run(bot.handle_admin_message(release_message))

    assert store.get_conversation(conversation["id"]) is None
    assert store.list_messages(conversation["id"]) == []
    assert store.get_admin_current_conversation(ADMIN_ID) is None
    assert "已清除當前用戶 ID" in release_message.answers[-1]["text"]
    assert reply_keyboard_labels(release_message.answers[-1]["reply_markup"]) == [ADMIN_PENDING, ADMIN_MY, ADMIN_RECENT]


def test_admin_recent_handoff_history_shows_recent_ten_users_and_filters_feedback(monkeypatch, tmp_path):
    bot, store = setup_bot(monkeypatch, tmp_path)
    fake_bot = FakeBot()
    admin = FakeUser(ADMIN_ID, "管理员")
    base_ts = now_ts()
    recent_user_ids = [5000 + index for index in range(12)]
    old_user_id = 5999
    feedback_user_id = 6000

    for index, user_id in enumerate(recent_user_ids):
        store.upsert_telegram_user(user_id, f"人工记录{index}", True)
        conversation = store.get_or_create_conversation(user_id)
        store.add_message(conversation["id"], "user", user_id, f"人工记录{index}", "callback", OTHER_BUTTON_TEXT)
        handoff_message = store.add_message(
            conversation["id"],
            "user",
            user_id,
            f"人工记录{index}",
            "text",
            f"人工聊天{index}",
            forwarded_to_admins=True,
        )
        store.add_message(conversation["id"], "bot", None, "Bot", "text", OTHER_ACK_TEXT)
        with db() as conn:
            conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (base_ts - index, handoff_message["id"]))
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (base_ts - index, conversation["id"]))

    store.upsert_telegram_user(old_user_id, "过期人工记录", True)
    old_conversation = store.get_or_create_conversation(old_user_id)
    old_message = store.add_message(
        old_conversation["id"],
        "user",
        old_user_id,
        "过期人工记录",
        "text",
        "过期人工聊天",
        forwarded_to_admins=True,
    )
    with db() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (base_ts - 8 * 24 * 3600, old_message["id"]))

    store.upsert_telegram_user(feedback_user_id, "反馈记录", True)
    feedback_conversation = store.get_or_create_conversation(feedback_user_id)
    store.add_message(feedback_conversation["id"], "user", feedback_user_id, "反馈记录", "callback", FEEDBACK_BUTTON_TEXT)
    feedback_message = store.add_message(
        feedback_conversation["id"],
        "user",
        feedback_user_id,
        "反馈记录",
        "text",
        "不应该出现在人工记录",
        forwarded_to_admins=True,
    )
    with db() as conn:
        conn.execute("UPDATE messages SET created_at = ? WHERE id = ?", (base_ts, feedback_message["id"]))

    recent_message = FakeMessage(admin, fake_bot, ADMIN_RECENT)
    asyncio.run(bot.handle_admin_message(recent_message))

    first_text = recent_message.answers[-1]["text"]
    first_buttons = inline_button_texts(recent_message.answers[-1]["reply_markup"])
    assert "最近会话记录" in first_text
    assert "5000" in first_text
    assert "----------------------------------------" in first_text
    assert "5009" in bot.admin_recent_handoff_history_list_view(page=1)[0]
    assert "5010" not in first_text
    assert str(old_user_id) not in first_text
    assert str(feedback_user_id) not in first_text
    assert any(text.startswith("人工记录0 · 5000") for text in first_buttons)

    detail_query = FakeQuery(f"admin_recent_detail:{store.get_or_create_conversation(5000)['id']}:0", admin, recent_message, fake_bot)
    asyncio.run(bot.admin_recent_detail_callback(detail_query))

    detail_text = recent_message.edits[-1]["text"]
    assert "最近 7 天人工服务消息" in detail_text
    assert "人工聊天0" in detail_text
    assert OTHER_ACK_TEXT not in detail_text
    assert "不应该出现在人工记录" not in detail_text


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
