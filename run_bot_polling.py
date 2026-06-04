from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from app.bot import TelegramCustomerBot
from app.db import init_db
from app.service import CustomerServiceStore


POLLING_RELOAD_INTERVAL_SECONDS = 5


def current_token(store: CustomerServiceStore) -> str:
    return str(store.get_bot_config().get("bot_token") or "").strip()


async def stop_tasks(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def run_bot_until_token_changes(store: CustomerServiceStore, stop_event: asyncio.Event) -> None:
    token = current_token(store)
    if not token:
        logging.warning("Bot token is not configured; polling is waiting for a token.")
        await asyncio.wait_for(stop_event.wait(), timeout=POLLING_RELOAD_INTERVAL_SECONDS)
        return

    tg = TelegramCustomerBot(store)
    bot = tg.make_bot()
    tasks: list[asyncio.Task] = []
    try:
        me = await bot.get_me()
        logging.info("Starting Telegram polling for bot @%s (%s)", me.username, me.id)
        await bot.delete_webhook(drop_pending_updates=False)
        await tg.setup_bot_commands(bot)
        polling_task = asyncio.create_task(tg.dispatcher.start_polling(bot, handle_signals=False))
        tasks = [
            polling_task,
            asyncio.create_task(tg.idle_handoff_monitor(bot, stop_event)),
            asyncio.create_task(tg.conversation_cleanup_monitor(stop_event)),
        ]

        while not stop_event.is_set():
            if polling_task.done():
                polling_task.result()
                return
            if current_token(store) != token:
                logging.info("Bot token changed; restarting Telegram polling.")
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLLING_RELOAD_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass
    finally:
        await stop_tasks(tasks)
        await bot.session.close()


async def amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    store = CustomerServiceStore()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    while not stop_event.is_set():
        try:
            await run_bot_until_token_changes(store, stop_event)
        except asyncio.TimeoutError:
            pass
        except Exception:
            logging.exception("Telegram polling stopped unexpectedly; retrying.")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLLING_RELOAD_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
