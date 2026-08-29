# APK Patcher Telegram Bot

`telegram_bot.py` is a Telegram UI wrapper around the existing ApkPatcherX CLI. The original patch engine remains unchanged.

## What the bot does

1. User sends an `.apk` document to the Telegram bot.
2. The bot stores it in an isolated per-job directory.
3. Inline buttons are shown for the available patch modes.
4. The selected options are translated to the existing ApkPatcherX CLI flags.
5. The existing `apkpatcher` command runs as a subprocess.
6. Progress/status messages are updated in Telegram.
7. The resulting `_Patched.apk` is sent back to the chat.

Use the bot only with APKs you own or are authorized to test or modify.

## Termux setup

Clone your fork and enter it:

```bash
git clone https://github.com/khan23153/Apkpatching.git
cd Apkpatching
git checkout telegram-bot
```

Install/update the Termux packages used by the original patcher:

```bash
pkg update -y
pkg install -y python openjdk-17 aapt2 git
```

Install this repository and its Python dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e .
```

Set the BotFather token for the current shell:

```bash
export TELEGRAM_BOT_TOKEN='YOUR_BOTFATHER_TOKEN'
```

Optional: make the bot private by allowing only your Telegram numeric user ID:

```bash
export APKPATCHER_ALLOWED_USERS='123456789'
```

Run it:

```bash
python telegram_bot.py
```

Then open the bot in Telegram, send `/start`, and upload an APK as a **document**.

## Keep it running in Termux

A simple `tmux` setup:

```bash
pkg install -y tmux
tmux new -s apkbot
```

Inside the tmux session:

```bash
cd ~/Apkpatching
export TELEGRAM_BOT_TOKEN='YOUR_BOTFATHER_TOKEN'
python telegram_bot.py
```

Detach with `Ctrl+b`, then `d`. Re-open later with:

```bash
tmux attach -t apkbot
```

## Environment options

```bash
# Required
TELEGRAM_BOT_TOKEN=...

# Maximum uploaded APK accepted by this wrapper (default 20 MB)
APKPATCHER_MAX_APK_MB=20

# Maximum patch job runtime in seconds (default 30 minutes)
APKPATCHER_JOB_TIMEOUT=1800

# Keep at 1 for the safest operation with the upstream CLI.
APKPATCHER_WORKERS=1

# Comma-separated Telegram numeric user IDs. Empty = public bot.
APKPATCHER_ALLOWED_USERS=

# Optional job directory
APKPATCHER_BOT_WORKDIR=/data/data/com.termux/files/home/apkpatcher-bot-jobs
```

## Commands

- `/start` — show instructions
- `/help` — show instructions
- `/reset` — clear the current APK session

## Notes

- The Telegram layer does not replace the patch engine; it invokes the existing `apkpatcher` command.
- JAR/tool downloads and checks performed by the original ApkPatcherX code still apply.
- Uploaded files receive a unique job prefix to prevent two Telegram users from sharing the same working filename.
- Patch jobs are serialized by default (`APKPATCHER_WORKERS=1`) because the upstream CLI uses shared resources under the user's home/runtime paths.
- Do not commit your BotFather token to GitHub. Keep it in an environment variable.
