# yt-dlp_telegrambot
Telegram bot interface for [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [spotdl](https://github.com/spotDL/spotify-downloader).

## Setup
Get a Telegram bot token: https://core.telegram.org/bots/features#creating-a-new-bot

```
echo "TELEGRAM_BOT_TOKEN=4839574812:AAFD39kkdpWt3ywyRZergyOLMaJhac60qc" >> .env
docker compose build
docker compose up -d
```

There's a daily limit of 2 downloads per user, unless you add your User ID to the WHITELIST in bot.py.

## Example

https://t.me/download_yt_dlp_bot
