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
CHAT_ID = int(os.getenv("CHAT_ID"))  # channel/group id
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

# === Progress Bar Helper ===
def progress_bar(current, total, length=20):
    filled = int(length * current // total)
    empty = length - filled
    percent = current / total * 100
    return f"[{'█'*filled}{'▒'*empty}] {percent:.2f}%"

# === Encode Function ===
def encode_video(input_path, output_path, progress_callback=None):
    import json
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

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
        if progress_callback and ("frame=" in line or "time=" in line):
            progress_callback(line.strip())
    process.wait()
    return output_path

# === SubsPlease Auto Download ===
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
        print("SubsPlease returned non-JSON, retrying in 60s")
    return releases

def download_file(url, output_path, progress_callback=None):
    r = requests.get(url, stream=True)
    total = int(r.headers.get('content-length', 0))
    downloaded = 0
    last_update = time.time()
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024*1024):  # 1MB chunks
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if progress_callback and (now - last_update > 5):  # update every 5s
                    last_update = now
                    progress_callback(downloaded, total)
    return output_path

# === Pyrogram Client ===
app = Client(name="anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    progress_msg = message.reply(f"⌑ Task   » Downloading\n⌑ {file_name}\n⌑ 0%")
    
    def download_progress(downloaded, total):
        progress = progress_bar(downloaded, total)
        progress_msg.edit_text(f"⌑ Task   » Downloading\n⌑ {file_name}\n⌑ {progress}")
    
    download_file(message.download(file_path), file_path, progress_callback=download_progress)
    
    # Auto start encoding
    pending_videos[message.id] = (file_path, progress_msg)
    encode_command(client, message)

@app.on_message(filters.command("encode"))
def encode_command(client, message: Message):
    if message.reply_to_message:
        orig_msg_id = message.reply_to_message.id
        if orig_msg_id not in pending_videos:
            message.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
            return
        input_path, progress_msg = pending_videos.pop(orig_msg_id)
        output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))

        def encoding_progress(line):
            progress_msg.edit_text(f"⌑ Task   » Encoding\n⌑ {os.path.basename(input_path)}\n⌑ {line}")

        encode_video(input_path, output_path, progress_callback=encoding_progress)
        progress_msg.edit_text(f"✅ Finished Encoding: {os.path.basename(input_path)}")
        client.send_document(message.chat.id, output_path)

        os.remove(input_path)
        os.remove(output_path)
    else:
        message.reply("Reply to a video/document with /encode to process it.")

# === Run Bot ===
if __name__ == "__main__":
    print("Bot is running...")
    app.run()
