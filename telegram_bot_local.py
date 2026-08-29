#!/usr/bin/env python3
"""Telegram launcher for large APKs through a local Bot API server.

Uses the existing Telegram handlers and patch engine. If telegram_bot_live.py is
available, its detailed live patch renderer is enabled automatically.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit

# Give the local-Bot-API launcher a practical large-file default while still
# allowing operators to set a lower/higher application safety cap explicitly.
os.environ.setdefault("APKPATCHER_MAX_APK_MB", "500")
os.environ.setdefault("TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081/bot")
os.environ.setdefault("TELEGRAM_BOT_API_FILE_URL", "http://127.0.0.1:8081/file/bot")
os.environ.setdefault("TELEGRAM_BOT_API_DATA_DIR", "/var/lib/telegram-bot-api")

import telegram_bot as bot
from telegram import Update
from telegram.error import TelegramError
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


def _extract_local_file_path(file_path: str) -> Path:
    """Convert PTB's local-Bot-API file URL back to its filesystem path.

    PTB may prepend ``base_file_url + token`` even when telegram-bot-api is
    running with ``--local``. The Local Bot API response itself contains an
    absolute filesystem path, so strip only our known local file prefix and
    validate that the result stays inside the configured Bot API data dir.
    """
    raw = str(file_path)

    if raw.startswith(("http://", "https://")):
        parsed = urlsplit(raw)
        url_path = unquote(parsed.path)
        marker = f"/file/bot{bot.TOKEN}"
        if not url_path.startswith(marker):
            raise RuntimeError("Unexpected Local Bot API file URL format")
        raw = url_path[len(marker):]

    candidate = Path(raw)
    if not candidate.is_absolute():
        raise RuntimeError("Local Bot API did not provide an absolute filesystem path")

    candidate = candidate.resolve(strict=False)
    data_root = Path(os.environ["TELEGRAM_BOT_API_DATA_DIR"]).resolve(strict=False)
    try:
        candidate.relative_to(data_root)
    except ValueError as exc:
        raise RuntimeError("Local Bot API file path is outside the configured data directory") from exc

    return candidate


async def receive_document_local(update, context) -> None:
    """Receive an APK through telegram-bot-api running with --local."""
    user = update.effective_user
    msg = update.effective_message
    doc = msg.document if msg else None
    if not user or not doc:
        return

    if not bot._authorized(user.id):
        await msg.reply_text("⛔ This bot is private.")
        return

    filename = bot._safe_name(doc.file_name or "uploaded.apk")
    if not filename.lower().endswith(".apk"):
        await msg.reply_text(
            "❌ Please send a normal <code>.apk</code> file.",
            parse_mode=bot.ParseMode.HTML,
        )
        return

    limit = bot.MAX_APK_MB * 1024 * 1024
    if doc.file_size and doc.file_size > limit:
        await msg.reply_text(
            f"❌ APK is too large. Current bot limit: {bot.MAX_APK_MB} MB."
        )
        return

    old = bot.SESSIONS.get(user.id)
    if old and old.running:
        await msg.reply_text(
            "⏳ Your current patch job is still running. Wait for it or use /reset later."
        )
        return
    if old:
        shutil.rmtree(old.workdir, ignore_errors=True)

    job_id = uuid.uuid4().hex[:10]
    workdir = bot.BASE_WORKDIR / f"u{user.id}_{job_id}"
    workdir.mkdir(parents=True, exist_ok=False)
    unique_name = f"u{user.id}_{job_id}_{filename}"
    input_path = workdir / unique_name

    status = await msg.reply_text("⬇️ Loading APK from local Telegram storage…")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        if not tg_file.file_path:
            raise FileNotFoundError("Local Bot API did not return a file path")

        local_path = _extract_local_file_path(str(tg_file.file_path))
        if not local_path.is_file():
            raise FileNotFoundError("Local Bot API file is not present on disk")

        await asyncio.to_thread(shutil.copy2, local_path, input_path)
    except (TelegramError, OSError, RuntimeError) as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        # Do not log the full exception/path here: PTB file URLs can contain
        # the bot token. Keep production logs free of credentials.
        bot.LOG.error("Local APK transfer failed: %s", type(exc).__name__)
        await bot._safe_edit(
            status,
            f"❌ Download failed: <code>{type(exc).__name__}</code>",
            parse_mode=bot.ParseMode.HTML,
        )
        return

    session = bot.Session(
        workdir=workdir,
        input_path=input_path,
        original_name=filename,
    )
    bot.SESSIONS[user.id] = session
    await bot._safe_edit(
        status,
        bot._session_text(session),
        parse_mode=bot.ParseMode.HTML,
        reply_markup=bot._keyboard(session),
    )


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

    # httpx INFO records include the complete Bot API URL, which contains the
    # bot token. Keep transport logging quiet in normal production operation.
    bot.logging.getLogger("httpx").setLevel(bot.logging.WARNING)
    bot.logging.getLogger("httpcore").setLevel(bot.logging.WARNING)

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
    app.add_handler(MessageHandler(filters.Document.ALL, receive_document_local))
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
