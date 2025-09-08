import os
import json
import time
import asyncio
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
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

# === VIDEO ENCODE ===
def encode_video(input_path, output_path, msg: Message = None):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    for line in process.stdout:
        if "time=" in line and msg:
            time_str = line[line.find("time=")+5:line.find(" bitrate")]
            text = f"⚙️ Encoding... {time_str}\nFile: {os.path.basename(input_path)}"
            try:
                asyncio.run_coroutine_threadsafe(msg.edit(text), asyncio.get_event_loop())
            except:
                pass
    process.wait()
    return output_path

# === PYROGRAM CLIENT ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

# === HANDLE TELEGRAM FILES ===
@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    msg = await message.reply(f"⬇️ Downloading {file_name}...")
    
    # Download using Pyrogram
    path = await client.download_media(message, file_name=os.path.join(DOWNLOAD_FOLDER, file_name))
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
            path = await app.download_media(link, file_name=os.path.join(DOWNLOAD_FOLDER, filename))
            out_file = os.path.join(ENCODED_FOLDER, filename)
            encode_video(path, out_file)
            await app.send_document(CHAT_ID, out_file)
            os.remove(path)
            os.remove(out_file)
            downloaded_episodes.add(link)
            save_tracked()
    except Exception as e:
        print("SubsPlease auto error:", e)

# === RUN BOT ===
async def main():
    # Run SubsPlease auto-download every 10 minutes
    async def scheduler():
        while True:
            await fetch_subsplease()
            await asyncio.sleep(600)

    asyncio.create_task(scheduler())
    await app.start()
    print("🚀 Bot is running...")
    await asyncio.Event().wait()  # keep running

if __name__ == "__main__":
    asyncio.run(main())
