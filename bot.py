import os
import json
import time
import threading
import subprocess
import requests
from queue import Queue
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # your Pyrogram session string
CHAT_ID = int(os.getenv("CHAT_ID"))           # channel/group id for auto-upload

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

# === QUEUE SYSTEM ===
task_queue = Queue()
current_task = None
cancel_flag = False

def add_task(task):
    task_queue.put(task)

def skip_task():
    global cancel_flag
    cancel_flag = True

def queue_list():
    return list(task_queue.queue)

# === PROGRESS BAR FUNCTION ===
def progress_bar(prefix, total_bytes, current_bytes, start_time):
    percent = (current_bytes / total_bytes) * 100
    blocks = int(percent // 5)
    bar = "█" * blocks + "▒" * (20 - blocks)
    elapsed = int(time.time() - start_time)
    speed = current_bytes / max(elapsed,1) / (1024*1024)
    eta = int((total_bytes - current_bytes) / max(current_bytes / max(elapsed,1),1))
    return (f"{prefix}\n"
            f"⌑ [{bar}] {percent:.2f}%\n"
            f"⌑ {current_bytes/(1024*1024):.2f}MB / {total_bytes/(1024*1024):.2f}MB\n"
            f"⌑ DL: {speed:.2f}MB/s | ETA: {eta}s | Past: {elapsed}s")

# === DOWNLOAD FUNCTION ===
def download_file(url, dest, msg=None):
    start_time = time.time()
    r = requests.get(url, stream=True)
    total_size = int(r.headers.get("content-length", 0))
    downloaded = 0
    with open(dest, "wb") as f:
        for chunk in r.iter_content(8192):
            if cancel_flag:
                msg.edit("⚠️ Task cancelled")
                return False
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                if msg:
                    msg.edit(progress_bar(f"⬇️ Downloading {os.path.basename(dest)}", total_size, downloaded, start_time))
    return True

# === ENCODE FUNCTION ===
def encode_video(input_path, output_path, msg=None):
    start_time = time.time()
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
    for line in process.stdout:
        if "time=" in line and msg:
            msg.edit(f"⚙️ Encoding {os.path.basename(input_path)}\n{line.strip()}")
        if cancel_flag:
            process.kill()
            msg.edit("⚠️ Task cancelled")
            return False
    process.wait()
    return True

# === AUTO DOWNLOAD FROM SUBSPLEASE ===
def get_recent_releases():
    releases = []
    try:
        res = requests.get(SUBS_API_URL, timeout=15).json()
        for ep in res.get("data", []):
            title = ep["release_title"]
            link = ep["link"]
            releases.append((title, link))
    except Exception as e:
        print("SubsPlease API error:", e)
    return releases

def auto_task(client):
    while True:
        try:
            recent = get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    msg = client.send_message(CHAT_ID, f"⬇️ Starting auto-download: {title}")
                    if download_file(url, file_path, msg):
                        output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                        encode_video(file_path, output_file, msg)
                        client.send_document(CHAT_ID, output_file)
                        os.remove(file_path)
                        os.remove(output_file)
                        downloaded_episodes.add(url)
                        save_tracked()
                        msg.edit(f"✅ Done {title}")
            time.sleep(600)
        except Exception as e:
            print("Auto task error:", e)
            time.sleep(60)

# === PYROGRAM CLIENT ===
app = Client(
    "anime_bot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

pending_videos = {}

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    msg = message.reply(f"⬇️ Downloading {file_name}...")
    message.download(file_path)
    pending_videos[message.id] = (file_path, msg)
    add_task(("encode", message.id))

@app.on_message(filters.command("encode"))
def encode_command(client, message: Message):
    if message.reply_to_message:
        orig_msg_id = message.reply_to_message.id
        if orig_msg_id in pending_videos:
            add_task(("encode", orig_msg_id))
        else:
            message.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
    else:
        message.reply("Reply to a video/document with /encode to process it.")

@app.on_message(filters.command("cancel"))
def cancel_command(client, message: Message):
    skip_task()
    message.reply("⚠️ Current task cancelled.")

@app.on_message(filters.command("queue"))
def queue_command(client, message: Message):
    q = queue_list()
    text = "Pending tasks:\n" + "\n".join([str(t) for t in q]) if q else "Queue empty"
    message.reply(text)

# === TASK WORKER ===
def worker():
    global current_task, cancel_flag
    while True:
        task = task_queue.get()
        cancel_flag = False
        current_task = task
        action, msg_id = task
        if msg_id in pending_videos:
            file_path, msg = pending_videos[msg_id]
            output_path = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
            encode_video(file_path, output_path, msg)
            app.send_document(CHAT_ID, output_path)
            os.remove(file_path)
            os.remove(output_path)
            pending_videos.pop(msg_id, None)
        current_task = None
        task_queue.task_done()

threading.Thread(target=worker, daemon=True).start()
threading.Thread(target=auto_task, args=(app,), daemon=True).start()

# === RUN BOT ===
print("✅ Bot is running...")
app.run()
