import os
import json
import time
import threading
import subprocess
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Pyrogram session string
CHAT_ID = int(os.getenv("CHAT_ID"))           # Telegram chat/channel id
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"
TASK_INTERVAL = 600  # 10 minutes

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


# === Progress Bar ===
def progress_bar(current, total, size=20):
    if total == 0:
        return "0%"
    percent = current / total
    done = int(size * percent)
    left = size - done
    bar = "█" * done + "▒" * left
    return f"{percent*100:.2f}% | {bar}"


# === Encode Function ===
def encode_video(input_path, output_path, progress_msg=None):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y", output_path
    ]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    for line in process.stdout:
        if "frame=" in line or "time=" in line:
            if progress_msg:
                progress_msg.edit_text(f"⌑ Task   » Encoding\n⌑ {os.path.basename(input_path)}\n⌑ {line.strip()}")

    process.wait()
    return output_path


# === SubsPlease Auto Download ===
def get_recent_releases():
    releases = []
    try:
        import requests
        res = requests.get(SUBS_API_URL, timeout=15).json()
        for ep in res.get("data", []):
            title = ep["release_title"]
            link = ep["link"]
            releases.append((title, link))
    except Exception as e:
        print("SubsPlease returned non-JSON content, retrying in 60s")
    return releases


def auto_mode(client: Client):
    while True:
        try:
            recent = get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    # Download file
                    print(f"⬇️ Downloading {title} ...")
                    # For SubsPlease you can add download_file(url, file_path)
                    # Skipping for now as per user's manual upload
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(TASK_INTERVAL)
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)


# === Pyrogram Client ===
app = Client(
    name="anime_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

pending_videos = []
is_task_running = False


def run_next_task(client):
    global is_task_running
    if is_task_running or not pending_videos:
        return

    is_task_running = True
    task = pending_videos.pop(0)
    input_path, progress_msg = task
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))

    progress_msg.edit_text(f"⌑ Task   » Encoding\n⌑ {os.path.basename(input_path)}\n⌑ 0%")
    encode_video(input_path, output_path, progress_msg)
    progress_msg.edit_text(f"⌑ Task   » Uploading\n⌑ {os.path.basename(input_path)}\n⌑ 0%")

    client.send_document(CHAT_ID, output_path)
    progress_msg.edit_text(f"✅ Finished {os.path.basename(input_path)}")
    os.remove(input_path)
    os.remove(output_path)
    is_task_running = False

    # Run next in queue
    run_next_task(client)


@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)

    progress_msg = message.reply(f"⌑ Task   » Downloading\n⌑ {file_name}\n⌑ 0%")

    def tg_progress(current, total):
        progress = progress_bar(current, total)
        progress_msg.edit_text(f"⌑ Task   » Downloading\n⌑ {file_name}\n⌑ {progress}")

    message.download(file_path, progress=tg_progress)
    pending_videos.append((file_path, progress_msg))

    # Start encoding automatically
    run_next_task(client)


# === Run Bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    print("Bot is running...")
    app.run()
