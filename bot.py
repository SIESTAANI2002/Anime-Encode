import os
import time
import json
import stat
import asyncio
import requests
import threading
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Your Pyrogram session string
CHAT_ID = int(os.getenv("CHAT_ID"))           # Your chat id
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

# === FFMPEG SETUP ===
FFMPEG_DIR = "bin"
FFMPEG_BIN = os.path.join(FFMPEG_DIR, "ffmpeg")
FFPROBE_BIN = os.path.join(FFMPEG_DIR, "ffprobe")
os.makedirs(FFMPEG_DIR, exist_ok=True)

if not os.path.exists(FFMPEG_BIN) or not os.path.exists(FFPROBE_BIN):
    print("⬇️ Downloading static ffmpeg...")
    url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    r = requests.get(url, stream=True)
    archive_path = os.path.join(FFMPEG_DIR, "ffmpeg.tar.xz")
    with open(archive_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    import tarfile
    with tarfile.open(archive_path, "r:xz") as tar:
        for member in tar.getmembers():
            if "ffmpeg" in member.name or "ffprobe" in member.name:
                member.name = os.path.basename(member.name)
                tar.extract(member, FFMPEG_DIR)
    os.remove(archive_path)
    os.chmod(FFMPEG_BIN, stat.S_IRWXU)
    os.chmod(FFPROBE_BIN, stat.S_IRWXU)
print("✅ FFMPEG ready")

# === QUEUE ===
task_queue = asyncio.Queue()
current_task = None
cancel_flag = False

# === CLIENT ===
app = Client(name="anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

# === UTILITIES ===
def fancy_progress_bar(prefix, transferred, total, speed, eta, done_label="Done"):
    percent = (transferred / total) * 100 if total else 0
    filled_len = int(10 * percent // 100)
    bar = "█" * filled_len + "▒" * (10 - filled_len)
    return (
        f"{prefix}\n"
        f"⌑ {bar} » {percent:.2f}%\n"
        f"⌑ {done_label} : {transferred / 1024 / 1024:.2f}MB of {total / 1024 / 1024:.2f}MB\n"
        f"⌑ Speed : {speed/1024:.2f} KB/s | ETA : {eta}s"
    )

def download_file(url, output_path, message: Message):
    r = requests.get(url, stream=True)
    total = int(r.headers.get("content-length", 0))
    chunk_size = 8192
    downloaded = 0
    start_time = time.time()
    for chunk in r.iter_content(chunk_size=chunk_size):
        if chunk:
            with open(output_path, "ab") as f:
                f.write(chunk)
            downloaded += len(chunk)
            elapsed = max(time.time() - start_time, 1)
            speed = downloaded / elapsed
            eta = int((total - downloaded) / speed) if speed > 0 else 0
            text = fancy_progress_bar("Downloading", downloaded, total, speed, eta)
            try: message.edit(text)
            except: pass
            global cancel_flag
            if cancel_flag:
                cancel_flag = False
                return False
    return True

def encode_video(input_path, output_path, message: Message):
    import json
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    # Audio streams
    probe_cmd = [
        FFPROBE_BIN, "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index,codec_name",
        "-of", "json", input_path
    ]
    result = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    audio_info = json.loads(result.stdout).get("streams", [])
    command = [
        FFMPEG_BIN, "-i", input_path,
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
        if "frame=" in line or "time=" in line:
            try:
                message.edit(f"Encoding...\n{line.strip()}")
            except: pass
        global cancel_flag
        if cancel_flag:
            process.kill()
            cancel_flag = False
            return False
    process.wait()
    return output_path

# === MANUAL ENCODE ===
pending_videos = {}

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    message.download(file_path)
    pending_videos[message.id] = file_path
    message.reply(f"✅ Saved {file_name}. Reply with /encode to process.")

@app.on_message(filters.command("encode"))
def encode_command(client, message: Message):
    if not message.reply_to_message:
        message.reply("Reply to a video/document with /encode to process it.")
        return
    orig_id = message.reply_to_message.id
    if orig_id not in pending_videos:
        message.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
        return
    input_path = pending_videos[orig_id]
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
    message.reply(f"⚙️ Encoding {os.path.basename(input_path)}...")
    encode_video(input_path, output_path, message)
    message.reply(f"✅ Done {os.path.basename(input_path)}")
    client.send_document(message.chat.id, output_path)
    os.remove(input_path)
    os.remove(output_path)
    pending_videos.pop(orig_id, None)

# === AUTO DOWNLOAD + ENCODE ===
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
    return
