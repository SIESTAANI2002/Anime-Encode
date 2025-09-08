import os
import json
import time
import asyncio
import threading
import subprocess
import psutil
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Your Pyrogram session string
CHAT_ID = int(os.getenv("CHAT_ID"))           # Only your user ID
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

# === TASK QUEUE ===
task_queue = []
active_task = None
cancel_flag = False
skip_flag = False
start_time = time.time()

def human_readable_size(size, decimal_places=2):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.{decimal_places}f}{unit}"
        size /= 1024
    return f"{size:.{decimal_places}f}PB"

def uptime():
    seconds = int(time.time() - start_time)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return f"{d}d {h}h {m}m {s}s"

# === FANCY PROGRESS BAR ===
def create_progress_bar(progress, length=10):
    filled = int(length * progress)
    empty = length - filled
    bar = "█" * filled + "▒" * empty
    return bar

# === FFmpeg Encode Function ===
def encode_video(input_path, output_path, progress_callback=None):
    import json
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

    # Detect audio streams
    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index,codec_name",
        "-of", "json", input_path
    ]
    result = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    audio_info = json.loads(result.stdout).get("streams", [])

    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:s", "copy"
    ]

    for stream in audio_info:
        idx = stream["index"]
        codec = stream["codec_name"].lower()
        if codec == "aac":
            command += [f"-c:a:{idx}", "aac", f"-b:a:{idx}", "128k"]
        elif codec == "opus":
            command += [f"-c:a:{idx}", "libopus", f"-b:a:{idx}", "128k"]
        elif codec == "mp3":
            command += [f"-c:a:{idx}", "libmp3lame", f"-b:a:{idx}", "128k"]
        elif codec == "flac":
            command += [f"-c:a:{idx}", "flac"]
        else:
            command += [f"-c:a:{idx}", "aac", f"-b:a:{idx}", "128k"]

    command += ["-y", output_path]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if progress_callback and ("frame=" in line or "time=" in line):
            progress_callback(line.strip())
    process.wait()
    return output_path

# === SUBSPLASE DOWNLOAD ===
import requests
def get_recent_releases():
    releases = []
    try:
        res = requests.get(SUBS_API_URL, timeout=15).json()
        for ep in res.get("data", []):
            title = ep["release_title"]
            link = ep["link"]
            releases.append((title, link))
    except Exception as e:
        print("SubsPlease API error:", e)
    return releases

def download_file(url, output_path, progress_callback=None):
    r = requests.get(url, stream=True)
    total = int(r.headers.get('content-length', 0))
    downloaded = 0
    start = time.time()
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total > 0:
                    elapsed = time.time() - start
                    speed = downloaded / elapsed
                    progress_callback(downloaded, total, speed)
    return output_path

# === PYROGRAM CLIENT ===
app = Client(name="anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

# === TASK PROCESSOR ===
async def process_tasks():
    global active_task, cancel_flag, skip_flag
    while True:
        if not active_task and task_queue:
            active_task = task_queue.pop(0)
            try:
                message, input_path = active_task
                cancel_flag = False
                skip_flag = False

                # Download step
                await message.edit("⌑ Downloading...")
                def dl_progress(dl, total, speed):
                    percent = dl / total
                    bar = create_progress_bar(percent, 10)
                    text = f"Name » [{os.path.basename(input_path)}]\n⌑ Downloading » {human_readable_size(speed)}/s\n⌑ {bar} » {percent*100:.2f}%"
                    asyncio.create_task(message.edit(text))
                download_file(input_path, input_path, dl_progress)

                # Encode step
                await message.edit("⌑ Encoding...")
                def enc_progress(line):
                    asyncio.create_task(message.edit(f"⌑ Encoding » {line}"))
                output_file = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
                encode_video(input_path, output_file, enc_progress)

                # Upload step
                await message.edit("⌑ Uploading...")
                await app.send_document(CHAT_ID, output_file)

                # Cleanup
                os.remove(input_path)
                os.remove(output_file)
                await message.edit("✅ Task Completed")

            except Exception as e:
                await message.edit(f"❌ Error: {e}")

            active_task = None
        await asyncio.sleep(5)

# === MESSAGE HANDLERS ===
@app.on_message(filters.private & filters.user(CHAT_ID) & (filters.video | filters.document))
async def handle_upload(client, message: Message):
    file_path = os.path.join(DOWNLOAD_FOLDER, message.document.file_name if message.document else message.video.file_name)
    await message.download(file_path)
    task_queue.append((message, file_path))
    await message.reply(f"✅ Task added to queue. Position: {len(task_queue)}")

@app.on_message(filters.command("cancel") & filters.private & filters.user(CHAT_ID))
async def cancel_task(client, message: Message):
    global cancel_flag, active_task
    if active_task:
        cancel_flag = True
        await message.reply("⚠️ Active task canceled.")
    else:
        await message.reply("⚠️ No active task.")

@app.on_message(filters.command("skip") & filters.private & filters.user(CHAT_ID))
async def skip_task(client, message: Message):
    global skip_flag, active_task
    if active_task:
        skip_flag = True
        await message.reply("⚠️ Skipping current task...")
    else:
        await message.reply("⚠️ No active task.")

@app.on_message(filters.command("queue") & filters.private & filters.user(CHAT_ID))
async def show_queue(client, message: Message):
    text = "⌑ Current Queue:\n"
    for i, (msg, path) in enumerate(task_queue[:5]):
        text += f"{i+1}. {os.path.basename(path)}\n"
    if not task_queue:
        text += "Queue is empty."
    await message.reply(text)

# === AUTO MODE (SubsPlease) ===
async def auto_mode():
    while True:
        try:
            recent = get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + ".mkv")
                    task_queue.append((None, file_path))  # Auto
