import os
import json
import time
import asyncio
import threading
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # your Pyrogram string session
CHAT_ID = int(os.getenv("CHAT_ID"))           # channel/group id for auto-upload
OWNER_ID = int(os.getenv("OWNER_ID"))         # your numeric Telegram user ID
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"
PROGRESS_INTERVAL = 20  # seconds

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
app = Client(session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

# Task queue
task_queue = []
current_task = None
cancel_flag = False

# === Utilities ===
def safe_edit(message: Message, text: str):
    """Edit Telegram message safely, ignoring flood wait."""
    try:
        message.edit_text(text)
    except:
        pass

# === Progress bar helper ===
def progress_bar(total, current, length=20):
    percent = current / total if total else 0
    filled = int(percent * length)
    bar = "█" * filled + "▒" * (length - filled)
    return bar, percent * 100

# === Download function ===
def download_file(url, path, progress_msg=None):
    global cancel_flag
    r = requests.get(url, stream=True)
    total_size = int(r.headers.get("content-length", 0))
    downloaded = 0
    start_time = time.time()
    chunk_size = 1024 * 1024  # 1MB

    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if cancel_flag:
                return False
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if progress_msg and int(time.time() - start_time) >= PROGRESS_INTERVAL:
                    bar, percent = progress_bar(total_size, downloaded)
                    text = f"Name » {os.path.basename(path)}\n⌑ Task   » Downloading\n⌑ {bar} » {percent:.2f}%"
                    safe_edit(progress_msg, text)
                    start_time = time.time()
    return True

# === Encode function ===
def encode_video(input_path, output_path, progress_msg=None):
    global cancel_flag
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
    last_update = time.time()
    for line in process.stdout:
        if cancel_flag:
            process.kill()
            return False
        if progress_msg and ("frame=" in line or "time=" in line):
            if time.time() - last_update >= PROGRESS_INTERVAL:
                text = f"Name » {os.path.basename(input_path)}\n⌑ Task   » Encoding\n⌑ {line[:40]} ..."
                safe_edit(progress_msg, text)
                last_update = time.time()
    process.wait()
    return True

# === Auto mode (SubsPlease) ===
def auto_mode():
    global cancel_flag
    while True:
        try:
            res = requests.get(SUBS_API_URL, timeout=15).json()
            for ep in res.get("data", []):
                title = ep["release_title"]
                link = ep["link"]
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(link)[1])
                    # send progress message
                    progress_msg = app.send_message(OWNER_ID, f"Starting download: {title}")
                    if not download_file(link, file_path, progress_msg):
                        safe_edit(progress_msg, "Download cancelled.")
                        continue
                    # start encoding
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    if not encode_video(file_path, output_file, progress_msg):
                        safe_edit(progress_msg, "Encoding cancelled.")
                        continue
                    app.send_document(CHAT_ID, output_file)
                    os.remove(file_path)
                    os.remove(output_file)
                    downloaded_episodes.add(link)
                    save_tracked()
            time.sleep(600)  # 10 min
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === /cancel command ===
@app.on_message(filters.command("cancel") & filters.user(OWNER_ID))
def cancel_task(client, message: Message):
    global cancel_flag
    cancel_flag = True
    message.reply("⚠️ All tasks cancelled.")

# === Manual encode ===
@app.on_message(filters.command("encode") & filters.user(OWNER_ID))
def manual_encode(client, message: Message):
    global cancel_flag
    cancel_flag = False
    if message.reply_to_message and (message.reply_to_message.document or message.reply_to_message.video):
        file_name = message.reply_to_message.document.file_name if message.reply_to_message.document else message.reply_to_message.video.file_name
        file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
        # download
        progress_msg = message.reply(f"Starting download: {file_name}")
        message.reply_to_message.download(file_path)
        # encode
        output_file = os.path.join(ENCODED_FOLDER, file_name)
        encode_video(file_path, output_file, progress_msg)
        # upload
        app.send_document(message.chat.id, output_file)
        os.remove(file_path)
        os.remove(output_file)
    else:
        message.reply("⚠️ Reply to a video/document to encode.")

# === Run bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, daemon=True).start()
    app.run()
