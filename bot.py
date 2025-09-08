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
    filled = int(length * current / total) if total else 0
    bar = "█" * filled + "▒" * (length - filled)
    percent = (current / total * 100) if total else 0
    return f"{bar} » {percent:.2f}%"

def format_progress_text(filename, task, current, total, speed, elapsed, eta):
    return (
        f"Filename : {filename}\n"
        f"{task}: {get_progress_bar(current, total)}\n"
        f"Done   : {current/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
        f"Speed  : {speed:.2f}MB/s\n"
        f"ETA    : {eta:.0f}s\n"
        f"Elapsed: {elapsed:.0f}s"
    )

# === DOWNLOAD FUNCTION ===
async def download_file(url, filename, msg: Message):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024*1024  # 1 MB
            path = os.path.join(DOWNLOAD_FOLDER, filename)
            start_time = time.time()
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(chunk_size):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed / 1024 / 1024
                    eta = (total - downloaded) / (speed * 1024 * 1024) if downloaded else 0
                    text = format_progress_text(filename, "Downloading", downloaded, total, speed, elapsed, eta)
                    try:
                        await msg.edit(text)
                    except:
                        pass
            return path

# === ENCODE FUNCTION ===
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
            time_str = line[line.find("time=")+5:line.find(" bitrate")]
            try:
                h, m, s = 0, 0, 0
                parts = time_str.split(":")
                if len(parts) == 3:
                    h, m, s = map(float, parts)
                elif len(parts) == 2:
                    m, s = map(float, parts)
                elapsed_sec = h*3600 + m*60 + s
                asyncio.run_coroutine_threadsafe(msg.edit(f"Encoding: {elapsed_sec:.0f}s processed\nFile: {os.path.basename(input_path)}"), asyncio.get_event_loop())
            except:
                pass
    return output_path

# === PYROGRAM CLIENT ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    msg = await message.reply(f"⬇️ Downloading {file_name}...")
    path = await download_file(message.document.file_id if message.document else message.video.file_id, file_name, msg)
    pending_videos[message.id] = path

    # Auto encode after download
    out_file = os.path.join(ENCODED_FOLDER, os.path.basename(path))
    await msg.edit(f"⚙️ Encoding {file_name}...")
    encode_video(path, out_file, msg)
    await msg.edit(f"📤 Uploading {file_name}...")
    await client.send_document(message.chat.id, out_file)

    os.remove(path)
    os.remove(out_file)
    pending_videos.pop(message.id, None)
    await msg.edit(f"✅ Finished {file_name}")

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
            if link in downloaded_episodes or not link.startswith("http"):
                continue
            filename = f"{title}.mkv"
            msg = await app.send_message(CHAT_ID, f"⬇️ Downloading {filename}")
            path = await download_file(link, filename, msg)
            out_file = os.path.join(ENCODED_FOLDER, filename)
            await msg.edit(f"⚙️ Encoding {filename}...")
            encode_video(path, out_file, msg)
            await msg.edit(f"📤 Uploading {filename}...")
            await app.send_document(CHAT_ID, out_file)
            os.remove(path)
            os.remove(out_file)
            downloaded_episodes.add(link)
            save_tracked()
            await msg.edit(f"✅ Done {filename}")
    except Exception as e:
        print("SubsPlease auto error:", e)

# === SCHEDULER ===
async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(fetch_subsplease, "interval", minutes=10)
    scheduler.start()
    await app.start()
    print("🚀 Bot is running...")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
