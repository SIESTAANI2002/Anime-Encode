import os
import json
import time
import math
import threading
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.errors import FloodWait

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Pyrogram session string
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

# === Pyrogram client ===
app = Client(
    "anime_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    workdir="./"
)

# === Flood-safe edit ===
def safe_edit(msg: Message, text: str):
    try:
        msg.edit(text)
    except FloodWait as e:
        print(f"Flood wait {e.x}s, sleeping...")
        time.sleep(e.x)
        msg.edit(text)
    except:
        pass

# === Progress bar helpers ===
def format_progress(current, total):
    if total == 0:
        return "0.00%"
    percent = current / total * 100
    bar_length = 20
    filled_len = int(bar_length * percent // 100)
    bar = "█" * filled_len + "▒" * (bar_length - filled_len)
    return f"{percent:.2f}% | {bar}"

def format_size(bytes):
    if bytes < 1024:
        return f"{bytes}B"
    elif bytes < 1024**2:
        return f"{bytes/1024:.2f}KB"
    elif bytes < 1024**3:
        return f"{bytes/1024**2:.2f}MB"
    else:
        return f"{bytes/1024**3:.2f}GB"

# === Download & encode ===
def download_file(url, output_path, progress_msg=None):
    with requests.get(url, stream=True) as r:
        total_length = int(r.headers.get("content-length", 0))
        downloaded = 0
        start_time = time.time()
        chunk_size = 1024*1024  # 1MB
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_msg and time.time() - start_time > 20:  # 20s interval
                        eta = (total_length - downloaded) / (downloaded / (time.time() - start_time + 0.1))
                        text = f"Name » {os.path.basename(output_path)}\n" \
                               f"⌑ Task » Downloading\n" \
                               f"⌑ {format_progress(downloaded, total_length)}\n" \
                               f"⌑ Done   : {format_size(downloaded)} of {format_size(total_length)}\n" \
                               f"⌑ ETA    : {int(eta)}s"
                        safe_edit(progress_msg, text)
                        start_time = time.time()
    return output_path

def encode_video(input_path, output_path, progress_msg=None):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-c:s", "copy",
        "-y", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if progress_msg and "time=" in line:
            safe_edit(progress_msg, f"Name » {os.path.basename(input_path)}\n⌑ Task » Encoding\n⌑ {line.strip()}")
    process.wait()
    return output_path

# === SubsPlease auto download every 10min ===
def auto_mode(client: Client):
    while True:
        try:
            res = requests.get(SUBS_API_URL, timeout=15)
            try:
                releases = res.json().get("data", [])
            except:
                print("⚠️ SubsPlease returned non-JSON, retrying in 60s")
                time.sleep(60)
                continue
            for ep in releases:
                title = ep["release_title"]
                url = ep["link"]
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + ".mkv")
                    progress_msg = client.send_message(CHAT_ID, f"Starting download: {title}")
                    download_file(url, file_path, progress_msg=progress_msg)
                    # auto encode
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_video(file_path, output_file, progress_msg=progress_msg)
                    # upload
                    client.send_document(CHAT_ID, output_file)
                    os.remove(file_path)
                    os.remove(output_file)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)  # 10 min interval
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Handle manual encode ===
pending_videos = {}

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    progress_msg = message.reply(f"Starting download: {file_name}")
    message.download(file_path)
    pending_videos[message.message_id] = (file_path, progress_msg)
    # auto encode
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
    encode_video(file_path, output_path, progress_msg=progress_msg)
    client.send_document(message.chat.id, output_path)
    os.remove(file_path)
    os.remove(output_path)
    pending_videos.pop(message.message_id, None)

# === Run Bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    print("Bot is running...")
    app.run()
