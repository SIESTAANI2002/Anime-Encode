import os
import json
import time
import threading
import asyncio
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))  # channel/group id for auto-upload
USER_ID = int(os.getenv("USER_ID"))  # your telegram user id to control bot
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

# === Pyrogram Client ===
app = Client(
    name="anime_bot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

pending_tasks = {}  # {message_id: {"path": file_path, "cancel": False}}

# Safe edit to handle flood waits
async def safe_edit(msg: Message, text: str):
    try:
        await msg.edit(text)
    except:
        pass

# Progress bar formatter
def format_progress(task_name, file_name, done_bytes, total_bytes, speed, elapsed):
    if total_bytes == 0:
        percent = 0
        eta = "?"
    else:
        percent = done_bytes / total_bytes * 100
        eta = int((total_bytes - done_bytes) / speed) if speed > 0 else "?"
    bar_len = 20
    filled = int(bar_len * percent / 100)
    bar = "█" * filled + "▒" * (bar_len - filled)
    text = f"""Name » {file_name}
⌑ Task   » {task_name}
⌑ {bar} » {percent:.2f}%
⌑ Done   : {done_bytes / 1024 / 1024:.2f}MB of {total_bytes / 1024 / 1024:.2f}MB
⌑ Speed  : {speed / 1024:.2f}KB/s
⌑ ETA    : {eta}s
"""
    return text

# === Download Function ===
def download_file(url, output_path, progress_callback=None, task_id=None):
    r = requests.get(url, stream=True)
    total = int(r.headers.get("Content-Length", 0))
    done = 0
    start_time = time.time()
    chunk_size = 1024 * 1024  # 1 MB
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if task_id and pending_tasks.get(task_id, {}).get("cancel"):
                return False
            if chunk:
                f.write(chunk)
                done += len(chunk)
                elapsed = time.time() - start_time
                speed = done / elapsed if elapsed > 0 else 0
                if progress_callback:
                    progress_callback(done, total, speed, elapsed)
    return True

# === Encode Function ===
def encode_video(input_path, output_path, progress_callback=None, task_id=None):
    import json
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                 "stream=index,codec_name", "-of", "json", input_path]
    result = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    audio_info = json.loads(result.stdout).get("streams", [])
    command = ["ffmpeg", "-i", input_path, "-vf", "scale=-1:720",
               "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:s", "copy"]
    for stream in audio_info:
        idx = stream["index"]
        codec = stream["codec_name"].lower()
        if codec == "aac":
            command += [f"-c:a:{idx}", "aac", f"-b:a:{idx}", "128k"]
        else:
            command += [f"-c:a:{idx}", "aac", f"-b:a:{idx}", "128k"]
    command += ["-y", output_path]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if task_id and pending_tasks.get(task_id, {}).get("cancel"):
            process.kill()
            return False
        if progress_callback:
            progress_callback(line.strip())
    process.wait()
    return output_path

# === Manual Encode Handler ===
@app.on_message(filters.command("encode") & filters.user(USER_ID))
async def encode_command(client, message: Message):
    reply = message.reply_to_message
    if not reply:
        await message.reply("Reply to a video/document to encode.")
        return
    file_name = reply.document.file_name if reply.document else reply.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    task_id = reply.id
    pending_tasks[task_id] = {"path": file_path, "cancel": False}
    progress_msg = await message.reply(f"⌑ Starting download: {file_name}")
    def progress_callback(done, total, speed, elapsed):
        text = format_progress("Downloading", file_name, done, total, speed, elapsed)
        asyncio.run_coroutine_threadsafe(safe_edit(progress_msg, text), app.loop)
    # Download
    download_file(reply.document.file_id, file_path, progress_callback=progress_callback, task_id=task_id)
    # Encode
    output_file = os.path.join(ENCODED_FOLDER, file_name)
    await safe_edit(progress_msg, f"⌑ Encoding {file_name} ...")
    encode_video(file_path, output_file, progress_callback=None, task_id=task_id)
    # Upload
    await safe_edit(progress_msg, f"⌑ Uploading {file_name} ...")
    await client.send_document(message.chat.id, output_file)
    pending_tasks.pop(task_id, None)
    os.remove(file_path)
    os.remove(output_file)
    await safe_edit(progress_msg, f"✅ Finished {file_name}")

# === Cancel Command ===
@app.on_message(filters.command("cancel") & filters.user(USER_ID))
async def cancel_command(client, message: Message):
    for task in pending_tasks.values():
        task["cancel"] = True
    await message.reply("All pending tasks canceled!")

# === Auto Mode (every 10 min) ===
def auto_mode(client: Client):
    while True:
        try:
            res = requests.get(SUBS_API_URL, timeout=15)
            releases = res.json().get("data", [])
            for ep in releases:
                title, url = ep["release_title"], ep["link"]
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + ".mkv")
                    print(f"Downloading {title}")
                    download_file(url, file_path)
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_video(file_path, output_file)
                    client.send_document(CHAT_ID, output_file)
                    os.remove(file_path)
                    os.remove(output_file)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)  # 10 min
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Run Bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    app.run()
