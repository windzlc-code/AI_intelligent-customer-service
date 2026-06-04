from __future__ import annotations

import asyncio
import signal

from app.bot import TelegramCustomerBot
from app.db import init_db
from app.service import CustomerServiceStore


async def amain() -> None:
    init_db()
    tg = TelegramCustomerBot(CustomerServiceStore())
    bot = tg.make_bot()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    await bot.delete_webhook(drop_pending_updates=False)
    await tg.setup_bot_commands(bot)
    polling_task = asyncio.create_task(tg.dispatcher.start_polling(bot, handle_signals=False))
    idle_monitor_task = asyncio.create_task(tg.idle_handoff_monitor(bot, stop_event))
    cleanup_monitor_task = asyncio.create_task(tg.conversation_cleanup_monitor(stop_event))
    try:
        await stop_event.wait()
    finally:
        cleanup_monitor_task.cancel()
        idle_monitor_task.cancel()
        polling_task.cancel()
        await bot.session.close()


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
