import os
import json
import time
import asyncio
import aiohttp
import subprocess
import shutil
from pyrogram import Client, filters
from pyrogram.types import Message

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

pending_videos = {}
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
app.start_time = time.time()  # For uptime calculation

# === HELPER FUNCTIONS ===
def get_progress_bar(current, total, length=20):
    filled = int(length * current / total) if total else 0
    bar = "█" * filled + "▒" * (length - filled)
    percent = (current / total * 100) if total else 0
    return bar, percent

def format_progress(filename, task, current, total, user="Ānī"):
    bar, percent = get_progress_bar(current, total)
    elapsed = int(time.time() - start_time)
    speed = current / elapsed / (1024*1024) if elapsed else 0
    eta = int((total - current) / (current/elapsed)) if current else 0
    total_space, used, free = shutil.disk_usage("/")
    free_mb = free // (1024*1024)
    uptime_sec = int(time.time() - app.start_time)
    h, m, s = uptime_sec//3600, (uptime_sec%3600)//60, uptime_sec%60
    uptime = f"{h}h{m}m{s}s"
    text = (f"Filename : {filename}\n"
            f"⌑ Task   » {task}\n"
            f"⌑ {bar} » {percent:.2f}%\n"
            f"⌑ Done   : {current/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
            f"⌑ Speed  : {speed:.2f}MB/s\n"
            f"⌑ ETA    : {eta}s\n"
            f"⌑ Elapsed: {elapsed}s\n"
            f"⌑ User   : {user}\n"
            f"\nFREE: {free_mb}MB | UPTIME: {uptime}")
    return text

# === DOWNLOAD FILE ===
async def download_file(url, filename, msg: Message):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024*1024
            path = os.path.join(DOWNLOAD_FOLDER, filename)
            global start_time
            start_time = time.time()
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(chunk_size):
                    f.write(chunk)
                    downloaded += len(chunk)
                    text = format_progress(filename, "Downloading", downloaded, total)
                    try: await msg.edit(text)
                    except: pass
            return path

# === ENCODE VIDEO ===
def encode_video(input_path, output_path, msg: Message):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    command = ["ffmpeg", "-i", input_path, "-vf", "scale=-1:720", "-c:v", "libx264",
               "-preset", "fast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", "-y", output_path]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if "time=" in line:
            try:
                time_str = line.split("time=")[1].split(" ")[0]
                h, m, s = 0, 0, 0
                parts = time_str.split(":")
                if len(parts)==3: h,m,s = map(float, parts)
                elif len(parts)==2: m,s = map(float, parts)
                elapsed_sec = h*3600 + m*60 + s
                text = format_progress(os.path.basename(input_path), "Encoding", elapsed_sec*1024*1024, 100*1024*1024)
                try: asyncio.run(msg.edit(text))
                except: pass
            except: pass
    return output_path

# === VIDEO HANDLER ===
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
    await msg.edit(f"✅ Finished {file_name}, uploading...")
    await client.send_document(message.chat.id, out_file)
    os.remove(path)
    os.remove(out_file)
    pending_videos.pop(message.id, None)

# === MANUAL ENCODE CMD ===
@app.on_message(filters.command("encode"))
async def encode_command(client, message: Message):
    if not message.reply_to_message:
        await message.reply("Reply to a video/document with /encode")
        return
    orig_id = message.reply_to_message.id
    if orig_id not in pending_videos:
        await message.reply("⚠️ File not found, upload it again.")
        return
    input_path = pending_videos[orig_id]
    out_file = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
    await message.reply(f"⚙️ Encoding {os.path.basename(input_path)}...")
    encode_video(input_path, out_file, message)
    await message.reply(f"✅ Done {os.path.basename(input_path)}")
    await client.send_document(message.chat.id, out_file)
    os.remove(input_path)
    os.remove(out_file)
    pending_videos.pop(orig_id, None)

# === RUN BOT ===
if __name__ == "__main__":
    print("🚀 Bot is running...")
    app.run()
