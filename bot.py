import os
import json
import time
import subprocess
import requests
import threading
from pyrogram import Client, filters
from pyrogram.types import Message

# ================= CONFIG =================
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Pyrogram session string
CHAT_ID = int(os.getenv("CHAT_ID"))           # Channel/Group for auto uploads
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

# ============ HELPER FUNCTIONS ============
def simple_progress_bar(done, total):
    if total == 0:
        return "0%"
    percent = done / total * 100
    return f"{percent:.2f}% | 100%"

def format_time(seconds):
    mins, sec = divmod(int(seconds), 60)
    hrs, mins = divmod(mins, 60)
    return f"{hrs:02}:{mins:02}:{sec:02}"

def download_file(url, output_path, message: Message):
    r = requests.get(url, stream=True)
    total_length = r.headers.get('content-length')
    if total_length is None:
        with open(output_path, "wb") as f:
            f.write(r.content)
        message.edit(f"Name » {os.path.basename(output_path)}\n⌑ Task » Downloading\n⌑ 100% | 100%\n⌑ Finished  : Done")
        return output_path

    dl = 0
    total_length = int(total_length)
    start_time = time.time()
    last_update = 0
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
                dl += len(chunk)
                elapsed = time.time() - start_time
                speed = dl / elapsed if elapsed > 0 else 0
                eta = (total_length - dl) / speed if speed > 0 else 0
                if time.time() - last_update > 20:
                    last_update = time.time()
                    text = f"Name » {os.path.basename(output_path)}\n" \
                           f"⌑ Task » Downloading\n" \
                           f"⌑ {simple_progress_bar(dl, total_length)}\n" \
                           f"⌑ Finished  : {format_time(eta)} (ETA)"
                    try:
                        message.edit(text)
                    except: pass
    message.edit(f"Name » {os.path.basename(output_path)}\n⌑ Task » Downloading\n⌑ 100% | 100%\n⌑ Finished  : Done")
    return output_path

def encode_video(input_path, output_path, message: Message):
    command = ["ffmpeg", "-i", input_path, "-vf", "scale=-1:720",
               "-c:v", "libx264", "-preset", "fast", "-crf", "23",
               "-c:a", "aac", "-b:a", "128k", "-y", output_path]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last_update = 0
    while True:
        line = process.stdout.readline()
        if not line:
            break
        if time.time() - last_update > 20:
            last_update = time.time()
            try:
                message.edit(f"Name » {os.path.basename(input_path)}\n⌑ Task » Encoding\n⌑ In Progress...\n⌑ Finished  : ?")
            except: pass
    process.wait()
    message.edit(f"Name » {os.path.basename(input_path)}\n⌑ Task » Encoding\n⌑ 100% | 100%\n⌑ Finished  : Done")
    return output_path

def upload_file(client: Client, chat_id, file_path, message: Message):
    message.edit(f"Name » {os.path.basename(file_path)}\n⌑ Task » Uploading\n⌑ 0% | 100%\n⌑ Finished  : ?")
    client.send_document(chat_id, file_path)
    message.edit(f"Name » {os.path.basename(file_path)}\n⌑ Task » Uploading\n⌑ 100% | 100%\n⌑ Finished  : Done")

# ============ SUBSPLASE AUTO MODE ============
def get_recent_releases():
    releases = []
    try:
        res = requests.get(SUBS_API_URL, timeout=15)
        data = res.json()
        for ep in data.get("data", []):
            title = ep["release_title"]
            link = ep["link"]
            releases.append((title, link))
    except Exception as e:
        print("SubsPlease returned non-JSON, retrying in 60s", e)
    return releases

def auto_mode(client: Client):
    while True:
        try:
            recent = get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    dummy_msg = client.send_message(CHAT_ID, f"Starting download: {title}")
                    download_file(url, file_path, dummy_msg)
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_video(file_path, output_file, dummy_msg)
                    upload_file(client, CHAT_ID, output_file, dummy_msg)
                    os.remove(file_path)
                    os.remove(output_file)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)  # 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# ============ PYROGRAM CLIENT ============
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    dummy_msg = message.reply(f"⬇️ Downloading {file_name}...")
    download_file(message.download(file_path), file_path, dummy_msg)
    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
    encode_video(file_path, output_file, dummy_msg)
    upload_file(client, message.chat.id, output_file, dummy_msg)
    os.remove(file_path)
    os.remove(output_file)

# ============ RUN BOT ============
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    print("Bot is running...")
    app.run()
