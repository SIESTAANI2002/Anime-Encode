import os
import json
import time
import asyncio
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
SUBSPLEASE_FEED = "https://subsplease.org/rss/?r=1080"  # latest releases RSS

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


# === SIMPLE PROGRESS BAR ===
def get_progress_bar(current, total, length=20):
    filled = int(length * current / total) if total else 0
    bar = "█" * filled + "▒" * (length - filled)
    percent = current / total * 100 if total else 0
    return f"{bar} » {percent:.2f}%"


# === PYROGRAM CLIENT ===
app = Client(
    name="anime_userbot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

pending_task = None  # Only one task at a time


# === DOWNLOAD WITH PROGRESS ===
async def download_file(message: Message, filename: str):
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    msg = await message.reply(f"⬇️ Downloading {filename}...")
    start = time.time()

    def progress(current, total):
        elapsed = time.time() - start
        speed = current / 1024 / 1024 / elapsed if elapsed else 0
        eta = (total - current) / (current / elapsed) if current else 0
        bar = get_progress_bar(current, total)
        text = (f"Filename : {filename}\n"
                f"Downloading: {bar}\n"
                f"Done   : {current / 1024 / 1024:.2f}MB of {total / 1024 / 1024:.2f}MB\n"
                f"Speed  : {speed:.2f}MB/s\n"
                f"ETA    : {eta:.0f}s\n"
                f"Elapsed: {elapsed:.0f}s")
        asyncio.create_task(msg.edit(text))

    path = await app.download_media(
        message,
        file_path=file_path,
        progress=progress,
        progress_args=None
    )
    return path, msg


# === ENCODE VIDEO ===
async def encode_video(input_path: str, msg: Message):
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    start = time.time()
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )

    async for line in process.stdout:
        try:
            line = line.decode()
        except:
            continue
        if "time=" in line:
            # just approximate progress
            text = f"⌑ Encoding » {os.path.basename(input_path)}\n{line.strip()}"
            asyncio.create_task(msg.edit(text))

    await process.wait()
    return output_path


# === VIDEO HANDLER ===
@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    global pending_task
    if pending_task:
        await message.reply("⚠️ Another task is running, please wait...")
        return

    pending_task = message
    file_name = message.document.file_name if message.document else message.video.file_name
    path, msg = await download_file(message, file_name)
    await msg.edit(f"⚙️ Download complete. Starting encode automatically...")
    out_file = await encode_video(path, msg)
    await msg.edit(f"⬆️ Uploading {file_name}...")
    await client.send_document(message.chat.id, out_file)
    os.remove(path)
    os.remove(out_file)
    pending_task = None


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
            filename = f"{title}.mkv"
            msg = await app.send_message(CHAT_ID, f"⬇️ Auto downloading {filename}...")
            path = await download_file(msg, filename)
            out_file = await encode_video(path, msg)
            await app.send_document(CHAT_ID, out_file)
            os.remove(path)
            os.remove(out_file)
            downloaded_episodes.add(link)
            save_tracked()
    except Exception as e:
        print("SubsPlease auto error:", e)


# === MAIN ===
if __name__ == "__main__":
    async def main():
        scheduler = AsyncIOScheduler()
        scheduler.add_job(fetch_subsplease, "interval", minutes=10)
        scheduler.start()
        await app.start()
        print("🚀 Bot is running...")
        await asyncio.Event().wait()  # keep running

    asyncio.run(main())
