import asyncio
import tempfile
import os
import subprocess
import shlex
import json
import uuid
import re
import html
import time
from datetime import datetime, timezone, timedelta
from filelock import FileLock
from telegram import Update, error, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CommandHandler, MessageHandler, filters, CallbackQueryHandler
from dotenv import load_dotenv
from itertools import product
from pprint import pprint

# Load environment variables
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_TELEGRAM_ID"))
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT"))
QUOTA_FILE = "data/quota.json"
LOCK_FILE = "data/quota.json.lock"
WHITELIST_FILE = "data/whitelist.json"
WHITELIST_LOCK_FILE = "data/whitelist.json.lock"

# Store callback data with timestamps for auto-cleanup (48 hours lifetime)
callback_payloads = {}

###

def load_whitelist():
    if not os.path.exists(WHITELIST_FILE):
        return []
    with FileLock(WHITELIST_LOCK_FILE):
        with open(WHITELIST_FILE, 'r') as f:
            return json.load(f)

def save_whitelist(data):
    with FileLock(WHITELIST_LOCK_FILE):
        with open(WHITELIST_FILE, 'w') as f:
            json.dump(data, f)

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

def check_and_update_quota(user_id: int) -> bool:
    now = datetime.now(timezone.utc)
    quota = load_quota()
    pprint(quota)

    user_data = [ts for ts in quota.get(str(user_id), []) if now - datetime.fromisoformat(ts).replace(tzinfo=timezone.utc) < timedelta(days=1)]
    if len(user_data) >= DAILY_LIMIT:
        return False

    user_data.append(now.isoformat())
    quota[str(user_id)] = user_data
    pprint(quota)
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

def cleanup_old_callbacks():
    """Remove callback_payloads items older than 48 hours"""
    now = time.time()
    expired_keys = [key for key, (payload, timestamp) in callback_payloads.items() 
                   if now - timestamp > 48 * 3600]
    
    for key in expired_keys:
        del callback_payloads[key]
    
    if expired_keys:
        print(f"Cleaned up {len(expired_keys)} expired callback payloads")

def is_whitelisted(user_id: int):
    whitelist = load_whitelist()
    return user_id in whitelist

def get_resolution_ffprobe(path: str):
    try:
        cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        data = json.loads(result.stdout)
        width = data['streams'][0]['width']
        height = data['streams'][0]['height']
        if width < 1 or height < 1:
            return None, None
        return width, height
    except:
        return None, None

def get_duration_ffprobe(path: str):
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', path]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, text=True)
        return float(result.stdout.strip())
    except:
        return None

def get_screenshot_ffmpeg(video_path: str, time: float):
    if time is None:
        return None
    
    image_path = os.path.join(os.path.dirname(video_path), 'screenshot.jpg')
    print(image_path)
    try:
        cmd = ['ffmpeg', '-v', 'error', '-ss', str(time), '-i', video_path, '-frames:v', '1', '-n', image_path]
        subprocess.run(cmd, check=True)
        return open(image_path, 'rb')
    except:
        return None

###

async def get_streams(message, url: str):

    user_id = message.chat_id
    print(f'userID {user_id} get_streams: {url}')

    base_msg = f'<code>{url}</code>'
    msg = await message.reply_text(base_msg+"\n\nFetching metadata...", parse_mode="HTML")

    if url.startswith('https://open.spotify.com/track/'):
        streams = await spotdl_get_streams(url)
    else:
        streams = await ytdlp_get_streams(url)

    #print('[streams]')
    #pprint(streams)

    base_msg += f'\n\n<b>{clean_text(streams["metadata"].get("title","unknown title"), 30)}</b>'
    base_msg += f'\n<b>Uploaded by</b> {clean_text(streams["metadata"].get("uploader","unknown"), 20)}'
    base_msg += f' <b>on</b> {clean_text(streams["metadata"].get("upload_date","unknown"), 10)}'
    
    if streams['streams']:
        keyboard = []
        for s in streams['streams']:
            uid = 'download:'+str(uuid.uuid4())
            callback_payloads[uid] = (s, time.time())
            keyboard.append(InlineKeyboardButton(s['label'], callback_data=uid))
    else:
        uid = 'get:'+str(uuid.uuid4())
        callback_payloads[uid] = (url, time.time())
        keyboard = [InlineKeyboardButton('🔄 retry', callback_data=uid)]
    
    await msg.edit_text(base_msg + f"\n\n{len(streams['streams'])} suitable download option(s) found:", reply_markup=InlineKeyboardMarkup([keyboard]), parse_mode="HTML")
    
    # Clean up old callback data occasionally
    if len(callback_payloads) > 1000:
        cleanup_old_callbacks()

async def spotdl_get_streams(url: str) -> dict:
    return {'metadata': {},
            'streams': [
                {'label': '🎵 audio', 'tool': 'spotdl', 'url': url, 'audio_only': True }
            ]}

async def ytdlp_get_streams(url: str) -> dict:
    cmd = ['yt-dlp', '--dump-single-json', url]
    if os.path.exists('cookies.txt'):
        cmd.extend(['--cookies','cookies.txt'])
    run = subprocess.run(cmd, capture_output=True, text=True)
    data = run.stdout.strip()
    #print(data)

    metadata = json.loads(data)
    if metadata is None or metadata.get('duration') is None or metadata.get('formats') is None:
        return {'metadata': {}, 'streams': []}

    language_preferences = [f['language_preference'] for f in metadata['formats'] if 'language_preference' in f]
    max_language_preference = max(language_preferences) if language_preferences else -1
    
    video_allowed = ['mp4', 'h264']
    audio_allowed = ['mp4', 'mp3', 'aac', 'm4a']
    missing_data  = [None, 'none']
    max_size_bytes = 48*1024*1024
    
    formats = {'both': {}, 'video': {}, 'audio': {}}
    for f in metadata['formats']:

        if f.get('filesize') not in missing_data:
            size = f['filesize']
        elif f.get('tbr') not in missing_data:
            size = f['tbr'] * metadata['duration'] * 1024 / 8
        else:
            continue

        video_ok = f.get('vcodec') in video_allowed or f.get('video_ext') in video_allowed
        audio_ok = (f.get('acodec') in audio_allowed or f.get('audio_ext') in audio_allowed) and f.get('language_preference', -1) == max_language_preference

        print(f"format_id {f['format_id']}, size {size}, video_ok {video_ok}, audio_ok {audio_ok}")

        if size > max_size_bytes:
            continue
        elif video_ok and audio_ok:
            formats['both'][size] = [ f['format_id'] ]
        elif video_ok and f['audio_ext'] in [None, 'none']:
            formats['video'][size] = [ f['format_id'] ]
        elif audio_ok and f['video_ext'] in [None, 'none']:
            formats['audio'][size] = [ f['format_id'] ]

    for (size_a, audio), (size_v, video) in product(formats['audio'].items(), formats['video'].items()):
        size = size_a + size_v
        if size <= max_size_bytes:
            formats['both'][size] = audio + video

    #print('[formats]')
    #pprint(formats)

    best_formats = {k: v[max(v.keys())] if v else None for k, v in formats.items()}

    #print('[best_formats]')
    #pprint(best_formats)

    streams = []
    if best_formats['both']:
        streams.append({'label': '🎬 video', 'tool': 'yt-dlp', 'url': url, 'streams': best_formats['both'], 'audio_only': False })
    if best_formats['video']:
        streams.append({'label': '🔇 video (no audio)', 'tool': 'yt-dlp', 'url': url, 'streams': best_formats['video'], 'audio_only': False })
    if best_formats['audio']:
        streams.append({'label': '🎵 audio', 'tool': 'yt-dlp', 'url': url, 'streams': best_formats['audio'], 'audio_only': True })

    return {'metadata': metadata, 'streams': streams}

###

async def download_stream(message, stream):

    user_id = message.chat_id
    print(f'userID {user_id} download_stream: {stream}')

    # Check quota - whitelist bypasses quota
    if not is_whitelisted(user_id) and not check_and_update_quota(user_id):
        print(f'quota exceeded')
        await message.reply_text(f"Quota exceeded: {DAILY_LIMIT} downloads per 24 hours. (User ID: {user_id})")
        return

    msg = await message.reply_text(f'Downloading {stream["label"]}...')
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            if stream['tool'] == 'spotdl':
                await download_stream_spotdl(message, stream, tmpdir)
            else:
                await download_stream_ytdlp(message, stream, tmpdir)

            files = os.listdir(tmpdir)
            if not files:
                await msg.edit_text("Download failed 😞")
                return
            await msg.edit_text(f'Sending {stream["label"]}...')
            await send_download(message, os.path.join(tmpdir, files[0]), stream['audio_only'])

            await msg.delete()

        except Exception as e:
            print('An internal error occurred: ' + str(e))
            await msg.edit_text('An internal error occurred: ' + str(e))

async def download_stream_spotdl(message, stream: dict, dir: str):
    process = await asyncio.create_subprocess_shell(f"spotdl download {shlex.quote(stream['url'])}", cwd=dir)
    await process.wait()

async def download_stream_ytdlp(message, stream: dict, dir: str):
    
    cmd = ['yt-dlp', '--newline', '--progress-delta', '1', '-f', '+'.join(stream['streams']), '-o', os.path.join(dir, '%(title)s.%(ext)s'), stream['url']]

    if os.path.exists('cookies.txt'):
        cmd.extend(['--cookies', 'cookies.txt'])

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )

    msg = await message.reply_text("`Starting yt-dlp...`", parse_mode="Markdown")
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        try:
            await msg.edit_text(f"`{clean_text(line.decode(),60)}`", parse_mode="Markdown")
        except error.BadRequest:
            pass

    await process.wait()
    await msg.delete()

async def send_download(message, filepath: str, audio_only: bool):
    try:
        if audio_only:
            await message.reply_audio(audio=open(filepath, 'rb'))
        else:
            width, height = get_resolution_ffprobe(filepath)  # width, height, duration and thumbnail are optional for reply_video(), but without them the Telegram client often messes up the preview and the playback
            duration = get_duration_ffprobe(filepath)
            screenshot = get_screenshot_ffmpeg(filepath, duration*0.1)
            await message.reply_video(video=open(filepath, 'rb'), width=width, height=height, duration=duration, thumbnail=screenshot)
    except Exception as e1:
        print(e1)
        try:
            await message.reply_document(document=open(filepath, 'rb'))
        except Exception as e2:
            await message.reply_text(f"Failed to send: {e1}, {e2}")

####

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Hi! I can download video and audio from YouTube, Facebook, Instagram, TikTok, Spotify, SoundCloud, …. For this I use the open source projects *yt-dlp* and *spotdl*.\n\nI will only download video/audio that can be viewed inside Telegram: mp4 format and <50MB.\n\nThere's a limit of {DAILY_LIMIT} downloads per 24h. If you want more, you'll need to run your own bot: https://github.com/cwverhey/yt-dlp_telegrambot/\n\nSend a URL to start downloading.", parse_mode="Markdown")

async def whitelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        await update.message.reply_text(f"You don't have permission to use this command. (User ID: {user_id})")
        return
    
    if not context.args:
        await update.message.reply_text("Usage:\n/whitelist add <userID> - Add user to whitelist\n/whitelist remove <userID> - Remove user from whitelist")
        
        whitelist = load_whitelist()
        if whitelist:
            await update.message.reply_text(f"Current whitelist: {', '.join(map(str, whitelist))}")
        else:
            await update.message.reply_text("Whitelist is empty.")
        return
        
    action = context.args[0].lower()
    
    if action not in ['add', 'remove'] or len(context.args) != 2:
        await update.message.reply_text("Usage:\n/whitelist add <userID> - Add user to whitelist\n/whitelist remove <userID> - Remove user from whitelist")
        return
        
    try:
        target_id = int(context.args[1])
        whitelist = load_whitelist()
        
        if action == 'add':
            if target_id in whitelist:
                await update.message.reply_text(f"User {target_id} is already whitelisted.")
            else:
                whitelist.append(target_id)
                save_whitelist(whitelist)
                await update.message.reply_text(f"User {target_id} added to whitelist.")
        else:  # remove
            if target_id not in whitelist:
                await update.message.reply_text(f"User {target_id} is not in the whitelist.")
            else:
                whitelist.remove(target_id)
                save_whitelist(whitelist)
                await update.message.reply_text(f"User {target_id} removed from whitelist.")
    except ValueError:
        await update.message.reply_text("User ID must be a number.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    match = re.search(r'https?://[^\s)]+', text)
    if match:
        await get_streams(update.message, match.group(0))
    else:
        await update.message.reply_text("No URL found. Send a URL to start downloading.")

async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data in callback_payloads:
        payload, _ = callback_payloads[query.data]
        if query.data.startswith('get:'):
            await get_streams(query.message, payload)
        if query.data.startswith('download:'):
            await download_stream(query.message, payload)
    else:
        await query.edit_message_text("This request has expired (48 hours).")

def main():

    os.makedirs('data', exist_ok=True)

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("whitelist", whitelist_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(handle_button))
    
    print("Bot started.")
    app.run_polling()

if __name__ == "__main__":
    main()
