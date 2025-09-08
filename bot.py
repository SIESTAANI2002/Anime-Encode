import os
import json
import time
import threading
import asyncio
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))  # channel/group for auto upload
USER_ID = int(os.getenv("USER_ID"))  # your Telegram ID to control the bot

DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# Load tracked episodes
if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === TASK MANAGEMENT ===
task_queue = []
current_task = None
cancel_flag = False

# === SAFE EDIT FUNCTION ===
async def safe_edit(message: Message, text: str):
    try:
        await message.edit(text)
    except:
        pass  # ignore flood

# === PROGRESS BAR HELPER ===
def get_progress_bar(current, total, length=20):
    percent = current / total if total else 0
    filled_len = int(length * percent)
    bar = "█" * filled_len + "▒" * (length - filled_len)
    return bar, percent * 100

# === DOWNLOAD ===
async def download_file(url, file_path, progress_msg: Message):
    global cancel_flag
    r = requests.get(url, stream=True)
    total = int(r.headers.get("content-length", 0))
    downloaded = 0
    chunk_size = 1024 * 1024  # 1 MB

    start_time = time.time()
    last_update = start_time
    with open(file_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if cancel_flag:
                return None
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if now - last_update > 20:
                    bar, pct = get_progress_bar(downloaded, total)
                    elapsed = int(now - start_time)
                    speed = downloaded / elapsed / (1024*1024)
                    eta = int((total - downloaded) / (speed * 1024*1024)) if speed > 0 else 0
                    msg_text = (
                        f"Name » {os.path.basename(file_path)}\n"
                        f"⌑ Task   » Downloading\n"
                        f"⌑ {bar} » {pct:.2f}%\n"
                        f"⌑ Done   : {downloaded/(1024*1024):.2f}MB of {total/(1024*1024):.2f}MB\n"
                        f"⌑ Speed  : {speed:.2f}MB/s\n"
                        f"⌑ ETA    : {eta}s\n"
                        f"⌑ Past   : {elapsed}s\n"
                        f"⌑ ENG    : PyroF v2.2.11\n"
                        f"⌑ TaskQ  : {len(task_queue)} pending\n"
                    )
                    await safe_edit(progress_msg, msg_text)
                    last_update = now
    return file_path

# === ENCODING ===
def encode_video(input_path, output_path, progress_callback=None):
    ext = os.path.splitext(input_path)[1]
    output_path = os.path.splitext(output_path)[0] + ext

    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y", output_path
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if progress_callback:
            progress_callback(line.strip())
    process.wait()
    return output_path

# === UPLOAD ===
async def upload_file(client: Client, chat_id, file_path, progress_msg: Message):
    global cancel_flag
    file_size = os.path.getsize(file_path)
    start_time = time.time()
    last_update = start_time
    sent_bytes = 0

    async def progress(current, total):
        nonlocal last_update
        now = time.time()
        if now - last_update > 20:
            bar, pct = get_progress_bar(current, total)
            elapsed = int(now - start_time)
            speed = current / elapsed / (1024*1024)
            eta = int((total - current) / (speed * 1024*1024)) if speed > 0 else 0
            msg_text = (
                f"Name » {os.path.basename(file_path)}\n"
                f"⌑ Task   » Uploading\n"
                f"⌑ {bar} » {pct:.2f}%\n"
                f"⌑ Done   : {current/(1024*1024):.2f}MB of {total/(1024*1024):.2f}MB\n"
                f"⌑ Speed  : {speed:.2f}MB/s\n"
                f"⌑ ETA    : {eta}s\n"
                f"⌑ Past   : {elapsed}s\n"
                f"⌑ ENG    : PyroF v2.2.11\n"
                f"⌑ TaskQ  : {len(task_queue)} pending\n"
            )
            await safe_edit(progress_msg, msg_text)
            last_update = now

    await client.send_document(chat_id, file_path, progress=progress, progress_args=(file_size,))
    return True

# === TASK HANDLER ===
async def process_task(client: Client, url, file_name):
    global current_task, cancel_flag
    cancel_flag = False
    current_task = file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    progress_msg = await client.send_message(USER_ID, f"Starting task » {file_name}")
    # Download
    downloaded_file = await download_file(url, file_path, progress_msg)
    if cancel_flag or downloaded_file is None:
        await safe_edit(progress_msg, f"⚠️ Task {file_name} canceled.")
        current_task = None
        return
    # Encode
    await safe_edit(progress_msg, f"⌑ Task » Encoding\n⌑ File » {file_name}")
    output_file = os.path.join(ENCODED_FOLDER, file_name)
    encode_video(file_path, output_file)
    # Upload
    await upload_file(client, CHAT_ID, output_file, progress_msg)
    # Cleanup
    os.remove(file_path)
    os.remove(output_file)
    await safe_edit(progress_msg, f"✅ Task {file_name} completed.")
    current_task = None

# === AUTO MODE ===
async def auto_mode(client: Client):
    while True:
        try:
            res = requests.get(SUBS_API_URL, timeout=15)
            try:
                recent = res.json()
            except:
                print("SubsPlease returned non-JSON, retrying in 60s")
                await asyncio.sleep(60)
                continue
            for ep in recent.get("data", []):
                title = ep["release_title"]
                link = ep["link"]
                if url not in downloaded_episodes:
                    task_queue.append((link, title))
                    downloaded_episodes.add(link)
                    save_tracked()
        except Exception as e:
            print("Auto mode error:", e)
        await asyncio.sleep(600)  # 10 min interval

# === BOT SETUP ===
app = Client("anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

@app.on_message(filters.command("cancel") & filters.user(USER_ID))
async def cancel_task(client, message):
    global cancel_flag
    cancel_flag = True
    await message.reply("⚠️ Current task canceled.")

@app.on_message(filters.command("encode") & filters.user(USER_ID))
async def manual_encode(client, message):
    if message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document
        url = await doc.download(file_name=os.path.join(DOWNLOAD_FOLDER, doc.file_name))
        task_queue.append((url, doc.file_name))
        await message.reply(f"✅ Task {doc.file_name} added to queue.")

async def task_worker():
    while True:
        if not current_task and task_queue:
            url, file_name = task_queue.pop(0)
            await
