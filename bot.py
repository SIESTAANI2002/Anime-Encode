import os
import json
import time
import math
import asyncio
import threading
import subprocess
import requests
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
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

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

# === Progress bar ===
def progress_text(filename, task, done, total, speed, elapsed, eta):
    percent = done / total * 100 if total else 0
    bar_len = 20
    filled = int(bar_len * percent / 100)
    bar = "█" * filled + "▒" * (bar_len - filled)
    return (f"Filename : {filename}\n"
            f"Task     : {task}\n"
            f"{bar} » {percent:.2f}%\n"
            f"Done     : {done/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
            f"Speed    : {speed:.2f}MB/s\n"
            f"Elapsed  : {elapsed:.0f}s\n"
            f"ETA      : {eta:.0f}s")

# === Pyrogram Client ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

pending_videos = {}
queue = asyncio.Queue()

# === Download file ===
async def download_telegram_file(message: Message, path, edit_msg=None):
    start = time.time()
    total = message.document.file_size if message.document else message.video.file_size
    downloaded = 0

    def callback(current, total_size):
        nonlocal downloaded
        downloaded = current
        elapsed = time.time() - start
        speed = downloaded / elapsed / 1024 / 1024
        eta = (total - downloaded) / (downloaded / elapsed) if downloaded else 0
        if edit_msg:
            asyncio.create_task(edit_msg.edit(progress_text(os.path.basename(path), "Downloading", downloaded, total, speed, elapsed, eta)))

    await message.download(file_name=path, progress=callback)
    return path

# === Encode video ===
def encode_video(input_path, output_path, edit_msg=None):
    ext = os.path.splitext(input_path)[1]
    output_path = os.path.splitext(output_path)[0] + ext

    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]

    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start = time.time()
    for line in process.stdout:
        if "time=" in line and edit_msg:
            try:
                t_str = line.split("time=")[1].split(" ")[0]
                parts = list(map(float, t_str.split(":")))
                if len(parts) == 3:
                    h, m, s = parts
                elif len(parts) == 2:
                    h, m, s = 0, parts[0], parts[1]
                else:
                    h = m = s = 0
                elapsed_sec = h*3600 + m*60 + s
                asyncio.create_task(edit_msg.edit(f"Encoding: {elapsed_sec:.0f}s\nFile: {os.path.basename(input_path)}"))
            except:
                pass
    return output_path

# === Handle Telegram files ===
@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    path = os.path.join(DOWNLOAD_FOLDER, file_name)
    msg = await message.reply(f"⬇️ Downloading {file_name}...")
    await download_telegram_file(message, path, msg)
    # Queue the encode task
    await queue.put((path, message.chat.id, file_name, msg))

# === Encode command ===
@app.on_message(filters.command("encode"))
async def manual_encode(client, message: Message):
    if message.reply_to_message and message.reply_to_message.id in pending_videos:
        path = pending_videos[message.reply_to_message.id]
        msg = await message.reply(f"⚙️ Encoding {os.path.basename(path)}...")
        await queue.put((path, message.chat.id, os.path.basename(path), msg))
    else:
        await message.reply("Reply to a video/document with /encode to process it.")

# === Queue processor ===
async def worker():
    while True:
        path, chat_id, file_name, msg = await queue.get()
        pending_videos[msg.id] = path
        out_file = os.path.join(ENCODED_FOLDER, os.path.basename(path))
        await msg.edit(f"⚙️ Encoding {file_name}...")
        encode_video(path, out_file, msg)
        await msg.edit(f"✅ Uploading {file_name}...")
        await app.send_document(chat_id, out_file)
        os.remove(path)
        os.remove(out_file)
        pending_videos.pop(msg.id, None)
        queue.task_done()

# === Run Bot ===
if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(worker())
    print("🚀 Bot is running...")
    app.run()
