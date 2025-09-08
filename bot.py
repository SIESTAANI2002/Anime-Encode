import os
import json
import time
import subprocess
import threading
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Pyrogram session string
CHAT_ID = int(os.getenv("CHAT_ID"))           # Auto-upload chat
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

# === Utilities ===
def format_time(sec):
    h, m = divmod(int(sec)//60, 60)
    s = int(sec) % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def progress_bar(percent, length=20):
    filled = int(length * percent / 100)
    return "█" * filled + "▒" * (length - filled)

# === Download & Encode ===
def download_file(url, output_path, msg: Message):
    r = requests.get(url, stream=True)
    total = int(r.headers.get("content-length", 0))
    chunk_size = 1024*1024  # 1 MB
    downloaded = 0
    start = time.time()

    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                percent = downloaded/total*100 if total else 0
                elapsed = time.time() - start
                speed = downloaded/elapsed/1024/1024 if elapsed else 0
                eta = (total - downloaded)/(speed*1024*1024) if speed > 0 else 0
                try:
                    msg.edit_text(
                        f"Filename: {os.path.basename(output_path)}\n"
                        f"Downloading: {percent:.2f}% [{progress_bar(percent)}]\n"
                        f"Done: {downloaded/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
                        f"Speed: {speed:.2f}MB/s | ETA: {format_time(eta)}"
                    )
                except: pass
    msg.edit_text(f"✅ Download complete: {os.path.basename(output_path)}")

def get_video_duration(path):
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "format=duration",
        "-of", "json", path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except:
        return 0

def encode_video(input_path, output_path, msg: Message):
    duration = get_video_duration(input_path)
    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720", "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-y", output_path
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start = time.time()

    import re
    time_pattern = re.compile(r"time=(\d+):(\d+):(\d+)\.\d+")
    for line in process.stdout:
        match = time_pattern.search(line)
        if match and duration > 0:
            h, m, s = map(int, match.groups())
            elapsed_sec = h*3600 + m*60 + s
            percent = min(elapsed_sec/duration*100, 100)
            elapsed = time.time() - start
            eta = elapsed_sec/percent*(100-percent) if percent > 0 else 0
            try:
                msg.edit_text(
                    f"Filename: {os.path.basename(input_path)}\n"
                    f"Encoding: {percent:.2f}% [{progress_bar(percent)}]\n"
                    f"Elapsed: {format_time(elapsed)} | ETA: {format_time(eta)}"
                )
            except: pass
    msg.edit_text(f"✅ Encoding complete: {os.path.basename(input_path)}")

# === Pyrogram Client ===
app = Client(name="anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    msg = message.reply(f"⬇️ Starting download: {file_name}")
    download_file(message.download(file_path), file_path, msg)
    
    # Auto-start encoding
    output_path = os.path.join(ENCODED_FOLDER, file_name)
    encode_msg = message.reply(f"⚙️ Encoding {file_name}")
    encode_video(file_path, output_path, encode_msg)
    client.send_document(message.chat.id, output_path)
    
    os.remove(file_path)
    os.remove(output_path)

# === Auto Download from SubsPlease ===
def auto_mode():
    while True:
        try:
            res = requests.get(SUBS_API_URL, timeout=15)
            data = res.json()
            for ep in data.get("data", []):
                title, url = ep["release_title"], ep["link"]
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    msg = app.send_message(CHAT_ID, f"⬇️ Auto downloading {title}")
                    download_file(url, file_path, msg)
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_msg = app.send_message(CHAT_ID, f"⚙️ Encoding {title}")
                    encode_video(file_path, output_file, encode_msg)
                    app.send_document(CHAT_ID, output_file)
                    os.remove(file_path)
                    os.remove(output_file)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)  # check every 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

if __name__ == "__main__":
    threading.Thread(target=auto_mode, daemon=True).start()
    print("Bot is running...")
    app.run()
