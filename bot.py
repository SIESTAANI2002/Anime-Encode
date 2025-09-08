import os
import json
import time
import threading
import subprocess
import requests
import math
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = os.getenv("CHAT_ID")
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# === Utils ===
def humanbytes(size):
    # Converts bytes into readable format
    if size == 0:
        return "0B"
    power = 2**10
    n = 0
    Dic_powerN = {0: "B", 1: "KB", 2: "MB", 3: "GB", 4: "TB"}
    while size > power:
        size /= power
        n += 1
    return f"{round(size,2)} {Dic_powerN[n]}"

def progress_bar(done, total):
    percent = int((done / total) * 100)
    bar = "█" * (percent // 10) + "▒" * (10 - (percent // 10))
    return percent, bar

# === Progress Callback ===
async def progress_message(msg, task, filename, current, total, start_time):
    now = time.time()
    elapsed = now - start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0

    percent, bar = progress_bar(current, total)
    text = (
        f"📂 Filename: {os.path.basename(filename)}\n"
        f"{task}: {percent}% [{bar}]\n"
        f"✅ Done: {humanbytes(current)} / {humanbytes(total)}\n"
        f"⚡ Speed: {humanbytes(speed)}/s\n"
        f"⏳ ETA: {int(eta)}s | ⌛ Elapsed: {int(elapsed)}s"
    )
    try:
        await msg.edit_text(text)
    except:
        pass

# === Download with progress ===
async def download_file(client, message, file_id, filename):
    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
    status = await message.reply("⬇️ Starting download...")

    start = time.time()

    async def progress(current, total):
        await progress_message(status, "⬇️ Downloading", filename, current, total, start)

    await client.download_media(file_id, file_name=file_path, progress=progress)
    return file_path, status

# === Encode Function ===
def encode_video(input_path, output_path, update_cb=None):
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    total_time = None
    start = time.time()

    for line in process.stdout:
        if "Duration" in line:
            duration = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = duration.split(":")
            total_time = int(float(h)) * 3600 + int(m) * 60 + float(s)
        if "time=" in line and total_time:
            t_str = line.split("time=")[1].split(" ")[0]
            h, m, s = t_str.split(":")
            cur_time = int(float(h)) * 3600 + int(m) * 60 + float(s)
            percent = (cur_time / total_time) * 100
            if update_cb:
                update_cb(cur_time, total_time, start)

    process.wait()
    return output_path

# === Upload with progress ===
async def upload_file(client, chat_id, file_path, reply_to, msg):
    start = time.time()

    async def progress(current, total):
        await progress_message(msg, "📤 Uploading", file_path, current, total, start)

    await client.send_document(chat_id, file_path, progress=progress, reply_to_message_id=reply_to)

# === Pyrogram Client ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    filename = message.document.file_name if message.document else message.video.file_name
    file_id = message.document.file_id if message.document else message.video.file_id

    # 1. Download
    file_path, status = await download_file(client, message, file_id, filename)

    # 2. Encode automatically
    output_path = os.path.join(ENCODED_FOLDER, filename)
    async def enc_update(cur, total, start):
        await progress_message(status, "⚙️ Encoding", filename, cur, total, start)

    encode_video(file_path, output_path, update_cb=enc_update)

    # 3. Upload
    await upload_file(client, message.chat.id, output_path, message.id, status)

    await status.edit_text(f"✅ Completed {filename}")
    os.remove(file_path)
    os.remove(output_path)

if __name__ == "__main__":
    print("Bot is running...")
    app.run()
