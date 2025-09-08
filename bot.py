import os
import json
import time
import math
import asyncio
import aiohttp
import subprocess
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # your session string
CHAT_ID = int(os.getenv("CHAT_ID"))           # auto-upload channel/group
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"
AUTO_DOWNLOAD_INTERVAL = 600  # 10 min

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# === Load tracked episodes ===
if Path(TRACK_FILE).exists():
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === Initialize Pyrogram client with session string ===
app = Client(
    name="anime_userbot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

# === Utilities ===
def format_bytes(size):
    for unit in ["B","KB","MB","GB","TB"]:
        if size < 1024.0:
            return f"{size:.2f}{unit}"
        size /= 1024.0

def create_progress_bar(progress):
    blocks = 20
    filled = int(blocks * progress)
    return "█" * filled + "▒" * (blocks - filled)

async def fancy_update(msg_obj, filename, task, done, total, speed, eta, past):
    percent = done / total if total else 0
    bar = create_progress_bar(percent)
    text = f"""
Name » {filename}
⌑ Task   » {task}
⌑ {bar} » {percent*100:.2f}%
⌑ Done   : {format_bytes(done)} of {format_bytes(total)}
⌑ Speed  : {format_bytes(speed)}/s
⌑ ETA    : {int(eta)}s
⌑ Past   : {int(past)}s
⌑ ENG    : PyroF v2.2.11
⌑ User   : Ānī

____________________________
FREE: {format_bytes(get_free_space())} | DL: {format_bytes(speed)}/s
UPTM: {uptime()} | UL: 0B/s
"""
    await msg_obj.edit(text)

def get_free_space():
    """Return free disk space in bytes"""
    statvfs = os.statvfs("/")
    return statvfs.f_frsize * statvfs.f_bavail

def uptime():
    """Return uptime string"""
    with open("/proc/uptime") as f:
        secs = float(f.readline().split()[0])
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}h{int(m)}m{int(s)}s"

# === Async SubsPlease fetch ===
async def get_recent_releases():
    releases = []
    try:
        async with aiohttp.ClientSession(headers={"User-Agent":"Mozilla/5.0"}) as session:
            async with session.get(SUBS_API_URL, timeout=15) as resp:
                if "application/json" not in resp.headers.get("Content-Type", ""):
                    print("⚠️ SubsPlease returned non-JSON, retrying in 60s")
                    await asyncio.sleep(60)
                    return await get_recent_releases()
                data = await resp.json()
                for ep in data.get("data", []):
                    title = ep["release_title"]
                    link = ep["link"]
                    releases.append((title, link))
    except Exception as e:
        print("⚠️ SubsPlease API error:", e)
        await asyncio.sleep(60)
    return releases

# === Download with progress ===
async def download_file(url, path, msg_obj):
    CHUNK_SIZE = 1024*1024  # 1 MB
    done = 0
    start_time = time.time()
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            total = int(r.headers.get("content-length", 0))
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(CHUNK_SIZE):
                    f.write(chunk)
                    done += len(chunk)
                    elapsed = time.time() - start_time
                    speed = done / elapsed if elapsed else 0
                    eta = (total - done) / speed if speed else 0
                    await fancy_update(msg_obj, os.path.basename(path), "Downloading", done, total, speed, eta, elapsed)
    await fancy_update(msg_obj, os.path.basename(path), "Download Complete", done, total, speed, 0, elapsed)

# === Encode video with progress ===
def encode_video(input_path, output_path, msg_obj):
    import shlex
    import threading

    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    command = f"ffmpeg -i {shlex.quote(input_path)} -vf scale=-1:720 -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -y {shlex.quote(output_path)}"
    
    process = subprocess.Popen(shlex.split(command), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def read_output():
        start = time.time()
        for line in process.stdout:
            if "time=" in line:
                parts = line.strip().split()
                t_idx = [i for i,p in enumerate(parts) if p.startswith("time=")]
                if t_idx:
                    time_str = parts[t_idx[0]].split("=")[1]
                    # convert hh:mm:ss.ms to seconds
                    h, m, s = map(float, time_str.split(":"))
                    elapsed_sec = h*3600 + m*60 + s
                    # naive ETA: assume total length 7 min for display
                    total_sec = 7*60
                    percent = elapsed_sec / total_sec
                    bar = create_progress_bar(percent)
                    asyncio.run_coroutine_threadsafe(
                        msg_obj.edit(f"⌑ Encoding » {os.path.basename(input_path)}\n⌑ {bar} » {percent*100:.2f}%"), app.loop
                    )
        process.wait()

    t = threading.Thread(target=read_output)
    t.start()
    t.join()
    return output_path

# === Queue system ===
TASK_QUEUE = []
CURRENT_TASK = None

async def process_queue():
    global CURRENT_TASK
    while True:
        if TASK_QUEUE and CURRENT_TASK is None:
            CURRENT_TASK = TASK_QUEUE.pop(0)
            await CURRENT_TASK()
            CURRENT_TASK = None
        await asyncio.sleep(2)

# === Auto mode task ===
async def auto_mode():
    while True:
        recent = await get_recent_releases()
        for title, url in recent:
            if url not in downloaded_episodes:
                file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                msg = await app.send_message(CHAT_ID, f"⬇️ Starting auto download: {title}")
                await download_file(url, file_path, msg)
                output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                encode_video(file_path, output_file, msg)
                await app.send_document(CHAT_ID, output_file)
                os.remove(file_path)
                os.remove(output_file)
                downloaded_episodes.add(url)
                save_tracked()
        await asyncio.sleep(AUTO_DOWNLOAD_INTERVAL)

# === Manual encode handler ===
@app.on_message(filters.command("encode") & filters.me)
async def manual_encode(client: Client, message: Message):
    if not message.reply_to_message or (not message.reply_to_message.document and not message.reply_to_message.video):
        await message.reply("⚠️ Reply to a video/document to encode.")
        return

    async def task():
        file_name = message.reply_to_message.document.file_name if message.reply_to_message.document else message.reply_to_message.video.file_name
        file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
        msg = await message.reply(f"⌑ Starting manual download: {file_name}")
        await message.reply("⚙️ Downloading...")
        await message.reply("⏳ Please wait...")
        file = await message.reply_to_message.download(file_path)
        output_file = os.path.join(ENCODED_FOLDER, file_name)
        encode_video(file_path, output_file, msg)
        await app.send_document(message.chat.id, output_file)
        os.remove(file_path)
        os.remove(output_file)

    TASK_QUEUE.append(task)

# === Run bot ===
if __name__ == "__main__":
    print("Bot is running...")
    loop = asyncio.get_event_loop()
    loop.create_task(auto_mode())
    loop.create_task(process_queue())
    app.run()
