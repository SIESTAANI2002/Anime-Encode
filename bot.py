import os
import json
import time
import threading
import subprocess
import requests
import shutil
from datetime import timedelta
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))  # your channel/group ID
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

# === FFMPEG SETUP ===
FFMPEG_BIN = "ffmpeg"
if not shutil.which(FFMPEG_BIN):
    # Download static ffmpeg
    import urllib.request
    import tarfile
    print("⬇️ Downloading static FFMPEG...")
    url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-i686-static.tar.xz"
    urllib.request.urlretrieve(url, "ffmpeg.tar.xz")
    with tarfile.open("ffmpeg.tar.xz") as tar:
        tar.extractall()
    ffmpeg_dir = [d for d in os.listdir() if "ffmpeg-" in d][0]
    FFMPEG_BIN = os.path.join(ffmpeg_dir, "ffmpeg")
    print("✅ FFMPEG ready")

# === Pyrogram Client ===
app = Client("anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

# === Task Queue ===
task_queue = []
current_task = None
cancel_flag = False
skip_flag = False

def fancy_progress(done, total, prefix="", length=20):
    percent = done / total if total else 0
    filled = int(length * percent)
    bar = "█" * filled + "▒" * (length - filled)
    eta = str(timedelta(seconds=int((total - done) / max(0.0001, 1))))
    return f"{prefix} » {bar} {percent*100:.2f}% | {done}/{total} | ETA: {eta}"

def encode_video(input_path, output_path, msg: Message):
    command = [FFMPEG_BIN, "-i", input_path, "-vf", "scale=-1:720",
               "-c:v", "libx264", "-preset", "fast", "-crf", "23",
               "-c:a", "aac", "-b:a", "128k", "-c:s", "copy", "-y", output_path]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if "frame=" in line or "time=" in line:
            try:
                msg.edit_text(f"⚙️ Encoding: {line.strip()}")
            except: pass
    process.wait()
    return output_path

def download_file(url, output_path, msg: Message):
    r = requests.get(url, stream=True)
    total = int(r.headers.get("content-length", 0))
    done = 0
    chunk_size = 8192
    for chunk in r.iter_content(chunk_size=chunk_size):
        if cancel_flag or skip_flag:
            break
        if chunk:
            done += len(chunk)
            with open(output_path, "ab") as f:
                f.write(chunk)
            try:
                msg.edit_text(f"⬇️ Downloading {os.path.basename(output_path)}\n" +
                              fancy_progress(done, total, prefix="Progress"))
            except: pass
    return output_path

def process_task(task):
    global current_task, cancel_flag, skip_flag
    current_task = task
    cancel_flag = False
    skip_flag = False

    message = task["message"]
    file_path = task["file_path"]
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))

    try:
        # Download if needed
        if task.get("url"):
            download_file(task["url"], file_path, message)

        # Encode
        encode_video(file_path, output_path, message)

        # Upload
        message.edit_text(f"📤 Uploading {os.path.basename(output_path)}")
        app.send_document(CHAT_ID, output_path)
    except Exception as e:
        message.edit_text(f"❌ Error: {e}")

    # Cleanup
    if os.path.exists(file_path):
        os.remove(file_path)
    if os.path.exists(output_path):
        os.remove(output_path)

    current_task = None
    if task_queue:
        next_task = task_queue.pop(0)
        threading.Thread(target=process_task, args=(next_task,)).start()

# === Commands ===
@app.on_message(filters.command("cancel"))
def cancel_current(client, message: Message):
    global cancel_flag
    if current_task:
        cancel_flag = True
        message.reply("🛑 Current task cancelled")
    else:
        message.reply("No task is running.")

@app.on_message(filters.command("skip"))
def skip_current(client, message: Message):
    global skip_flag
    if current_task:
        skip_flag = True
        message.reply("⏭️ Current task skipped")
    else:
        message.reply("No task is running.")

@app.on_message(filters.command("queue"))
def show_queue(client, message: Message):
    if task_queue:
        queue_text = "\n".join([f"{i+1}. {os.path.basename(t['file_path'])}" for i, t in enumerate(task_queue)])
        message.reply(f"📝 Queue:\n{queue_text}")
    else:
        message.reply("Queue is empty.")

# === Manual /encode ===
@app.on_message(filters.command("encode") & filters.reply)
def encode_command(client, message: Message):
    if message.reply_to_message:
        media_msg = message.reply_to_message
        file_path = f"downloads/{media_msg.document.file_name if media_msg.document else media_msg.video.file_name}"
        media_msg.download(file_path)
        task = {"message": message, "file_path": file_path}
        if current_task is None:
            threading.Thread(target=process_task, args=(task,)).start()
        else:
            task_queue.append(task)
            message.reply("✅ Added to queue")

# === Auto-download from SubsPlease ===
def auto_mode():
    while True:
        try:
            res = requests.get(SUBS_API_URL, timeout=15).json()
            for ep in res.get("data", []):
                title, url = ep["release_title"], ep["link"]
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    task = {"message": None, "file_path": file_path, "url": url}
                    if current_task is None:
                        threading.Thread(target=process_task, args=(task,)).start()
                    else:
                        task_queue.append(task)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)  # 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Run Bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, daemon=True).start()
    app.run()
