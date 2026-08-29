#!/usr/bin/env python3
"""Telegram front-end for ApkPatcherX.

This module keeps the original ApkPatcher engine unchanged and invokes the
installed ``apkpatcher`` console command in a subprocess for each job.

Required environment variables:
    TELEGRAM_BOT_TOKEN=<BotFather token>

Optional:
    APKPATCHER_MAX_APK_MB=20
    APKPATCHER_JOB_TIMEOUT=1800
    APKPATCHER_WORKERS=1
    APKPATCHER_ALLOWED_USERS=123456789,987654321
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


LOG = logging.getLogger("apkpatcher.telegram")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
MAX_APK_MB = max(1, int(os.environ.get("APKPATCHER_MAX_APK_MB", "20")))
JOB_TIMEOUT = max(60, int(os.environ.get("APKPATCHER_JOB_TIMEOUT", "1800")))
WORKERS = max(1, int(os.environ.get("APKPATCHER_WORKERS", "1")))

_allowed_raw = os.environ.get("APKPATCHER_ALLOWED_USERS", "").strip()
ALLOWED_USERS = {
    int(x.strip()) for x in _allowed_raw.split(",") if x.strip().isdigit()
}

BASE_WORKDIR = Path(
    os.environ.get(
        "APKPATCHER_BOT_WORKDIR",
        str(Path(tempfile.gettempdir()) / "apkpatcher-bot"),
    )
).expanduser()
BASE_WORKDIR.mkdir(parents=True, exist_ok=True)

PATCH_SEMAPHORE = asyncio.Semaphore(WORKERS)


@dataclass(frozen=True)
class PatchOption:
    key: str
    label: str
    flags: tuple[str, ...]
    note: str = ""


# The original project applies its normal SSL/VPN smali patch when no special
# short-circuit patch (AES/Ads/etc.) is selected. Keep that as one explicit
# option instead of pretending SSL and VPN are independently selectable.
PATCHES: tuple[PatchOption, ...] = (
    PatchOption("default", "SSL + VPN Bypass", ()),
    PatchOption("flutter", "Flutter SSL Bypass", ("-f",)),
    PatchOption("package", "Spoof Package Detection", ("-pkg",)),
    PatchOption("screenshot", "Remove Screenshot Restriction", ("-rmss",)),
    PatchOption("androidid", "Android ID Hook", (), "Requires a custom Android ID"),
    PatchOption("usb", "USB Debugging Bypass", ("-rmusb",)),
    PatchOption("ads", "Ad Removal", ("-rmads",)),
    PatchOption("telegram", "Telegram Patcher", ("-t",)),
    PatchOption("algorithm", "Algorithm Logs", ("-A2",)),
)
PATCH_BY_KEY = {p.key: p for p in PATCHES}


@dataclass
class Session:
    workdir: Path
    input_path: Path
    original_name: str
    selected: set[str] = field(default_factory=lambda: {"default"})
    android_id: Optional[str] = None
    running: bool = False


SESSIONS: Dict[int, Session] = {}


def _authorized(user_id: int) -> bool:
    return not ALLOWED_USERS or user_id in ALLOWED_USERS


def _safe_name(name: str) -> str:
    name = Path(name).name
    stem = re.sub(r"[^A-Za-z0-9._() -]+", "_", name).strip(" .")
    return stem or "uploaded.apk"


def _human_size(n: int) -> str:
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _session_text(session: Session) -> str:
    selected = [PATCH_BY_KEY[k].label for k in session.selected if k in PATCH_BY_KEY]
    selected.sort()
    lines = [
        "✅ <b>APK Uploaded!</b>",
        "",
        f"📁 <b>File:</b> <code>{session.original_name}</code>",
        f"📦 <b>Size:</b> {_human_size(session.input_path.stat().st_size)}",
        "",
        "👇 <b>Select patches below — tap to toggle on/off:</b>",
        "",
        f"Selected: <b>{len(selected)}</b>",
    ]
    if session.android_id:
        lines.append(f"Android ID: <code>{session.android_id}</code>")
    return "\n".join(lines)


def _keyboard(session: Session) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for patch in PATCHES:
        checked = "✅" if patch.key in session.selected else "▫️"
        rows.append(
            [InlineKeyboardButton(f"{checked} {patch.label}", callback_data=f"patch:{patch.key}")]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton("🚀 Start Patching", callback_data="action:start"),
                InlineKeyboardButton("🔄 Clear All", callback_data="action:clear"),
            ],
            [
                InlineKeyboardButton("📎 Upload New APK", callback_data="action:new"),
                InlineKeyboardButton("⚙️ Settings", callback_data="action:settings"),
            ],
        ]
    )
    return InlineKeyboardMarkup(rows)


def _clean_output(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\r", "").strip()


def _stage_from_line(line: str) -> tuple[int, str] | None:
    low = line.lower()
    if "scan" in low:
        return 10, "Scanning APK…"
    if "decompile" in low and "successful" not in low:
        return 20, "Decompiling APK…"
    if "decompile successful" in low:
        return 35, "Decompile complete"
    if "patch" in low and "manifest" not in low and "final apk" not in low:
        return 55, "Applying selected patches…"
    if "manifest" in low:
        return 65, "Updating manifest…"
    if "recompile" in low and "successful" not in low:
        return 75, "Recompiling APK…"
    if "recompile successful" in low:
        return 85, "Recompile complete"
    if "signing" in low:
        return 92, "Signing APK…"
    if "sign successful" in low:
        return 97, "Signing complete"
    if "final apk" in low:
        return 100, "Patched APK ready"
    return None


def _progress_bar(percent: int) -> str:
    percent = max(0, min(100, percent))
    filled = round(percent / 10)
    return "█" * filled + "░" * (10 - filled)


async def _safe_edit(message, text: str, **kwargs) -> None:
    try:
        await message.edit_text(text, **kwargs)
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or not _authorized(user.id):
        if update.effective_message:
            await update.effective_message.reply_text("⛔ This bot is private.")
        return

    text = (
        "🛡️ <b>APK Patcher Bot</b>\n\n"
        "Send me an APK file and I will show the available patch options.\n\n"
        "<b>How to use:</b>\n"
        "1️⃣ Send an <code>.apk</code> file\n"
        "2️⃣ Toggle the patches you want\n"
        "3️⃣ Tap 🚀 <b>Start Patching</b>\n"
        "4️⃣ Receive the patched APK\n\n"
        "Use only on APKs you own or are authorized to test."
    )
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    session = SESSIONS.pop(user.id, None)
    if session and not session.running:
        shutil.rmtree(session.workdir, ignore_errors=True)
    await update.effective_message.reply_text("🔄 Session cleared. Send me a new APK file.")


async def receive_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    doc = msg.document if msg else None
    if not user or not doc:
        return
    if not _authorized(user.id):
        await msg.reply_text("⛔ This bot is private.")
        return

    filename = _safe_name(doc.file_name or "uploaded.apk")
    if not filename.lower().endswith(".apk"):
        await msg.reply_text("❌ Please send a normal <code>.apk</code> file.", parse_mode=ParseMode.HTML)
        return

    limit = MAX_APK_MB * 1024 * 1024
    if doc.file_size and doc.file_size > limit:
        await msg.reply_text(f"❌ APK is too large. Current bot limit: {MAX_APK_MB} MB.")
        return

    old = SESSIONS.get(user.id)
    if old and old.running:
        await msg.reply_text("⏳ Your current patch job is still running. Wait for it or use /reset later.")
        return
    if old:
        shutil.rmtree(old.workdir, ignore_errors=True)

    job_id = uuid.uuid4().hex[:10]
    workdir = BASE_WORKDIR / f"u{user.id}_{job_id}"
    workdir.mkdir(parents=True, exist_ok=False)
    unique_name = f"u{user.id}_{job_id}_{filename}"
    input_path = workdir / unique_name

    status = await msg.reply_text("⬇️ Downloading APK…")
    try:
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(custom_path=input_path)
    except TelegramError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        await _safe_edit(status, f"❌ Download failed: <code>{type(exc).__name__}</code>", parse_mode=ParseMode.HTML)
        return

    session = Session(workdir=workdir, input_path=input_path, original_name=filename)
    SESSIONS[user.id] = session
    await _safe_edit(
        status,
        _session_text(session),
        parse_mode=ParseMode.HTML,
        reply_markup=_keyboard(session),
    )


async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    await query.answer()

    if not _authorized(user.id):
        await query.answer("Private bot", show_alert=True)
        return

    session = SESSIONS.get(user.id)
    if not session:
        await query.message.reply_text("📎 Send me an APK first.")
        return

    data = query.data or ""
    if data.startswith("patch:"):
        if session.running:
            await query.answer("A patch job is already running.", show_alert=True)
            return
        key = data.split(":", 1)[1]
        if key not in PATCH_BY_KEY:
            return
        if key == "androidid":
            if key in session.selected:
                session.selected.remove(key)
                session.android_id = None
            else:
                context.user_data["awaiting_android_id"] = True
                await query.message.reply_text(
                    "🆔 Send the 16-character Android ID you want to use.\n"
                    "Example: <code>7e9f51f096bd5c83</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
        elif key in session.selected:
            session.selected.remove(key)
        else:
            session.selected.add(key)

        await _safe_edit(
            query.message,
            _session_text(session),
            parse_mode=ParseMode.HTML,
            reply_markup=_keyboard(session),
        )
        return

    if data == "action:clear":
        if session.running:
            await query.answer("A patch job is already running.", show_alert=True)
            return
        session.selected.clear()
        session.android_id = None
        await _safe_edit(
            query.message,
            _session_text(session),
            parse_mode=ParseMode.HTML,
            reply_markup=_keyboard(session),
        )
        return

    if data == "action:new":
        if session.running:
            await query.answer("A patch job is already running.", show_alert=True)
            return
        shutil.rmtree(session.workdir, ignore_errors=True)
        SESSIONS.pop(user.id, None)
        await query.message.reply_text("📎 Send your new APK file.")
        return

    if data == "action:settings":
        await query.message.reply_text(
            f"⚙️ <b>Bot settings</b>\n"
            f"Max APK: <b>{MAX_APK_MB} MB</b>\n"
            f"Job timeout: <b>{JOB_TIMEOUT // 60} min</b>\n"
            f"Workers: <b>{WORKERS}</b>",
            parse_mode=ParseMode.HTML,
        )
        return

    if data == "action:start":
        if session.running:
            await query.answer("Already patching.", show_alert=True)
            return
        if not session.selected:
            await query.answer("Select at least one patch.", show_alert=True)
            return
        if "androidid" in session.selected and not session.android_id:
            await query.answer("Set Android ID first.", show_alert=True)
            return
        asyncio.create_task(run_patch_job(user.id, query.message, context))


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    msg = update.effective_message
    if not user or not msg or not msg.text:
        return
    if not context.user_data.pop("awaiting_android_id", False):
        return

    session = SESSIONS.get(user.id)
    if not session or session.running:
        await msg.reply_text("📎 Send an APK first.")
        return

    android_id = msg.text.strip()
    if not re.fullmatch(r"[0-9A-Fa-f]{16}", android_id):
        context.user_data["awaiting_android_id"] = True
        await msg.reply_text("❌ Android ID must be exactly 16 hexadecimal characters.")
        return

    session.android_id = android_id.lower()
    session.selected.add("androidid")
    await msg.reply_text(f"✅ Android ID set: <code>{session.android_id}</code>", parse_mode=ParseMode.HTML)


def _build_command(session: Session) -> list[str]:
    # Use the package's installed console entry point. This preserves all of
    # the original engine's dependency checks and patch behavior.
    command = ["apkpatcher", "-i", str(session.input_path)]

    selected = set(session.selected)
    selected.discard("default")

    # These flags can be combined with the normal smali patch path.
    for key in ("flutter", "package", "screenshot", "usb"):
        if key in selected:
            command.extend(PATCH_BY_KEY[key].flags)

    if "androidid" in selected and session.android_id:
        command.extend(["-D", session.android_id])

    # The upstream engine intentionally routes these through separate patch
    # modes. Do not silently combine mutually incompatible short-circuit modes.
    special = [key for key in ("ads", "telegram", "algorithm") if key in selected]
    if len(special) > 1:
        raise ValueError(
            "Ad Removal, Telegram Patcher and Algorithm Logs are separate upstream modes; "
            "select only one of them per job."
        )
    if special:
        command.extend(PATCH_BY_KEY[special[0]].flags)

    return command


async def run_patch_job(user_id: int, menu_message, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = SESSIONS.get(user_id)
    if not session or session.running:
        return

    try:
        command = _build_command(session)
    except ValueError as exc:
        await menu_message.reply_text(f"⚠️ {exc}")
        return

    session.running = True
    progress = await menu_message.reply_text(
        "🚀 <b>Patching APK…</b>\n\n"
        f"📁 APK: <code>{session.original_name}</code>\n"
        f"🛠️ Patches: <b>{len(session.selected)}</b>\n\n"
        "░░░░░░░░░░ 0%\n"
        "⏳ Waiting for patch worker…",
        parse_mode=ParseMode.HTML,
    )

    last_percent = -1
    log_tail: list[str] = []

    try:
        async with PATCH_SEMAPHORE:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(session.workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=os.environ.copy(),
            )

            async def consume() -> None:
                nonlocal last_percent
                assert proc.stdout is not None
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        break
                    line = _clean_output(raw.decode("utf-8", errors="replace"))
                    if not line:
                        continue
                    LOG.info("patch[%s] %s", user_id, line)
                    log_tail.append(line)
                    del log_tail[:-25]
                    stage = _stage_from_line(line)
                    if stage and stage[0] != last_percent:
                        last_percent = stage[0]
                        pct, label = stage
                        await _safe_edit(
                            progress,
                            "🚀 <b>Patching APK…</b>\n\n"
                            f"📁 APK: <code>{session.original_name}</code>\n"
                            f"🛠️ Patches: <b>{len(session.selected)}</b>\n\n"
                            f"{_progress_bar(pct)} {pct}%\n"
                            f"⏳ {label}",
                            parse_mode=ParseMode.HTML,
                        )

            try:
                await asyncio.wait_for(asyncio.gather(consume(), proc.wait()), timeout=JOB_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise RuntimeError(f"Patch job exceeded {JOB_TIMEOUT // 60} minutes")

            if proc.returncode != 0:
                tail = "\n".join(log_tail[-8:])[-3000:]
                raise RuntimeError(tail or f"apkpatcher exited with code {proc.returncode}")

        output = session.input_path.with_name(f"{session.input_path.stem}_Patched.apk")
        if not output.exists():
            raise RuntimeError("Patcher finished but the expected patched APK was not created.")

        await _safe_edit(
            progress,
            "✅ <b>Patching complete!</b>\n\n"
            "██████████ 100%\n"
            "📤 Uploading patched APK…",
            parse_mode=ParseMode.HTML,
        )

        send_name = f"{Path(session.original_name).stem}_Patched.apk"
        with output.open("rb") as fh:
            await context.bot.send_document(
                chat_id=menu_message.chat_id,
                document=fh,
                filename=send_name,
                caption="✅ Patched APK ready.",
            )
        await _safe_edit(progress, "✅ <b>Done.</b> Patched APK sent above.", parse_mode=ParseMode.HTML)

    except FileNotFoundError:
        await _safe_edit(
            progress,
            "❌ <b>apkpatcher command not found.</b>\n\n"
            "Install this repository first with <code>pip install -e .</code> and restart the bot.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        LOG.exception("Patch job failed for user %s", user_id)
        error = _clean_output(str(exc))[-3500:]
        await _safe_edit(
            progress,
            f"❌ <b>Patching failed.</b>\n\n<pre>{error}</pre>",
            parse_mode=ParseMode.HTML,
        )
    finally:
        session.running = False
        # Keep the uploaded file/session so the user can change selections and retry.


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOG.exception("Unhandled Telegram bot error", exc_info=context.error)


def main() -> None:
    if not TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set")

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.Document.ALL, receive_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text))
    app.add_error_handler(error_handler)

    LOG.info("Starting APK Patcher Telegram bot")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
