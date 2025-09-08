import os
import json
import time
import asyncio
import aiohttp
import subprocess
import shutil
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
task_queue = asyncio.Queue()
current_task = None
cancel_flag = False

# === PROGRESS BAR ===
def make_progress_bar(current, total, size=20):
    ratio = current / total if total else 0
    filled = int(ratio * size)
    bar = "█" * filled + "▒" * (size - filled)
    percent = ratio * 100
    return f"{bar} » {percent:.2f}%"

# === SubsPlease Auto Download ===
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

async def get_recent_releases():
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(SUBS_API_URL, timeout=15) as resp:
                if "application/json" not in resp.headers.get("Content-Type", ""):
                    print("⚠️ SubsPlease returned non-JSON, retrying in 60s")
                    await asyncio.sleep(60)
                    return await get_recent_releases()
                data = await resp.json()
                releases = []
                for ep in data.get("data", []):
                    title = ep.get("release_title")
                    link = ep.get("link")
                    if title and link:
                        releases.append((title, link))
                return releases
    except Exception as e:
        print("SubsPlease API error:", e)
        await asyncio.sleep(60)
        return await get_recent_releases()

async def download_file(url, output_path, message=None):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            total = int(r.headers.get('content-length', 0))
            downloaded = 0
            start = time.time()
            with open(output_path, "wb") as f:
                async for chunk in r.content.iter_chunked(1024*1024):  # 1MB chunks
                    if cancel_flag:
                        print("Download canceled")
                        return None
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start
                    speed = downloaded / 1024 / 1024 / elapsed if elapsed > 0 else 0
                    eta = (total - downloaded) / (speed * 1024 * 1024) if speed > 0 else 0
                    if message:
                        bar = make_progress_bar(downloaded, total)
                        await message.edit(
                            f"Name » {os.path.basename(output_path)}\n"
                            f"⌑ Task   » Downloading\n"
                            f"⌑ {bar}\n"
                            f"⌑ Done   : {downloaded/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
                            f"⌑ Speed  : {speed:.2f}MB/s\n"
                            f"⌑ ETA    : {int(eta)}s"
                        )
    return output_path

def encode_video(input_path, output_path, message=None):
    ext = os.path.splitext(input_path)[1]
    output_path = os.path.splitext(output_path)[0] + ext
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-c:s", "copy",
        "-y", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if message and "time=" in line:
            try:
                await asyncio.sleep(0.1)
                # You can parse line to show frame/time progress if needed
                # Here we just update simple progress
                await message.edit(f"⌑ Encoding » {os.path.basename(input_path)}\n⌑ {line[:50]} ...")
            except: pass
    process.wait()
    return output_path

# === BOT CLIENT ===
app = Client(name="anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

# === HANDLERS ===
@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    task = {"message": message, "file_path": file_path}
    await task_queue.put(task)
    await message.reply(f"✅ Task queued: {file_name}")

@app.on_message(filters.command("encode"))
async def manual_encode(client, message: Message):
    if message.reply_to_message:
        await task_queue.put({"message": message.reply_to_message, "manual": True})
        await message.reply("✅ Manual encode task queued")
    else:
        await message.reply("⚠️ Reply to a video/document with /encode")

@app.on_message(filters.command("cancel"))
async def cancel_task(client, message: Message):
    global cancel_flag
    cancel_flag = True
    await message.reply("⚠️ Current task canceled")

# === AUTO MODE LOOP ===
async def auto_mode():
    while True:
        try:
            recent = await get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + ".mkv")
                    msg = await app.send_message(CHAT_ID, f"⬇️ Downloading {title}")
                    await download_file(url, file_path, msg)
                    await msg.edit(f"✅ Download complete » {title}")
                    downloaded_episodes.add(url)
                    save_tracked()
            await asyncio.sleep(600)  # 10 min
        except Exception as e:
            print("Auto mode error:", e)
            await asyncio.sleep(60)

# === TASK WORKER ===
async def worker():
    global current_task, cancel_flag
    while True:
        task = await task_queue.get()
        current_task = task
        cancel_flag = False
        msg = task["message"]
        file_path = task.get("file_path")
        if not os.path.exists(file_path):
            await msg.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
            task_queue.task_done()
            continue
        # Encode
        output_path = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
        await msg.reply(f"⚙️ Encoding {os.path.basename(file_path)}...")
        encode_video(file_path, output_path, msg)
        await app.send_document(msg.chat.id, output_path)
        os.remove(file_path)
        os.remove(output_path)
        task_queue.task_done()

# === RUN ===
async def main():
    asyncio.create_task(auto_mode())
    asyncio.create_task(worker())
    await app.start()
    print("Bot is running...")
    await asyncio.Event().wait()  # Keep alive

asyncio.run(main())
