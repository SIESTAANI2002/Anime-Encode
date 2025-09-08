import os
import json
import time
import threading
import subprocess
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

# Track downloaded episodes
if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === Pyrogram Client ===
app = Client(
    "anime_userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING,
    workdir="."
)

# === Queue system ===
task_queue = []

def enqueue(task):
    task_queue.append(task)
    if len(task_queue) == 1:
        threading.Thread(target=process_queue).start()

def process_queue():
    while task_queue:
        task = task_queue[0]
        try:
            task()
        except Exception as e:
            print("Task error:", e)
        task_queue.pop(0)

# === Encode Function ===
def encode_video(input_path, output_path, progress_msg):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start = time.time()
    for line in process.stdout:
        if "frame=" in line or "time=" in line:
            # Example: show simple progress bar text
            text = f"⌑ Task » Encoding\n⌑ Info » Processing..."
            try:
                progress_msg.edit(text)
            except:
                pass
    process.wait()

# === Upload Function ===
def upload_file(client, chat_id, file_path, progress_msg):
    def progress(current, total):
        percent = (current / total) * 100
        text = f"⌑ Task » Uploading\n⌑ {percent:.2f}% completed"
        try:
            progress_msg.edit(text)
        except:
            pass

    client.send_document(chat_id, file_path, progress=progress)

# === Handle Video ===
@app.on_message(filters.video | filters.document)
def handle_video(client: Client, message: Message):
    def task():
        file_name = message.document.file_name if message.document else message.video.file_name
        file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
        progress_msg = message.reply(f"⬇️ Downloading {file_name}...")
        message.download(file_path)
        output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
        encode_video(file_path, output_file, progress_msg)
        upload_file(client, message.chat.id, output_file, progress_msg)
        os.remove(file_path)
        os.remove(output_file)

    enqueue(task)

# === Auto Download SubsPlease ===
def get_recent_releases():
    import requests
    releases = []
    try:
        res = requests.get(SUBS_API_URL, timeout=15).json()
        for ep in res.get("data", []):
            title = ep["release_title"]
            link = ep["link"]
            releases.append((title, link))
    except Exception as e:
        print("SubsPlease returned non-JSON, retrying in 60s")
    return releases

def download_file(url, output_path):
    import requests
    r = requests.get(url, stream=True)
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024*1024):
            if chunk:
                f.write(chunk)
    return output_path

def auto_mode(client: Client):
    while True:
        try:
            recent = get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    def task():
                        print(f"⬇️ Downloading {title}")
                        file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                        download_file(url, file_path)
                        output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                        encode_video(file_path, output_file, progress_msg=None)
                        client.send_document(CHAT_ID, output_file)
                        os.remove(file_path)
                        os.remove(output_file)
                        downloaded_episodes.add(url)
                        save_tracked()
                        print(f"✅ Done {title}\n")
                    enqueue(task)
            time.sleep(600)  # check every 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Run Bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    app.run()
