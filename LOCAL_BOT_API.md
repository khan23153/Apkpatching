# Large APK support with Telegram Local Bot API

Telegram's hosted Bot API has smaller file transfer limits. For large APKs, run Telegram's official open-source Bot API server on the same VPS and point this bot at it.

With a local Bot API server, Telegram documents support downloads without a Bot API file-size limit and uploads up to 2000 MB. This project defaults its own safety cap to 500 MB when configured for large-file use.

## Required Telegram API credentials

Create an application at https://my.telegram.org and obtain:

- `TELEGRAM_API_ID`
- `TELEGRAM_API_HASH`

These are different from `TELEGRAM_BOT_TOKEN`. Keep all three secret.

## Build the official local Bot API server on Ubuntu/Debian

```bash
sudo apt-get update
sudo apt-get install -y make git zlib1g-dev libssl-dev gperf cmake g++

cd /opt
sudo git clone --recursive https://github.com/tdlib/telegram-bot-api.git
sudo chown -R "$USER":"$USER" /opt/telegram-bot-api
cd /opt/telegram-bot-api
rm -rf build
mkdir build
cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
cmake --build . -j"$(nproc)"
sudo cmake --install .
```

The installed binary is normally `/usr/local/bin/telegram-bot-api`.

## Local Bot API environment

Create `/opt/apkpatching/telegram-api.env`:

```env
TELEGRAM_API_ID=YOUR_API_ID
TELEGRAM_API_HASH=YOUR_API_HASH
```

Keep the local Bot API server and the ApkPatcher bot under the same Linux user so files returned by `getFile` are directly readable by the patch bot. On an Ubuntu VPS where the project runs as `ubuntu`:

```bash
sudo chown ubuntu:ubuntu /opt/apkpatching/telegram-api.env
chmod 600 /opt/apkpatching/telegram-api.env

sudo mkdir -p /var/lib/telegram-bot-api /var/tmp/telegram-bot-api
sudo chown -R ubuntu:ubuntu /var/lib/telegram-bot-api /var/tmp/telegram-bot-api
```

## systemd service for Telegram Bot API

```bash
sudo tee /etc/systemd/system/telegram-bot-api.service >/dev/null <<'EOF'
[Unit]
Description=Telegram Local Bot API Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
EnvironmentFile=/opt/apkpatching/telegram-api.env
ExecStart=/usr/local/bin/telegram-bot-api --local --http-ip-address=127.0.0.1 --http-port=8081 --dir=/var/lib/telegram-bot-api --temp-dir=/var/tmp/telegram-bot-api
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot-api
sudo systemctl status telegram-bot-api --no-pager
```

The server reads `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from the environment.

## Point ApkPatcher bot at the local server

Add these values to `/opt/apkpatching/bot.env`:

```env
APKPATCHER_MAX_APK_MB=500
APKPATCHER_WORKERS=1
TELEGRAM_BOT_API_BASE_URL=http://127.0.0.1:8081/bot
TELEGRAM_BOT_API_FILE_URL=http://127.0.0.1:8081/file/bot
TELEGRAM_BOT_API_DATA_DIR=/var/lib/telegram-bot-api
```

The Bot API URLs intentionally end in `/bot`; python-telegram-bot appends the bot token itself for normal API calls. Large incoming APKs are resolved through the raw local `getFile` response and copied directly from the local Bot API data directory.

If the bot token was previously being used through Telegram's hosted Bot API, Telegram recommends calling the Bot API `logOut` method before switching that bot to a local Bot API server.

## ApkPatcher bot systemd service

Run the patch bot as the same `ubuntu` user:

```bash
sudo chown ubuntu:ubuntu /opt/apkpatching/bot.env
chmod 600 /opt/apkpatching/bot.env
sudo mkdir -p /opt/apkpatching/work
sudo chown -R ubuntu:ubuntu /opt/apkpatching/work

sudo tee /etc/systemd/system/apkpatcher-bot.service >/dev/null <<'EOF'
[Unit]
Description=APK Patcher Telegram Bot
After=network-online.target telegram-bot-api.service
Requires=telegram-bot-api.service

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/apkpatching
EnvironmentFile=/opt/apkpatching/bot.env
Environment="PATH=/opt/apkpatching/vps-bin:/opt/apkpatching/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/opt/apkpatching/.venv/bin/python /opt/apkpatching/telegram_bot_service.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot-api apkpatcher-bot
sudo systemctl status telegram-bot-api apkpatcher-bot --no-pager
```

## Verification

```bash
ss -ltnp | grep 8081
systemctl show -p User -p Group telegram-bot-api apkpatcher-bot
sudo -u ubuntu test -r /var/lib/telegram-bot-api && echo "Bot API data root readable"
sudo journalctl -u telegram-bot-api -u apkpatcher-bot -n 100 --no-pager
```

## Notes

- `APKPATCHER_MAX_APK_MB` is an application safety cap, not Telegram's local-server hard limit.
- Keep `APKPATCHER_WORKERS=1` until VPS memory/disk usage has been tested under real workloads.
- Large APK decompilation can consume several times the APK size in disk and RAM.
- Keep port 8081 bound to `127.0.0.1`; do not expose it publicly.
- The Local Bot API service user must have read/write access to its data/temp directories, and the patch bot must be able to read files returned by `getFile`. Running both services as the same non-root user avoids permission drift.
