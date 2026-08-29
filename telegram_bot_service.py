#!/usr/bin/env python3
"""Production VPS launcher with a single-instance lock.

Prevents two ApkPatcher bot processes on the same VPS from polling the same
Telegram bot token at once. Launches the local Bot API version for large APKs.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

LOCK_PATH = Path(os.environ.get("APKPATCHER_INSTANCE_LOCK", "/tmp/apkpatcher-telegram-bot.lock"))


def _acquire_single_instance_lock():
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SystemExit(
            "Another ApkPatcher Telegram bot instance is already running on this VPS. "
            f"Lock: {LOCK_PATH}"
        ) from exc
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def main() -> None:
    lock_handle = _acquire_single_instance_lock()
    try:
        import telegram_bot_local
        telegram_bot_local.main()
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


if __name__ == "__main__":
    main()
