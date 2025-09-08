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
CHAT_ID = os.getenv("CHAT_ID")
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

# === Pyrogram Client ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

# === Progress Helper ===
def simple_progress_bar(done, total):
    if total == 0:
        return "0%"
    percent = done / total * 100
    return f"{percent:.1f}%"

def format_time(seconds):
    mins, sec = divmod(int(seconds), 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs:02}:{mins:02}:{sec:02}"

# === Download with inline progress ===
def download_file(url, output_path, message: Message):
    r = requests.get(url, stream=True)
    total_length = r.headers.get('content-length')
    if total_length is None:
        with open(output_path, "wb") as f:
            f.write(r.content)
        message.edit(f"✅ Download complete: {os.path.basename(output_path)}")
        return output_path

    dl = 0
    total_length = int(total_length)
    start_time = time.time()
    progress_msg = message.edit(f"Downloading: 0%\nElapsed: 00:00:00  ETA: ?")
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
                dl += len(chunk)
                elapsed = time.time() - start_time
                speed = dl / elapsed if elapsed > 0 else 0
                eta = (total_length - dl) / speed if speed > 0 else 0
                text = f"Downloading: {simple_progress_bar(dl, total_length)}\nElapsed: {format_time(elapsed)}  ETA: {format_time(eta)}"
                try:
                    progress_msg.edit(text)
                except: pass
    progress_msg.edit(f"✅ Download complete: {os.path.basename(output_path)}")
    return output_path

# === Encoding with inline progress ===
def encode_video(input_path, output_path, message: Message):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

    command = ["ffmpeg", "-i", input_path, "-vf", "scale=-1:720",
               "-c:v", "libx264", "-preset", "fast", "-crf", "23",
               "-c:a", "aac", "-b:a", "128k", "-y", output_path]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    progress_msg = message.edit(f"Encoding: 0%")
    for line in process.stdout:
        if "time=" in line:
            # extract time=00:xx:xx.xx
            import re
            m = re.search(r"time=(\d+:\d+:\d+\.\d+)", line)
            if m:
                current_time = m.group(1)
                # Optional: update percent roughly (not exact)
                progress_msg.edit(f"Encoding: {current_time}")
    process.wait()
    progress_msg.edit(f"✅ Encoding complete: {os.path.basename(output_path)}")
    return output_path

# === Upload with inline progress ===
def upload_file(client: Client, chat_id, file_path, message: Message):
    message.edit(f"Uploading: 0%")
    client.send_document(chat_id, file_path)
    message.edit(f"✅ Upload complete: {os.path.basename(file_path)}")

# === Auto mode ===
def auto_mode(client: Client):
    while True:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            res = requests.get(SUBS_API_URL, headers=headers, timeout=15)
            try:
                data = res.json()
            except ValueError:
                print("⚠️ SubsPlease returned non-JSON, skipping")
                time.sleep(60)
                continue

            for ep in data.get("data", []):
                title, url = ep["release_title"], ep["link"]
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    # send temp message to track progress
                    temp_msg = client.send_message(CHAT_ID, f"Starting download: {title}")
                    download_file(url, file_path, temp_msg)
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_video(file_path, output_file, temp_msg)
                    upload_file(client, CHAT_ID, output_file, temp_msg)
                    os.remove(file_path)
                    os.remove(output_file)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Manual reply handler ===
@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    pending_videos[message.id] = file_path
    progress_msg = message.reply(f"Downloading {file_name}...")
    message.download(file_path)
    progress_msg.edit(f"✅ Download complete: {file_name}\nAuto-starting encoding...")
    output_path = os.path.join(ENCODED_FOLDER, file_name)
    encode_video(file_path, output_path, progress_msg)
    upload_file(client, message.chat.id, output_path, progress_msg)
    os.remove(file_path)
    os.remove(output_path)
    pending_videos.pop(message.id, None)

# === Run bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    print("Bot is running...")
    app.run()
