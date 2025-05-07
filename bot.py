import asyncio
import tempfile
import os
import subprocess
import shlex
import json
from datetime import datetime, timezone, timedelta
from filelock import FileLock
from telegram import Update, error
from telegram.ext import Application, CommandHandler, ContextTypes, Updater, CommandHandler, MessageHandler, filters, CallbackContext
from dotenv import load_dotenv
from typing import List
from itertools import product
from pprint import pprint
import re
import html

# Load environment variables
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
QUOTA_FILE = "data/quota.json"
LOCK_FILE = "data/quota.json.lock"
DAILY_LIMIT = 2
WHITELIST = set([72906842])  # Optional: add Telegram user IDs here that don't have a quota

def load_quota():
    if not os.path.exists(QUOTA_FILE):
        return {}
    with FileLock(LOCK_FILE):
        with open(QUOTA_FILE, 'r') as f:
            return json.load(f)

def save_quota(data):
    with FileLock(LOCK_FILE):
        with open(QUOTA_FILE, 'w') as f:
            json.dump(data, f)

def check_and_update_quota(user_id: str) -> bool:
    now = datetime.now(timezone.utc)
    quota = load_quota()
    user_id = str(user_id)
    user_data = [ts for ts in quota.get(user_id, []) if now - datetime.fromisoformat(ts).replace(tzinfo=timezone.utc) < timedelta(days=1)]

    if len(user_data) >= DAILY_LIMIT:
        return False

    user_data.append(now.isoformat())
    quota[user_id] = user_data
    save_quota(quota)
    return True

def clean_text(str: str, max_len = 0):
    str = re.sub(r'<[^>]+>', '', str)
    str = html.unescape(str)
    str = re.sub(r'[\x00-\x1f\x7f]', ' ', str)
    str = str.strip()
    if max_len and len(str) > max_len:
        return str[:max_len-1].strip() + '…'
    return str.strip()

async def ytdlp_fetch_metadata(update: Update, url: str) -> List:

    status_msg = await update.message.reply_text('Fetching metadata...', parse_mode="HTML")

    cmd = ['yt-dlp', '--dump-single-json', url]
    run = subprocess.run(cmd, capture_output=True, text=True)
    metadata = json.loads(run.stdout.strip())

    if metadata is None:
        await status_msg.edit_text('Could not get metadata 😞')
        return {'formats': [], 'duration': 1}

    str = f'<b>{clean_text(metadata['title'], 40)}</b>\n' + \
        f'<b>Duration</b> {metadata['duration']} seconds, <b>uploaded</b> {clean_text(metadata['upload_date'], 20)} <b>by</b> {clean_text(metadata['uploader'], 50)}\n' + \
        f'<b>Description</b> {clean_text(metadata['description'], 60)}\n'
    
    await status_msg.edit_text(str, parse_mode="HTML")
    
    return metadata

def ytdlp_best_streams(formats_in, duration, max_size_bytes) -> List:

    max_bitrate = max_size_bytes * 8 / duration

    video_allowed = ['mp4', 'h264']
    audio_allowed = ['mp4', 'mp3', 'aac', 'm4a']

    formats = {'both': [], 'video': [], 'audio': []}
    for f in formats_in:

        if (f.get('filesize') is None or f.get('filesize') > max_size_bytes) and (f.get('tbr') is None or f.get('tbr') > max_bitrate):
            continue

        size = f['filesize'] if f.get('filesize') is not None else duration * f['tbr'] * 1024 / 8

        video_ok = f.get('vcodec') in video_allowed or f.get('video_ext') in video_allowed
        audio_ok = f.get('acodec') in audio_allowed or f.get('audio_ext') in audio_allowed

        print(f['format_id'], video_ok, audio_ok, size)

        if video_ok and audio_ok:
            formats['both'].append([ size, [f['format_id']] ])
        elif video_ok and f['audio_ext'] in [None, 'none']:
            formats['video'].append([ size, f['format_id'] ])
        elif audio_ok and f['video_ext'] in [None, 'none']:
            formats['audio'].append([ size, f['format_id'] ])

    for audio, video in product(formats['audio'], formats['video']):
        size = audio[0] + video[0]
        if size <= max_size_bytes:
            formats['both'].append([ size, [audio[1], video[1]] ])

    formats_both = sorted(formats['both'], key=lambda x: -x[0])

    if formats_both:
        return formats_both[0][1]
    else:
        return []

async def download(update: Update, url: str, audio_only=False):

    max_size_bytes = 49*1024*1024  # 49 MB in bytes
    user_id = update.effective_user.id
    print(f'userID {update.effective_user.id} download: {url}')

    if not check_and_update_quota(user_id) and user_id not in WHITELIST:
        print(f'quota exceeded')
        await update.message.reply_text(f"Quota exceeded: {DAILY_LIMIT} downloads per 24 hours. (User ID: {user_id})")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            if url.startswith('https://open.spotify.com/track/'):
                # spotdl
                audio_only = True
                await spotdl_get_download(update, url, tmpdir)
            else:
                # yt-dlp
                metadata = await ytdlp_fetch_metadata(update, url)
                streams = ytdlp_best_streams(metadata['formats'], metadata['duration'], max_size_bytes)
                await ytdlp_get_download(update, url, tmpdir, streams, audio_only)
        except:
            pass

        await send_download(update, tmpdir, audio_only)

async def ytdlp_get_download(update: Update, url: str, dir: str, streams: List[str], audio_only=False):

    if not streams:
        return
    
    cmd_streams = '-f ' + '+'.join(streams)
    if audio_only:
        cmd = f"yt-dlp -x --newline --progress-delta 2 {cmd_streams} -o '{dir}/%(title)s.%(ext)s' {shlex.quote(url)}"
    else:
        cmd = f"yt-dlp --newline --progress-delta 2 {cmd_streams} -o '{dir}/%(title)s.%(ext)s' {shlex.quote(url)}"

    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=dir
    )

    status_msg = await update.message.reply_text("Starting download...")
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        try:
            await status_msg.edit_text(f"Downloading:\n`{clean_text(line.decode(),60)}`", parse_mode="Markdown")
        except error.BadRequest:
            pass

    await process.wait()
    await status_msg.delete()

async def spotdl_get_download(update: Update, url: str, dir: str):
    
    cmd = f"spotdl download {shlex.quote(url)}"
    process = await asyncio.create_subprocess_shell(cmd, cwd=dir)

    status_msg = await update.message.reply_text("Downloading...")
    await process.wait()
    
    await status_msg.delete()

async def send_download(update: Update, dir: str, audio_only=False):
    files = os.listdir(dir)
    if not files:
        await update.message.reply_text("Download failed 😞")
        return
    
    status_msg = await update.message.reply_text('Download complete 😀 sending file...')
    filepath = os.path.join(dir, files[0])

    try:
        if audio_only:
            print('send audio')
            await update.message.reply_audio(audio=open(filepath, 'rb'))
        else:
            print('send video')
            await update.message.reply_video(video=open(filepath, 'rb'))
    except Exception as e:
        try:
            print('send document')
            await update.message.reply_document(document=open(filepath, 'rb'))
        except Exception as e:
            await update.message.reply_text(f"Failed to send: {e}")

    await status_msg.delete()

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text and not text.startswith('/'):
        await download(update, text, audio_only=False)

async def video_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await download(update, context.args[0], audio_only=False)
    else:
        await update.message.reply_text("Usage: /video <URL>")

async def audio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args:
        await download(update, context.args[0], audio_only=True)
    else:
        await update.message.reply_text("Usage: /audio <URL>")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Send /video <url> or /audio <url> to download.")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("video", video_command))
    app.add_handler(CommandHandler("audio", audio_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
