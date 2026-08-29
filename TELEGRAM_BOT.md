# APK Patcher Telegram Bot

`telegram_bot.py` adds an optional Telegram interface around the existing ApkPatcherX CLI. The patch engine itself is not replaced or modified by this wrapper.

## Features

- Upload an `.apk` file through Telegram.
- Select supported patch modes with inline buttons.
- Run the existing `apkpatcher` CLI in an isolated per-job directory.
- Show patching progress/status in the chat.
- Optionally show detailed live patch activity (smali files, pattern counts, manifest/config writes, signing stages).
- Return the generated `_Patched.apk` to the user.
- Optionally restrict the bot to specific Telegram user IDs.

Use this only with APKs you own or are authorized to test or modify.

## Installation

Install ApkPatcherX with the optional Telegram dependency:

```bash
python -m pip install -e '.[telegram]'
```

The original ApkPatcher dependencies (Java, aapt2 and the existing tool files) are still required.

## Configuration

Set a BotFather token:

```bash
export TELEGRAM_BOT_TOKEN='YOUR_BOTFATHER_TOKEN'
```

Optional runtime settings:

```bash
# Maximum accepted APK size in MB (default 20)
export APKPATCHER_MAX_APK_MB=20

# Maximum runtime for one patch job in seconds (default 1800)
export APKPATCHER_JOB_TIMEOUT=1800

# Number of concurrent patch workers (default 1)
export APKPATCHER_WORKERS=1

# Comma-separated Telegram numeric user IDs. Empty means public bot.
export APKPATCHER_ALLOWED_USERS='123456789'

# Optional job directory
export APKPATCHER_BOT_WORKDIR="$HOME/apkpatcher-bot-jobs"

# Live-mode options
export APKPATCHER_LIVE_LINES=12
export APKPATCHER_LIVE_EDIT_INTERVAL=1.5
```

Do not commit your BotFather token to GitHub.

## Run

Standard progress mode:

```bash
python telegram_bot.py
```

Detailed live progress mode:

```bash
python telegram_bot_live.py
```

Then send `/start` to the bot and upload an APK as a Telegram document.

## Commands

- `/start` — show instructions
- `/help` — show instructions
- `/reset` — clear the current APK session

## Design notes

- The Telegram layer invokes the existing `apkpatcher` command as a subprocess instead of duplicating patch logic.
- The live renderer parses the existing CLI output; it does not alter the patch engine.
- Uploaded APKs use unique job paths so different users do not share the same working filename.
- Jobs are serialized by default because the existing CLI uses shared runtime resources.
- Telegram support is an optional dependency, so normal ApkPatcherX installations remain unchanged.
