#!/usr/bin/env python3
"""Telegram launcher for large APKs through a local Bot API server.

Uses the existing Telegram handlers and patch engine. If telegram_bot_live.py is
available, its detailed live patch renderer is enabled automatically.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

# Give the local-Bot-API launcher a practical large-file default while still
# allowing operators to set a lower/higher application safety cap explicitly.
os.environ.setdefault("APKPATCHER_MAX_APK_MB", "500")
os.environ.setdefault("TELEGRAM_BOT_API_BASE_URL", "http://127.0.0.1:8081/bot")
os.environ.setdefault("TELEGRAM_BOT_API_FILE_URL", "http://127.0.0.1:8081/file/bot")
os.environ.setdefault("TELEGRAM_BOT_API_DATA_DIR", "/var/lib/telegram-bot-api")

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


def _raw_local_get_file_path(file_id: str) -> Path:
    """Call the local Bot API directly and return its absolute file_path.

    telegram-bot-api started with --local returns an absolute filesystem path
    in the raw getFile JSON. python-telegram-bot intentionally normalizes a
    File.file_path into a file URL, so for local downloads we read the raw JSON
    instead and never call File.download_to_drive().
    """
    base_url = os.environ["TELEGRAM_BOT_API_BASE_URL"].rstrip("/")
    endpoint = f"{base_url}{bot.TOKEN}/getFile"
    body = urllib.parse.urlencode({"file_id": file_id}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        # Do not include the exception text here: urllib errors can contain the
        # request URL, and the Bot API token is part of that URL.
        raise RuntimeError(f"Local Bot API getFile request failed ({type(exc).__name__})") from None

    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError("Local Bot API getFile returned a non-success response")

    result = payload.get("result")
    raw_path = result.get("file_path") if isinstance(result, dict) else None
    if not isinstance(raw_path, str) or not raw_path.startswith("/"):
        raise RuntimeError("Local Bot API did not return an absolute file path")

    data_root = Path(os.environ["TELEGRAM_BOT_API_DATA_DIR"]).resolve(strict=True)
    local_path = Path(raw_path).resolve(strict=True)

    # The API response is trusted, but still constrain file access to the Bot
    # API working directory configured on this VPS.
    try:
        local_path.relative_to(data_root)
    except ValueError:
        raise RuntimeError("Local Bot API returned a path outside its data directory") from None

    if not local_path.is_file():
        raise FileNotFoundError("Local Bot API file does not exist")
    if not os.access(local_path, os.R_OK):
        raise PermissionError("Bot process cannot read the Local Bot API file")

    return local_path


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
        local_path = await asyncio.to_thread(_raw_local_get_file_path, doc.file_id)
        await asyncio.to_thread(shutil.copy2, local_path, input_path)

        copied_size = input_path.stat().st_size
        if doc.file_size is not None and copied_size != doc.file_size:
            raise IOError("Copied APK size does not match Telegram metadata")
    except (OSError, RuntimeError) as exc:
        shutil.rmtree(workdir, ignore_errors=True)
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
