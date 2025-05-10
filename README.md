# yt-dlp_telegrambot
<img align="right" width="288" height="626" src="https://i.imgur.com/iRa51WQ.jpeg">
Telegram bot interface for [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [spotdl](https://github.com/spotDL/spotify-downloader). Only downloads formats that can be played by Telegram: mp4/aac/h264, max. 50MB.

### Setup
Get your own Telegram bot token: https://core.telegram.org/bots/features#creating-a-new-bot

Ask [@userinfobot](https://t.me/userinfobot) for your user ID number.

```
echo "TELEGRAM_BOT_TOKEN=4839574812:AAFD39kkdpWt3ywyRZergyOLMaJhac60qc" >> .env
echo "ADMIN_TELEGRAM_ID=12345678" >> .env
echo "DAILY_LIMIT=2" >> .env
docker compose build
docker compose up -d
```

There's a limit of 2 downloads per 24h per user, unless you add their User ID number to the whitelist with `/whitelist add <user ID>`. Only the admin-user can do this.

#### Cookies

To enable `yt-dlp` to download age-restricted content, supply `cookies.txt`. See:
- https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp
- https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies

### Example
https://t.me/savestreambot
