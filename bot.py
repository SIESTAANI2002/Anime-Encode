import os
import json
import time
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

# Load tracked episodes
if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === Simple inline progress bar helper ===
def progress_bar(percentage, length=20):
    filled = int(length * percentage // 100)
    bar = "█" * filled + "░" * (length - filled)
    return bar

def format_size(size):
    for unit in ['B','KB','MB','GB']:
        if size < 1024:
            return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"

def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}h{m}m{s}s"
    elif m > 0:
        return f"{m}m{s}s"
    else:
        return f"{s}s"

# === Pyrogram Client ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

# === Download function with inline progress ===
def download_file(url, file_path, msg: Message):
    r = requests.get(url, stream=True)
    total_length = r.headers.get("content-length")
    if total_length is None:
        with open(file_path, "wb") as f:
            f.write(r.content)
        return
    total_length = int(total_length)
    downloaded = 0
    start_time = time.time()
    # send initial message
    progress_msg = msg.reply_text(f"Filename: {os.path.basename(file_path)}\nDownloading: 0%")
    with open(file_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024*1024):  # 1MB
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                percent = downloaded / total_length * 100
                elapsed = time.time() - start_time
                speed = downloaded / elapsed
                eta = (total_length - downloaded) / speed if speed > 0 else 0
                text = (
                    f"Filename: {os.path.basename(file_path)}\n"
                    f"Downloading: {percent:.2f}% [{progress_bar(percent)}]\n"
                    f"Done: {format_size(downloaded)} of {format_size(total_length)}\n"
                    f"Speed: {format_size(speed)}/s | ETA: {format_time(eta)}"
                )
                progress_msg.edit_text(text)
    progress_msg.edit_text(f"✅ Download complete: {os.path.basename(file_path)}")
    return file_path

# === Encoding function with inline progress ===
def encode_video(input_path, output_path, msg: Message):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-y", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start_time = time.time()
    for line in process.stdout:
        if "time=" in line:
            time_str = line.split("time=")[1].split(" ")[0]
            h, m, s = time_str.split(":")
            elapsed_sec = int(float(h)*3600 + float(m)*60 + float(s))
            # fake total duration as 1.0x for progress bar
            percent = min(elapsed_sec / (elapsed_sec+1) * 100, 100)
            progress_text = (
                f"Filename: {os.path.basename(input_path)}\n"
                f"Encoding: {percent:.2f}% [{progress_bar(percent)}]\n"
                f"Elapsed: {format_time(time.time()-start_time)}"
            )
            try:
                msg.edit_text(progress_text)
            except:
                pass
    msg.edit_text(f"✅ Encoding complete: {os.path.basename(input_path)}")
    return output_path

# === Upload function with inline progress ===
def upload_file(client: Client, chat_id, file_path, msg: Message):
    msg.edit_text(f"Uploading: {os.path.basename(file_path)}")
    client.send_document(chat_id, file_path)
    msg.edit_text(f"✅ Upload complete: {os.path.basename(file_path)}")

# === Auto download loop ===
def auto_mode(client: Client):
    while True:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(SUBS_API_URL, headers=headers, timeout=15)
            try:
                data = res.json()
            except ValueError:
                print("⚠️ SubsPlease returned non-JSON, retrying in 60s")
                time.sleep(60)
                continue
            for ep in data.get("data", []):
                title = ep["release_title"]
                url = ep["link"]
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    dummy_msg = app.send_message(CHAT_ID, f"Starting download: {title}")
                    download_file(url, file_path, dummy_msg)
                    # auto encode after download
                    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_video(file_path, output_path, dummy_msg)
                    upload_file(client, CHAT_ID, output_path, dummy_msg)
                    os.remove(file_path)
                    os.remove(output_path)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)  # 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Manual upload / encode ===
@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    msg = message.reply_text(f"Downloading: {file_name}")
    message.download(file_path)
    # auto start encoding after download
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
    encode_video(file_path, output_path, msg)
    upload_file(client, message.chat.id, output_path, msg)
    os.remove(file_path)
    os.remove(output_path)

# === Run bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    print("Bot is running...")
    app.run()
