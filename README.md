# yt-dlp_telegrambot
Telegram bot interface for [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [spotdl](https://github.com/spotDL/spotify-downloader). Only downloads formats that can be played by Telegram: mp4/aac/h264, max. 50MB.

## Setup
Get a Telegram bot token: https://core.telegram.org/bots/features#creating-a-new-bot

```
echo "TELEGRAM_BOT_TOKEN=4839574812:AAFD39kkdpWt3ywyRZergyOLMaJhac60qc" >> .env
echo "ADMIN_TELEGRAM_ID=12345678" >> .env
echo "DAILY_LIMIT=2" >> .env
docker compose build
docker compose up -d
```

There's a daily limit of 2 downloads per user, unless you add your User ID to the whitelist with /whitelist add <user ID>. Only the admin-user can do this.

## Example

https://t.me/download_yt_dlp_bot
