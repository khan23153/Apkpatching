# VPS deployment (Ubuntu / Debian)

This bot uses Telegram long polling, so no domain, nginx, webhook or open inbound port is required.

## 1. Install system packages

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip openjdk-17-jre-headless aapt
```

The Debian/Ubuntu `aapt` package provides both `aapt` and `aapt2` on supported releases.

Verify:

```bash
python3 --version
java -version
aapt2 version
```

## 2. Install the bot

```bash
cd /opt
sudo git clone https://github.com/khan23153/Apkpatching.git apkpatching
sudo chown -R "$USER":"$USER" /opt/apkpatching
cd /opt/apkpatching

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e .
chmod +x vps-bin/pkg vps-bin/termux-wake-lock vps-bin/termux-wake-unlock
```

## 3. Configure secrets

Create `/opt/apkpatching/bot.env`:

```bash
cat > /opt/apkpatching/bot.env <<'EOF'
TELEGRAM_BOT_TOKEN=PASTE_BOTFATHER_TOKEN_HERE
APKPATCHER_MAX_APK_MB=50
APKPATCHER_JOB_TIMEOUT=1800
APKPATCHER_WORKERS=1
# Optional private allow-list. Leave empty to allow everyone.
APKPATCHER_ALLOWED_USERS=
APKPATCHER_BOT_WORKDIR=/opt/apkpatching/work
EOF
chmod 600 /opt/apkpatching/bot.env
mkdir -p /opt/apkpatching/work
```

Do not commit `bot.env` or your BotFather token to GitHub.

## 4. Test in foreground

```bash
cd /opt/apkpatching
set -a
. ./bot.env
set +a
export PATH="/opt/apkpatching/vps-bin:/opt/apkpatching/.venv/bin:$PATH"
./.venv/bin/python telegram_bot.py
```

Open Telegram, send `/start`, then upload a test APK you own or are authorized to test.

Stop the foreground test with `Ctrl+C` after it works.

## 5. Run 24/7 with systemd

```bash
sudo tee /etc/systemd/system/apkpatcher-bot.service >/dev/null <<'EOF'
[Unit]
Description=APK Patcher Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/apkpatching
EnvironmentFile=/opt/apkpatching/bot.env
Environment="PATH=/opt/apkpatching/vps-bin:/opt/apkpatching/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/opt/apkpatching/.venv/bin/python /opt/apkpatching/telegram_bot.py
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now apkpatcher-bot
```

Check status/logs:

```bash
sudo systemctl status apkpatcher-bot --no-pager
sudo journalctl -u apkpatcher-bot -f
```

Restart after config changes:

```bash
sudo systemctl restart apkpatcher-bot
```

## Updating later

```bash
cd /opt/apkpatching
git pull
. .venv/bin/activate
python -m pip install -e .
sudo systemctl restart apkpatcher-bot
```

## Notes

- Start with `APKPATCHER_WORKERS=1`. APK decompilation/recompilation is CPU/RAM intensive and parallel jobs can exhaust a small VPS.
- 2 vCPU / 4 GB RAM is a practical minimum for small-to-medium APKs; 4 vCPU / 8 GB RAM is more comfortable.
- Telegram/Bot API file-size limits and your own `APKPATCHER_MAX_APK_MB` setting still apply.
- The `vps-bin` scripts only neutralize the upstream Termux-specific `pkg` and wake-lock checks. Actual Java and `aapt2` are installed through apt.
