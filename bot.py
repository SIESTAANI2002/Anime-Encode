import os
import json
import time
import threading
import subprocess
import requests
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Use session string login
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

# === Simple Progress Formatter ===
def progress_bar(percent, length=12):
    done = int(length * percent / 100)
    left = length - done
    return "█" * done + "▒" * left

def format_progress(name, task, percent, eta, past):
    bar = progress_bar(percent)
    msg = (
        f"Name » {name}\n"
        f"⌑ Task   » {task}\n"
        f"⌑ {bar} » {percent:.2f}%\n"
        f"⌑ ETA    : {eta}\n"
        f"⌑ Past   : {past}\n"
    )
    return msg

# === Encode Function ===
def encode_video(input_path, output_path, update_func=None):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-c:s", "copy",
        "-y", output_path
    ]

    start_time = time.time()
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    for line in process.stdout:
        if "time=" in line:
            parts = line.strip().split()
            time_str = next((p.split('=')[1] for p in parts if 'time=' in p), "00:00:00")
            elapsed = int(time.time() - start_time)
            percent = 0  # Placeholder; can calculate if duration known
            if update_func:
                update_func(percent, elapsed, time_str)

    process.wait()
    return output_path

# === Download File ===
def download_file(url, output_path, update_func=None):
    r = requests.get(url, stream=True)
    total_length = int(r.headers.get("content-length", 0))
    chunk_size = 1024 * 1024  # 1MB
    downloaded = 0
    start_time = time.time()

    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                elapsed = int(time.time() - start_time)
                percent = downloaded / total_length * 100 if total_length else 0
                eta = (total_length - downloaded) / (downloaded / elapsed) if downloaded else 0
                if update_func:
                    update_func(percent, int(eta), elapsed)
    return output_path

# === Auto Mode ===
def auto_mode(client: Client):
    while True:
        try:
            res = requests.get(SUBS_API_URL, timeout=15)
            releases = []
            try:
                data = res.json()
                for ep in data.get("data", []):
                    title = ep["release_title"]
                    link = ep["link"]
                    releases.append((title, link))
            except:
                print("⚠️ SubsPlease returned non-JSON, retrying in 60s")
                time.sleep(60)
                continue

            for title, url in releases:
                if url in downloaded_episodes:
                    continue

                file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])

                msg = client.send_message(CHAT_ID, format_progress(title, "Downloading", 0, "?", "0s"))
                def update_download(percent, eta, past):
                    client.edit_message_text(CHAT_ID, msg.message_id, format_progress(title, "Downloading", percent, str(eta)+"s", str(past)+"s"))

                download_file(url, file_path, update_func=update_download)

                output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                def update_encode(percent, past, eta):
                    client.edit_message_text(CHAT_ID, msg.message_id, format_progress(title, "Encoding", percent, str(eta)+"s", str(past)+"s"))

                encode_video(file_path, output_file, update_func=update_encode)

                client.edit_message_text(CHAT_ID, msg.message_id, format_progress(title, "Uploading", 100, "0s", str(int(time.time() - past))+"s"))
                client.send_document(CHAT_ID, output_file)

                os.remove(file_path)
                os.remove(output_file)
                downloaded_episodes.add(url)
                save_tracked()

            time.sleep(600)  # 10 min interval
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Pyrogram Client ===
app = Client(
    "anime_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    msg = message.reply(format_progress(file_name, "Downloading", 0, "?", "0s"))

    def update_download(percent, eta, past):
        client.edit_message_text(message.chat.id, msg.message_id, format_progress(file_name, "Downloading", percent, str(eta)+"s", str(past)+"s"))

    message.download(file_path, progress=update_download)

    output_path = os.path.join(ENCODED_FOLDER, file_name)
    def update_encode(percent, past, eta):
        client.edit_message_text(message.chat.id, msg.message_id, format_progress(file_name, "Encoding", percent, str(eta)+"s", str(past)+"s"))

    encode_video(file_path, output_path, update_func=update_encode)
    client.edit_message_text(message.chat.id, msg.message_id, format_progress(file_name, "Uploading", 100, "0s", "?"))
    client.send_document(message.chat.id, output_path)

    os.remove(file_path)
    os.remove(output_path)

# === Run Bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    app.run()
