#!/usr/bin/env python3
"""Run the ApkPatcher Telegram bot with detailed live patch output.

This module reuses telegram_bot.py and replaces its patch-job renderer.
The VPS uses the normal Linux APKTool first and automatically retries with the
upstream APKEditor mode (-a) only when APKTool reports a compatibility failure.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
import shutil
import time
from collections import deque
from pathlib import Path

import telegram_bot as bot
from telegram.constants import ParseMode


LIVE_LINES = max(5, min(20, int(os.environ.get("APKPATCHER_LIVE_LINES", "12"))))
LIVE_EDIT_INTERVAL = max(0.8, float(os.environ.get("APKPATCHER_LIVE_EDIT_INTERVAL", "1.5")))
HEARTBEAT_INTERVAL = max(10.0, float(os.environ.get("APKPATCHER_HEARTBEAT_INTERVAL", "15")))

SMALI_RE = re.compile(r"([^/\\\s]+\.smali)\b", re.I)
PATTERN_COUNT_RE = re.compile(r"Pattern Applied.*?(\d+)\s*Time", re.I)
APKEDITOR_RETRY_MARKERS = (
    "failed with apktool",
    "try with apkeditor",
    "flag -a",
)


def _display_event(line: str) -> str | None:
    """Convert noisy terminal lines to concise Telegram-friendly events."""
    clean = bot._clean_output(line)
    if not clean:
        return None

    low = clean.lower()

    if set(clean) <= {"_", "-", "=", "|", " "}:
        return None
    if "[ pattern ]" in low and "\\.method" in low:
        return None
    if clean.startswith("~$") or "java -jar" in low:
        return None

    if "flutter native analysis" in low:
        return "🔬 Flutter native library analysis"
    if "searching for flutter ssl offset" in low:
        return "🔎 Searching Flutter SSL signature"
    if "ssl_verify_peer_cert" in low and "patched successfully" in low:
        return "✅ Flutter SSL function patched"

    m = PATTERN_COUNT_RE.search(clean)
    if m:
        return f"✅ Pattern applied × {m.group(1)}"

    smali = SMALI_RE.search(clean)
    if smali and ("✔" in clean or "✓" in clean or "└" in clean or "pattern" in low):
        return f"✅ {smali.group(1)}"

    if "[ tag ]" in low:
        text = re.sub(r"^.*?\[\s*tag\s*\]\s*", "", clean, flags=re.I).strip()
        return f"🔎 {text[:120]}" if text else None

    if "[ skip patch ]" in low:
        text = re.sub(r"^.*?\[\s*skip patch\s*\]\s*", "", clean, flags=re.I).strip()
        return f"⏭️ Skip: {text[:120]}" if text else None

    if "androidmanifest.xml" in low and ("updated" in low or "✔" in clean or "✓" in clean):
        if "networksecurityconfig" in low:
            return "📝 AndroidManifest: networkSecurityConfig updated"
        if "usescleartexttraffic" in low:
            return "📝 AndroidManifest: usesCleartextTraffic updated"
        return "📝 AndroidManifest.xml updated"

    if "write default certificate" in low or ("certificate" in low and ".pem" in low):
        return "📜 Default certificate written"

    if "write network config" in low or "network_security_config.xml" in low:
        return "📄 Network security config written"

    if "decompile successful" in low:
        return "✅ APK decompiled"
    if "decompile apk" in low:
        return "🔧 Decompiling APK…"
    if "recompile successful" in low:
        return "✅ APK recompiled"
    if "recompile apk" in low:
        return "📦 Recompiling APK…"
    if "sign successful" in low:
        return "✅ APK signed"
    if "signing apk" in low:
        return "🔐 Signing APK…"
    if "final apk" in low:
        return "🎉 Patched APK created"

    useful = (
        "[ updated ]", "[ certificate ]", "[ write ", "[ patch", "scanning",
        "patching", "manifest", "successful", "created", "inject", "hook",
    )
    if any(x in low for x in useful) and len(clean) <= 180:
        return f"• {clean}"

    return None


def _update_stats(stats: dict[str, int], line: str) -> None:
    low = line.lower()
    match = PATTERN_COUNT_RE.search(line)
    if match:
        stats["patterns"] += int(match.group(1))
    if SMALI_RE.search(line) and ("✔" in line or "✓" in line or "pattern" in low):
        stats["smali"] += 1
    if "androidmanifest.xml" in low and ("updated" in low or "✔" in line or "✓" in line):
        stats["manifest"] += 1
    if "certificate" in low and ("write" in low or ".pem" in low):
        stats["certificates"] += 1
    if "sign successful" in low:
        stats["signed"] = 1


def _render(session, percent: int, stage: str, events, stats: dict[str, int]) -> str:
    escaped_events = [html.escape(x) for x in events]
    activity = "\n".join(escaped_events) if escaped_events else "Waiting for patcher output…"
    return (
        "🚀 <b>Patching APK…</b>\n\n"
        f"📁 <b>APK:</b> <code>{html.escape(session.original_name)}</code>\n"
        f"🛠️ <b>Patches:</b> {len(session.selected)}\n\n"
        f"{bot._progress_bar(percent)} <b>{percent}%</b>\n"
        f"⏳ {html.escape(stage)}\n\n"
        "<b>Live activity</b>\n"
        f"<pre>{activity}</pre>\n"
        f"🔧 Smali: <b>{stats['smali']}</b>  •  Patterns: <b>{stats['patterns']}</b>  •  Manifest: <b>{stats['manifest']}</b>"
    )


def _should_retry_with_apkeditor(command: list[str], lines: list[str]) -> bool:
    if "-a" in command:
        return False
    text = "\n".join(lines[-30:]).lower()
    return any(marker in text for marker in APKEDITOR_RETRY_MARKERS)


def _cleanup_retry_artifacts(session) -> None:
    """Remove only artifacts belonging to this job before a clean attempt."""
    stem = session.input_path.stem
    home = Path.home().resolve()

    decompile_dir = (home / f"{stem}_decompiled").resolve()
    sig_dir = (home / f"{stem}_SigBlock").resolve()

    for directory in (decompile_dir, sig_dir):
        if directory.parent == home and directory.exists():
            shutil.rmtree(directory, ignore_errors=True)

    for suffix in ("_Patched.apk", "_Patch.apk"):
        output = session.input_path.with_name(f"{stem}{suffix}")
        try:
            output.unlink()
        except FileNotFoundError:
            pass


def _patch_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("TERM", "xterm")
    env.setdefault("HOME", str(Path.home()))
    # The patcher is a Python console process whose stdout is piped into this
    # bot. Disable block buffering so stage messages emitted between Java/r2
    # subprocesses reach Telegram immediately.
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


async def run_patch_job_live(user_id: int, menu_message, context) -> None:
    session = bot.SESSIONS.get(user_id)
    if not session or session.running:
        return

    try:
        command = bot._build_command(session)
    except ValueError as exc:
        await menu_message.reply_text(f"⚠️ {exc}")
        return

    session.running = True
    events: deque[str] = deque(maxlen=LIVE_LINES)
    stats = {"smali": 0, "patterns": 0, "manifest": 0, "certificates": 0, "signed": 0}
    percent, stage = 0, "Waiting for patch worker…"

    progress = await menu_message.reply_text(
        _render(session, percent, stage, events, stats), parse_mode=ParseMode.HTML
    )

    log_tail: list[str] = []
    last_edit = 0.0
    dirty = False

    async def refresh(force: bool = False) -> None:
        nonlocal last_edit, dirty
        now = time.monotonic()
        if not force and (not dirty or now - last_edit < LIVE_EDIT_INTERVAL):
            return
        try:
            await bot._safe_edit(
                progress,
                _render(session, percent, stage, events, stats),
                parse_mode=ParseMode.HTML,
            )
            last_edit = now
            dirty = False
        except Exception:
            bot.LOG.debug("Live Telegram progress update failed", exc_info=True)

    async def run_attempt(attempt_command: list[str]) -> int:
        """Run one patcher attempt and stream its output into the same UI."""
        nonlocal percent, stage, dirty

        started_at = time.monotonic()
        last_output_at = started_at
        last_real_stage = stage

        proc = await asyncio.create_subprocess_exec(
            *attempt_command,
            cwd=str(session.workdir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=_patch_env(),
        )

        async def consume() -> None:
            nonlocal percent, stage, dirty, last_output_at, last_real_stage
            assert proc.stdout is not None
            while True:
                raw = await proc.stdout.readline()
                if not raw:
                    break
                line = bot._clean_output(raw.decode("utf-8", errors="replace"))
                if not line:
                    continue

                last_output_at = time.monotonic()
                bot.LOG.info("patch[%s] %s", user_id, line)
                log_tail.append(line)
                del log_tail[:-30]

                low = line.lower()
                if "flutter native analysis" in low:
                    percent, stage = 70, "Analyzing Flutter native library…"
                    last_real_stage = stage
                    dirty = True
                elif "searching for flutter ssl offset" in low:
                    percent, stage = 73, "Searching Flutter SSL signature…"
                    last_real_stage = stage
                    dirty = True
                elif "ssl_verify_peer_cert" in low and "patched successfully" in low:
                    percent, stage = 75, "Flutter SSL patch applied"
                    last_real_stage = stage
                    dirty = True
                else:
                    parsed_stage = bot._stage_from_line(line)
                    if parsed_stage:
                        percent, stage = parsed_stage
                        last_real_stage = stage
                        dirty = True

                _update_stats(stats, line)
                event = _display_event(line)
                if event and (not events or events[-1] != event):
                    events.append(event)
                    dirty = True

                await refresh()

        async def heartbeat() -> None:
            nonlocal stage, dirty
            while proc.returncode is None:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                if proc.returncode is not None:
                    break

                now = time.monotonic()
                silence = now - last_output_at
                elapsed = now - started_at
                stage = (
                    f"{last_real_stage} — still working "
                    f"({_format_duration(elapsed)} elapsed; "
                    f"{_format_duration(silence)} since last output)"
                )
                dirty = True
                await refresh(force=True)

        try:
            await asyncio.wait_for(
                asyncio.gather(consume(), proc.wait(), heartbeat()),
                timeout=bot.JOB_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(f"Patch attempt exceeded {bot.JOB_TIMEOUT // 60} minutes")

        await refresh(force=True)
        return int(proc.returncode or 0)

    try:
        async with bot.PATCH_SEMAPHORE:
            # Clean stale output from an interrupted/failed previous attempt of
            # this same job before starting with the normal Linux APKTool path.
            await asyncio.to_thread(_cleanup_retry_artifacts, session)
            returncode = await run_attempt(command)

            if returncode != 0 and _should_retry_with_apkeditor(command, log_tail):
                bot.LOG.warning(
                    "patch[%s] APKTool compatibility failure; retrying cleanly with APKEditor",
                    user_id,
                )

                await asyncio.to_thread(_cleanup_retry_artifacts, session)

                stats.update(smali=0, patterns=0, manifest=0, certificates=0, signed=0)
                events.clear()
                events.append("↻ APKTool failed; switching automatically to APKEditor")
                log_tail.clear()
                percent = 5
                stage = "Retrying with APKEditor compatibility mode…"
                dirty = True
                await refresh(force=True)

                command = [*command, "-a"]
                returncode = await run_attempt(command)

            if returncode != 0:
                tail = "\n".join(log_tail[-8:])[-3000:]
                raise RuntimeError(tail or f"apkpatcher exited with code {returncode}")

        output = session.input_path.with_name(f"{session.input_path.stem}_Patched.apk")
        if not output.exists():
            raise RuntimeError("Patcher finished but the expected patched APK was not created.")

        summary = (
            "✅ <b>Patching complete!</b>\n\n"
            "██████████ <b>100%</b>\n\n"
            "<b>Summary</b>\n"
            f"🔧 Smali files changed: <b>{stats['smali']}</b>\n"
            f"🧩 Patterns applied: <b>{stats['patterns']}</b>\n"
            f"📝 Manifest updates: <b>{stats['manifest']}</b>\n"
            f"📜 Certificate/config writes: <b>{stats['certificates']}</b>\n"
            f"🔐 Signing: <b>{'✅' if stats['signed'] else 'Completed by patcher'}</b>\n\n"
            "📤 Uploading patched APK…"
        )
        await bot._safe_edit(progress, summary, parse_mode=ParseMode.HTML)

        send_name = f"{Path(session.original_name).stem}_Patched.apk"
        with output.open("rb") as fh:
            await context.bot.send_document(
                chat_id=menu_message.chat_id,
                document=fh,
                filename=send_name,
                caption=(
                    "✅ Patched APK ready.\n"
                    f"Smali: {stats['smali']} • Patterns: {stats['patterns']} • Manifest: {stats['manifest']}"
                ),
            )
        await bot._safe_edit(
            progress,
            summary.replace("📤 Uploading patched APK…", "✅ Patched APK sent above."),
            parse_mode=ParseMode.HTML,
        )

    except FileNotFoundError:
        await bot._safe_edit(
            progress,
            "❌ <b>apkpatcher command not found.</b>\n\nInstall this repository first and restart the bot.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        bot.LOG.exception("Patch job failed for user %s", user_id)
        error = html.escape(bot._clean_output(str(exc))[-3200:])
        await bot._safe_edit(
            progress,
            f"❌ <b>Patching failed.</b>\n\n<pre>{error}</pre>",
            parse_mode=ParseMode.HTML,
        )
    finally:
        session.running = False


def main() -> None:
    bot.run_patch_job = run_patch_job_live
    bot.main()


if __name__ == "__main__":
    main()
