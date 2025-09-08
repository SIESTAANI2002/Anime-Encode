import os
import json
import time
import asyncio
import aiohttp
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import feedparser

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))

DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBSPLEASE_FEED = "https://subsplease.org/rss/?r=1080"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# Track downloaded episodes
if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === PROGRESS BAR ===
def get_progress_bar(current, total, length=20):
    filled = int(length * current / total)
    bar = "█" * filled + "▒" * (length - filled)
    percent = current / total * 100
    return f"{bar} » {percent:.2f}%"

# === PYROGRAM CLIENT ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
loop = asyncio.get_event_loop()
pending_videos = {}

# === DOWNLOAD FILE ===
async def download_file(url, filename, msg: Message):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024
            path = os.path.join(DOWNLOAD_FOLDER, filename)
            start_time = time.time()
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(chunk_size):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed / 1024 / 1024
                    eta = (total - downloaded) / (downloaded / elapsed) if downloaded else 0
                    bar = get_progress_bar(downloaded, total)
                    text = (f"Filename : {filename}\n"
                            f"Downloading: {bar}\n"
                            f"Done   : {downloaded/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
                            f"Speed  : {speed:.2f}MB/s\n"
                            f"ETA    : {eta:.0f}s\n"
                            f"Elapsed: {elapsed:.0f}s")
                    # Update inline safely
                    asyncio.run_coroutine_threadsafe(msg.edit(text), loop)
            return path

# === ENCODE VIDEO ===
def encode_video(input_path, output_path, msg: Message):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if "time=" in line:
            text = f"Encoding: {os.path.basename(input_path)}\n{line.strip()}"
            asyncio.run_coroutine_threadsafe(msg.edit(text), loop)
    process.wait()
    return output_path

# === VIDEO HANDLER ===
@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    msg = await message.reply(f"⬇️ Downloading {file_name}...")
    if message.document:
        file_id = message.document.file_id
    else:
        file_id = message.video.file_id
    path = await client.download_media(file_id, file_name=os.path.join(DOWNLOAD_FOLDER, file_name))
    pending_videos[message.id] = path

    # Auto encode after download
    out_file = os.path.join(ENCODED_FOLDER, os.path.basename(path))
    await msg.edit(f"⚙️ Encoding {file_name}...")
    encode_video(path, out_file, msg)
    await msg.edit(f"✅ Finished {file_name}, uploading...")
    await client.send_document(message.chat.id, out_file)
    os.remove(path)
    os.remove(out_file)
    pending_videos.pop(message.id, None)

# === MANUAL /ENCODE COMMAND ===
@app.on_message(filters.command("encode"))
async def encode_command(client, message: Message):
    if message.reply_to_message:
        orig_msg_id = message.reply_to_message.id
        if orig_msg_id not in pending_videos:
            await message.reply("⚠️ File not found, please upload it again.")
            return
        input_path = pending_videos[orig_msg_id]
        output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
        msg = await message.reply(f"⚙️ Encoding {os.path.basename(input_path)}...")
        encode_video(input_path, output_path, msg)
        await message.reply(f"✅ Done {os.path.basename(input_path)}")
        await client.send_document(message.chat.id, output_path)
        os.remove(input_path)
        os.remove(output_path)
        pending_videos.pop(orig_msg_id, None)
    else:
        await message.reply("Reply to a video/document with /encode to process it.")

# === SUBSPLEASE AUTO-DOWNLOAD ===
async def fetch_subsplease():
    try:
        feed = feedparser.parse(SUBSPLEASE_FEED)
        if not feed.entries:
            print("⚠️ SubsPlease feed empty")
            return
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            if link in downloaded_episodes:
                continue
            print(f"⬇️ Auto download: {title} -> {link}")
            filename = f"{title}.mkv"
            msg = await app.send_message(CHAT_ID, f"⬇️ {filename}")
            path = await download_file(link, filename, msg)
            out_file = os.path.join(ENCODED_FOLDER, filename)
            encode_video(path, out_file, msg)
            await app.send_document(CHAT_ID, out_file)
            os.remove(path)
            os.remove(out_file)
            downloaded_episodes.add(link)
            save_tracked()
    except Exception as e:
        print("SubsPlease auto error:", e)

# === RUN BOT ===
if __name__ == "__main__":
    scheduler = AsyncIOScheduler()
    scheduler.add_job(fetch_subsplease, "interval", minutes=10)
    scheduler.start()
    print("🚀 Bot is running...")
    app.run()
