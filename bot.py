import os
import json
import time
import asyncio
import shutil
import threading
import subprocess
import requests
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
ENCODE_CHUNK = 1 * 1024 * 1024  # 1MB per progress update

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# Queue & task control
task_queue = asyncio.Queue()
current_task = None
cancel_task = False
skip_task = False

# Track downloaded episodes
if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === Fancy progress bar helpers ===
def fancy_progress(name, task, done, total, speed, eta, past, user="Ānī"):
    percent = done / total * 100 if total else 0
    bar_len = 20
    filled_len = int(bar_len * percent // 100)
    bar = "█" * filled_len + "▒" * (bar_len - filled_len)
    free_space = shutil.disk_usage("/").free / (1024*1024*1024)
    uptime = time.time() - start_time
    return (
        f"Name » {name}\n"
        f"⌑ Task   » {task}\n"
        f"⌑ {bar} » {percent:.2f}%\n"
        f"⌑ Done   : {done/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
        f"⌑ Speed  : {speed/1024/1024:.2f}MB/s\n"
        f"⌑ ETA    : {eta:.0f}s\n"
        f"⌑ Past   : {past:.0f}s\n"
        f"⌑ ENG    : PyroF v2.2.11\n"
        f"⌑ User   : {user}\n"
        f"____________________________\n"
        f"FREE: {free_space:.2f}GB | DL: {speed/1024/1024:.2f}MB/s\n"
        f"UPTM: {uptime/3600:.0f}h{(uptime%3600)/60:.0f}m | UL: 0B/s"
    )

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
    start = time.time()
    while True:
        line = process.stdout.readline()
        if not line:
            break
        if progress_callback and ("frame=" in line or "time=" in line):
            elapsed = time.time() - start
            progress_callback(line.strip(), elapsed)
    process.wait()
    return output_path

# === SubsPlease Auto Download ===
def get_recent_releases():
    releases = []
    try:
        res = requests.get(SUBS_API_URL, timeout=15)
        res_json = res.json()
        for ep in res_json.get("data", []):
            title = ep["release_title"]
            link = ep["link"]
            releases.append((title, link))
    except Exception:
        print("⚠️ SubsPlease returned non-JSON, retrying in 60s")
    return releases

def download_file(url, output_path, progress_callback=None):
    r = requests.get(url, stream=True)
    total = int(r.headers.get('content-length', 0))
    done = 0
    start = time.time()
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=ENCODE_CHUNK):
            if chunk:
                f.write(chunk)
                done += len(chunk)
                elapsed = time.time() - start
                speed = done/elapsed if elapsed > 0 else 0
                eta = (total-done)/speed if speed>0 else 0
                if progress_callback:
                    progress_callback(done, total, speed, eta, elapsed)
    return output_path

# === Pyrogram Client ===
app = Client("anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

start_time = time.time()
pending_videos = {}

# === Task Queue Worker ===
async def worker():
    global current_task, cancel_task, skip_task
    while True:
        name, path, msg = await task_queue.get()
        if cancel_task:
            await msg.edit("⚠️ Task cancelled")
            cancel_task = False
            task_queue.task_done()
            continue
        current_task = name
        await msg.edit(f"⌑ Task » Processing {name}")
        # Encode video
        def progress(line, elapsed):
            asyncio.create_task(msg.edit(f"⌑ Encoding » {name}\n{line}"))

        encode_video(path, os.path.join(ENCODED_FOLDER, os.path.basename(path)), progress_callback=progress)
        await msg.edit(f"✅ Done {name}")
        task_queue.task_done()
        current_task = None

# === Telegram Commands ===
@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    await message.download(file_path)
    pending_videos[message.id] = file_path
    await message.reply(f"✅ Saved {file_name}. Reply with /encode to start encoding.")

@app.on_message(filters.command("encode"))
async def encode_command(client, message: Message):
    if not message.reply_to_message:
        await message.reply("Reply to a video/document with /encode")
        return
    orig_id = message.reply_to_message.id
    if orig_id not in pending_videos:
        await message.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
        return
    file_path = pending_videos.pop(orig_id)
    await task_queue.put((os.path.basename(file_path), file_path, message))

@app.on_message(filters.command("cancel"))
async def cancel_command(client, message: Message):
    global cancel_task
    cancel_task = True
    await message.reply("⚠️ Current task will be cancelled.")

@app.on_message(filters.command("skip"))
async def skip_command(client, message: Message):
    global skip_task
    skip_task = True
    await message.reply("⚠️ Current task will be skipped.")

# === Auto-download loop ===
async def auto_download():
    while True:
        recent = get_recent_releases()
        for title, url in recent:
            if url not in downloaded_episodes:
                file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                await download_file(url, file_path)
                downloaded_episodes.add(url)
                save_tracked()
                await task_queue.put((title, file_path, None))
        await asyncio.sleep(600)  # every 10 min

# === Run Bot ===
async def main():
    asyncio.create_task(worker())
    asyncio.create_task(auto_download())
    await app.start()
    print("Bot is running...")
    await idle()
    await app.stop()

if __name__ == "__main__":
    from pyrogram import idle
    asyncio.run(main())
