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
SESSION_STRING = os.getenv("SESSION_STRING")  # session string login
CHAT_ID = int(os.getenv("CHAT_ID"))           # channel/group id
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"

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

# Task queue
task_queue = Queue()
current_task = None

# === Fancy Progress Bar ===
def progress_bar(prefix, progress, downloaded, total, speed, eta, elapsed):
    bar_len = 20
    filled_len = int(round(bar_len * progress / 100))
    bar = "█" * filled_len + "▒" * (bar_len - filled_len)
    return (
        f"{prefix}\n"
        f"⌑ {bar} » {progress:.2f}%\n"
        f"⌑ Done   : {downloaded} of {total}\n"
        f"⌑ Speed  : {speed}\n"
        f"⌑ ETA    : {eta}\n"
        f"⌑ Past   : {elapsed}\n"
        f"____________________________"
    )

# === Encode Function ===
def encode_video(input_path, output_path, callback=None):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index,codec_name",
        "-of", "json", input_path
    ]
    result = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    audio_info = json.loads(result.stdout).get("streams", [])

    command = ["ffmpeg", "-i", input_path, "-vf", "scale=-1:720", "-c:v", "libx264",
               "-preset", "fast", "-crf", "23", "-c:s", "copy"]
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
        if callback:
            callback(line.strip())
    process.wait()
    return output_path

# === SubsPlease Auto Download ===
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

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

def download_file(url, output_path, callback=None):
    r = requests.get(url, stream=True)
    total_size = int(r.headers.get("content-length", 0))
    downloaded = 0
    start_time = time.time()
    chunk_size = 8192
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                elapsed = time.time() - start_time
                speed = downloaded / (1024*1024) / elapsed if elapsed > 0 else 0
                percent = downloaded * 100 / total_size if total_size else 0
                eta = (total_size - downloaded) / (1024*1024*speed) if speed else 0
                if callback:
                    callback(percent, f"{downloaded/1024/1024:.2f}MB", f"{total_size/1024/1024:.2f}MB",
                             f"{speed:.2f}MB/s", f"{eta:.0f}s", f"{elapsed:.0f}s")
    return output_path

def auto_mode(client: Client):
    while True:
        try:
            recent = get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    def dummy_cb(*a): pass
                    download_file(url, file_path, callback=dummy_cb)
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_video(file_path, output_file, callback=dummy_cb)
                    client.send_document(CHAT_ID, output_file)
                    os.remove(file_path)
                    os.remove(output_file)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)  # every 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Pyrogram Client ===
app = Client("anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    msg = message.reply(f"⌑ Downloading » {file_name}")
    message.download(file_path)
    msg.edit(f"⌑ Download complete » {file_name}")
    pending_videos[message.id] = file_path

@app.on_message(filters.command("encode"))
def encode_command(client, message: Message):
    if message.reply_to_message:
        orig_msg_id = message.reply_to_message.id
        if orig_msg_id not in pending_videos:
            message.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
            return
        input_path = pending_videos[orig_msg_id]
        output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
        msg = message.reply(f"⌑ Encoding » {os.path.basename(input_path)}")

        def progress_cb(line):
            try:
                if "frame=" in line or "time=" in line:
                    msg.edit(f"⌑ {line}")
            except: pass

        encode_video(input_path, output_path, callback=progress_cb)
        msg.edit(f"✅ Done » {os.path.basename(input_path)}")
        client.send_document(message.chat.id, output_path)
        os.remove(input_path)
        os.remove(output_path)
        pending_videos.pop(orig_msg_id, None)
    else:
        message.reply("Reply to a video/document with /encode to process it.")

# === Run Bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    app.run()
