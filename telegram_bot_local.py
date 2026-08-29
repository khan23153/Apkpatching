#!/usr/bin/env python3
"""Telegram launcher for large APKs through a local Bot API server.

Uses the existing Telegram handlers and patch engine. If telegram_bot_live.py is
available, its detailed live patch renderer is enabled automatically.
"""

from __future__ import annotations

import os

# Give the local-Bot-API launcher a practical large-file default while still
# allowing operators to set a lower/higher application safety cap explicitly.
os.environ.setdefault("APKPATCHER_MAX_APK_MB", "500")
os.environ.setdefault("TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081/bot")
os.environ.setdefault("TELEGRAM_BOT_API_FILE_URL", "http://127.0.0.1:8081/file/bot")

import telegram_bot as bot
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

try:
    from telegram_bot_live import run_patch_job_live
except ImportError:
    run_patch_job_live = None


def main() -> None:
    if not bot.TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    if run_patch_job_live is not None:
        bot.run_patch_job = run_patch_job_live

    # python-telegram-bot appends the bot token directly to these prefixes.
    # Keep them ending in '/bot', not '/bot/', otherwise requests become
    # '/bot/<token>/method' and the local Bot API rejects the token.
    base_url = os.environ["TELEGRAM_BOT_API_BASE_URL"].rstrip("/")
    base_file_url = os.environ["TELEGRAM_BOT_API_FILE_URL"].rstrip("/")

    bot.logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = (
        Application.builder()
        .token(bot.TOKEN)
        .base_url(base_url)
        .base_file_url(base_file_url)
        .local_mode(True)
        .build()
    )

    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("help", bot.help_command))
    app.add_handler(CommandHandler("reset", bot.reset))
    app.add_handler(CallbackQueryHandler(bot.callback))
    app.add_handler(MessageHandler(filters.Document.ALL, bot.receive_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.receive_text))
    app.add_error_handler(bot.error_handler)

    bot.LOG.info(
        "Starting APK Patcher bot via local Bot API at %s (max APK %s MB)",
        base_url,
        bot.MAX_APK_MB,
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
