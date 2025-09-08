import os
import json
import time
import threading
import subprocess
import requests
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message
from threading import Event

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

# Load tracked episodes
if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === Task Queue ===
pending_videos = []
is_task_running = False
cancel_event = Event()

# === Safe message edit to avoid FloodWait ===
def safe_edit(message, text):
    try:
        message.edit(text)
    except:
        pass

# === Progress helper ===
def progress_bar(current, total, length=20):
    filled = int(length * current / total)
    return "█" * filled + "▒" * (length - filled)

# === Download ===
def download_file(url, output_path, progress_msg=None):
    r = requests.get(url, stream=True)
    total_length = int(r.headers.get("content-length", 0))
    downloaded = 0
    last_update = time.time()

    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024*1024):
            if cancel_event.is_set():
                return False
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if time.time() - last_update > 20:
                    pct = (downloaded / total_length) * 100 if total_length else 0
                    bar = progress_bar(downloaded, total_length)
                    safe_edit(progress_msg, f"⌑ Task   » Downloading\n⌑ {os.path.basename(output_path)}\n⌑ {pct:.2f}% | {bar}")
                    last_update = time.time()
    return True

# === Encode ===
def encode_video(input_path, output_path, progress_msg=None):
    ext = os.path.splitext(input_path)[1]
    output_path = os.path.splitext(output_path)[0] + ext
    command = [
        "ffmpeg", "-y", "-i", input_path, "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last_update = time.time()
    while True:
        line = process.stdout.readline()
        if not line:
            break
        if progress_msg and ("frame=" in line or "time=" in line):
            if time.time() - last_update > 20:
                safe_edit(progress_msg, f"⌑ Task   » Encoding\n⌑ {os.path.basename(input_path)}\n⌑ {line.strip()}")
                last_update = time.time()
    process.wait()
    return output_path

# === Run next task ===
def run_next_task(client):
    global is_task_running
    if is_task_running or not pending_videos:
        return

    is_task_running = True
    task = pending_videos.pop(0)
    input_path, progress_msg = task
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))

    if cancel_event.is_set():
        safe_edit(progress_msg, f"❌ Task canceled: {os.path.basename(input_path)}")
        is_task_running = False
        return

    safe_edit(progress_msg, f"⌑ Task   » Encoding\n⌑ {os.path.basename(input_path)}\n⌑ 0%")
    encode_video(input_path, output_path, progress_msg)

    if cancel_event.is_set():
        safe_edit(progress_msg, f"❌ Task canceled during encoding: {os.path.basename(input_path)}")
        is_task_running = False
        return

    safe_edit(progress_msg, f"⌑ Task   » Uploading\n⌑ {os.path.basename(input_path)}\n⌑ 0%")
    client.send_document(CHAT_ID, output_path)
    safe_edit(progress_msg, f"✅ Finished {os.path.basename(input_path)}")

    os.remove(input_path)
    os.remove(output_path)
    is_task_running = False
    run_next_task(client)

# === Pyrogram Client ===
app = Client(name="anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    progress_msg = message.reply(f"⌑ Task   » Downloading\n⌑ {file_name}\n⌑ 0%")
    pending_videos.append((file_path, progress_msg))
    message.download(file_path)
    run_next_task(client)

@app.on_message(filters.command("cancel") & filters.user(YOUR_TELEGRAM_USER_ID))
def cancel_tasks(client, message: Message):
    cancel_event.set()
    pending_videos.clear()
    message.reply("❌ All tasks canceled.")
    cancel_event.clear()

# === Auto Download from SubsPlease ===
def get_recent_releases():
    releases = []
    try:
        res = requests.get(SUBS_API_URL, timeout=15).json()
        for ep in res.get("data", []):
            title = ep["release_title"]
            link = ep["link"]
            releases.append((title, link))
    except Exception as e:
        print("SubsPlease returned non-JSON content, retrying in 60s", e)
    return releases

def auto_mode(client: Client):
    while True:
        try:
            recent = get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + ".mkv")
                    progress_msg = client.send_message(CHAT_ID, f"⌑ Task   » Downloading\n⌑ {title}\n⌑ 0%")
                    success = download_file(url, file_path, progress_msg)
                    if not success:
                        continue
                    pending_videos.append((file_path, progress_msg))
                    run_next_task(client)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)  # 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Run Bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    print("Bot is running...")
    app.run()
