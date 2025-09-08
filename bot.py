import os
import json
import time
import math
import asyncio
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))  # Channel/Group ID for auto-upload
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === Progress Bar Formatter ===
def format_progress(name, task, percent, eta, past):
    bar_len = 20
    filled_len = int(math.ceil(bar_len * percent / 100))
    bar = '█' * filled_len + '▒' * (bar_len - filled_len)
    return f"""Name » {name}
⌑ Task   » {task}
⌑ {bar} » {percent:.2f}%
⌑ ETA    : {eta}
⌑ Past   : {past}"""

# === File Download ===
async def download_file(url, file_path, message: Message):
    start_time = time.time()
    total = 0
    chunk_size = 1024*1024  # 1MB

    r = requests.get(url, stream=True)
    total_length = int(r.headers.get('content-length', 0))
    msg = await message.reply(f"⌑ Task   » Downloading\n⌑ 0%")

    with open(file_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                total += len(chunk)
                percent = total / total_length * 100 if total_length else 0
                past = int(time.time() - start_time)
                eta = int((past / total) * (total_length - total)/1024/1024) if total > 0 else 0
                await message._client.edit_message_text(message.chat.id, msg.id,
                    format_progress(os.path.basename(file_path), "Downloading", percent, f"{eta}s", f"{past}s")
                )
    await message._client.edit_message_text(message.chat.id, msg.id,
        format_progress(os.path.basename(file_path), "Downloading", 100, "0s", f"{int(time.time() - start_time)}s")
    )
    return file_path

# === Encoding Function ===
async def encode_video(input_path, output_path, message: Message):
    start_time = time.time()
    msg = await message.reply(f"⌑ Task   » Encoding\n⌑ 0%")
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    while True:
        line = process.stdout.readline()
        if not line and process.poll() is not None:
            break
        if "time=" in line:
            # crude progress estimation (not perfect)
            elapsed = time.time() - start_time
            percent = min(100, elapsed / 60 * 100)  # example, adjust if you want precise
            await message._client.edit_message_text(message.chat.id, msg.id,
                format_progress(os.path.basename(input_path), "Encoding", percent, "?", f"{int(elapsed)}s")
            )
    await message._client.edit_message_text(message.chat.id, msg.id,
        format_progress(os.path.basename(input_path), "Encoding", 100, "0s", f"{int(time.time() - start_time)}s")
    )
    return output_path

# === Upload Function ===
async def upload_file(file_path, chat_id, message: Message):
    start_time = time.time()
    total_size = os.path.getsize(file_path)
    msg = await message.reply(f"⌑ Task   » Uploading\n⌑ 0%")
    sent = 0

    async for m in message._client.send_document(chat_id, file_path, progress=lambda d, t: None, progress_args=()):
        # fake progress since Pyrogram doesn't give streaming progress
        elapsed = time.time() - start_time
        percent = min(100, (sent/total_size)*100)
        await message._client.edit_message_text(message.chat.id, msg.id,
            format_progress(os.path.basename(file_path), "Uploading", percent, "?", f"{int(elapsed)}s")
        )
    await message._client.edit_message_text(message.chat.id, msg.id,
        format_progress(os.path.basename(file_path), "Uploading", 100, "0s", f"{int(time.time() - start_time)}s")
    )

# === Pyrogram Client ===
app = Client(name="anime_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)
pending_videos = {}

@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    # Download file with progress
    await download_file(message.document.file_id if message.document else message.video.file_id, file_path, message)
    # Auto encode after download
    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
    await encode_video(file_path, output_file, message)
    await upload_file(output_file, message.chat.id, message)
    os.remove(file_path)
    os.remove(output_file)

# === Run Bot ===
if __name__ == "__main__":
    print("Bot is running...")
    app.run()
