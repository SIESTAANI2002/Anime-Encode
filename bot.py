import os
import json
import time
import math
import asyncio
import aiohttp
import subprocess
from pyrogram import Client, filters
from pyrogram.types import Message
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import feedparser

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))

DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBSPLEASE_FEED = "https://subsplease.org/rss/?r=1080"

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

# === TASK QUEUE ===
queue = asyncio.Queue()
current_task = None
cancel_flag = False
skip_flag = False

# === PROGRESS BAR ===
def get_progress_bar(current, total, length=20):
    if total == 0: return "▒" * length + " » 0.00%"
    filled = int(length * current / total)
    bar = "█" * filled + "▒" * (length - filled)
    percent = current / total * 100
    return f"{bar} » {percent:.2f}%"

async def download_file(url, filename, msg: Message):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1 MB
            path = os.path.join(DOWNLOAD_FOLDER, filename)
            start_time = time.time()
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(chunk_size):
                    if cancel_flag or skip_flag:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed / 1024 / 1024
                    eta = (total - downloaded) / (downloaded / elapsed) if downloaded else 0
                    bar = get_progress_bar(downloaded, total)
                    text = (f"Filename : {filename}\n"
                            f"Task     : Downloading\n"
                            f"{bar}\n"
                            f"Done     : {downloaded/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
                            f"Speed    : {speed:.2f}MB/s\n"
                            f"ETA      : {eta:.0f}s\n"
                            f"Elapsed  : {elapsed:.0f}s")
                    try: await msg.edit(text)
                    except: pass
            return path

def encode_video(input_path, output_path, msg: Message):
    global cancel_flag, skip_flag
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start_time = time.time()
    for line in process.stdout:
        if cancel_flag or skip_flag:
            process.kill()
            break
        if "time=" in line:
            try:
                time_str = line.split("time=")[1].split(" ")[0]
                parts = time_str.split(":")
                h, m, s = 0,0,0
                if len(parts)==3:
                    h,m,s = map(float, parts)
                elif len(parts)==2:
                    m,s = map(float, parts)
                elapsed_sec = h*3600 + m*60 + s
                bar = get_progress_bar(elapsed_sec, elapsed_sec+1)  # approximate
                text = (f"Filename : {os.path.basename(input_path)}\n"
                        f"Task     : Encoding\n"
                        f"{bar}\n"
                        f"Elapsed  : {elapsed_sec:.0f}s")
                asyncio.run_coroutine_threadsafe(msg.edit(text), asyncio.get_event_loop())
            except: pass
    return output_path

app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

# === MANUAL /encode ===
@app.on_message(filters.command("encode") & filters.private)
async def encode_command(client, message: Message):
    if not message.reply_to_message:
        await message.reply("⚠️ Reply to a video/document with /encode to process.")
        return
    file_msg = message.reply_to_message
    file_name = file_msg.document.file_name if file_msg.document else file_msg.video.file_name
    await message.reply(f"✅ Added to queue: {file_name}")
    await queue.put((file_msg, message.chat.id))

# === CANCEL/SKIP ===
@app.on_message(filters.command("cancel") & filters.private)
async def cancel_cmd(client, message: Message):
    global cancel_flag
    cancel_flag = True
    await message.reply("⚠️ Current task cancelled!")

@app.on_message(filters.command("skip") & filters.private)
async def skip_cmd(client, message: Message):
    global skip_flag
    skip_flag = True
    await message.reply("⏭ Skipping current task...")

# === PROCESS QUEUE ===
async def worker():
    global current_task, cancel_flag, skip_flag
    while True:
        file_msg, chat_id = await queue.get()
        cancel_flag = False
        skip_flag = False
        file_name = file_msg.document.file_name if file_msg.document else file_msg.video.file_name
        msg = await app.send_message(chat_id, f"⬇️ Downloading {file_name}...")
        path = await download_file(file_msg.document.file_id if file_msg.document else file_msg.video.file_id, file_name, msg)
        if cancel_flag or skip_flag:
            await msg.edit("⚠️ Task cancelled/skipped!")
            queue.task_done()
            continue
        out_file = os.path.join(ENCODED_FOLDER, os.path.basename(path))
        await msg.edit(f"⚙️ Encoding {file_name}...")
        encode_video(path, out_file, msg)
        if cancel_flag or skip_flag:
            await msg.edit("⚠️ Task cancelled/skipped!")
            queue.task_done()
            continue
        await msg.edit(f"✅ Uploading {file_name}...")
        await app.send_document(chat_id, out_file)
        os.remove(path)
        os.remove(out_file)
        queue.task_done()

# === SUBSPLEASE AUTO-DOWNLOAD ===
async def fetch_subsplease():
    try:
        feed = feedparser.parse(SUBSPLEASE_FEED)
        if not feed.entries: return
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            if link in downloaded_episodes:
                continue
            filename = f"{title}.mkv"
            msg = await app.send_message(CHAT_ID, f"⬇️ Auto download: {filename}")
            path = await download_file(link, filename, msg)
            out_file = os.path.join(ENCODED_FOLDER, filename)
            encode_video(path, out_file, msg)
            await app.send_document(CHAT_ID, out_file)
            os.remove(path)
            os.remove(out_file)
            downloaded_episodes.add(link)
            save_tracked()
    except Exception as e:
        print("SubsPlease auto error:", e)

# === MAIN ===
async def main():
    scheduler = AsyncIOScheduler()
    scheduler.add_job(fetch_subsplease, "interval", minutes=10)
    scheduler.start()
    await app.start()
    print("🚀 Bot is running...")
    asyncio.create_task(worker())
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
