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
cmake --build . --target install -j"$(nproc)"
```

The built binary is normally available under `/opt/telegram-bot-api/bin/telegram-bot-api`.

## Local Bot API environment

Create `/opt/apkpatching/telegram-api.env`:

```env
TELEGRAM_API_ID=YOUR_API_ID
TELEGRAM_API_HASH=YOUR_API_HASH
```

Then:

```bash
chmod 600 /opt/apkpatching/telegram-api.env
sudo mkdir -p /var/lib/telegram-bot-api /var/tmp/telegram-bot-api
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
EnvironmentFile=/opt/apkpatching/telegram-api.env
ExecStart=/opt/telegram-bot-api/bin/telegram-bot-api --local --http-port=8081 --dir=/var/lib/telegram-bot-api --temp-dir=/var/tmp/telegram-bot-api
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
TELEGRAM_BOT_API_BASE_URL=http://127.0.0.1:8081/bot
TELEGRAM_BOT_API_FILE_URL=http://127.0.0.1:8081/file/bot
```

The URLs intentionally end in `/bot`; python-telegram-bot appends the bot token itself.

If the bot token was previously being used through Telegram's hosted Bot API, Telegram recommends calling the Bot API `logOut` method before switching that bot to a local Bot API server.

## Restart the bot

```bash
sudo systemctl restart telegram-bot-api
sudo systemctl restart apkpatcher-bot
sudo journalctl -u telegram-bot-api -u apkpatcher-bot -f
```

## Notes

- `APKPATCHER_MAX_APK_MB` is an application safety cap, not Telegram's local-server hard limit.
- 500 MB is a reasonable starting cap for VPS use; large APK decompilation can consume several times the APK size in disk and RAM.
- Keep `APKPATCHER_WORKERS=1` until VPS memory/disk usage has been tested under real workloads.
- Do not expose port 8081 publicly. Bind/use it locally through `127.0.0.1` as shown above.
