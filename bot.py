import os
import json
import time
import asyncio
import aiohttp
import subprocess
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

# === ASYNCIO LOOP ===
loop = asyncio.get_event_loop()

# === PROGRESS BAR ===
def get_progress_bar(current, total, length=20):
    filled = int(length * current / total)
    bar = "█" * filled + "▒" * (length - filled)
    percent = current / total * 100
    return f"{bar} » {percent:.2f}%"

def format_progress(filename, task, current, total, speed, elapsed, eta):
    return (f"Filename : {filename}\n"
            f"Task     : {task}\n"
            f"{get_progress_bar(current, total)}\n"
            f"Done     : {current/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
            f"Speed    : {speed:.2f}MB/s\n"
            f"ETA      : {eta:.0f}s\n"
            f"Elapsed  : {elapsed:.0f}s")

# === DOWNLOAD FUNCTION ===
async def download_file(url, filename, msg: Message):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024*1024
            path = os.path.join(DOWNLOAD_FOLDER, filename)
            start_time = time.time()
            last_text = ""
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(chunk_size):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed / 1024 / 1024
                    eta = (total - downloaded) / (downloaded/elapsed) if downloaded else 0
                    text = format_progress(filename, "Downloading", downloaded, total, speed, elapsed, eta)
                    if text != last_text:
                        last_text = text
                        try:
                            await msg.edit(text)
                        except:
                            pass
            return path

# === ENCODE FUNCTION ===
def encode_video(input_path, output_path, progress_callback=None):
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
    for line in process.stdout:
        if "time=" in line and progress_callback:
            try:
                time_str = line[line.find("time=")+5:line.find(" bitrate")]
                h, m, s = 0, 0, 0
                parts = time_str.split(":")
                if len(parts) == 3:
                    h, m, s = map(float, parts)
                elif len(parts) == 2:
                    m, s = map(float, parts)
                elapsed_sec = h*3600 + m*60 + s
                progress_callback(elapsed_sec)
            except:
                pass
    process.wait()
    return output_path

# === PYROGRAM CLIENT ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

# --- HANDLE VIDEO UPLOAD ---
@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    msg = await message.reply(f"⬇️ Downloading {file_name}...")
    path = await download_file(message.document.file_id if message.document else message.video.file_id, file_name, msg)
    pending_videos[message.id] = path

    # Auto encode after download
    out_file = os.path.join(ENCODED_FOLDER, os.path.basename(path))
    await msg.edit(f"⚙️ Encoding {file_name}...")

    def enc_callback(elapsed):
        try:
            loop.create_task(msg.edit(f"Encoding elapsed: {elapsed:.0f}s\nFile: {file_name}"))
        except:
            pass

    encode_video(path, out_file, progress_callback=enc_callback)
    await msg.edit(f"✅ Finished {file_name}, uploading...")
    await client.send_document(message.chat.id, out_file)
    os.remove(path)
    os.remove(out_file)
    pending_videos.pop(message.id, None)

# --- MANUAL ENCODE COMMAND ---
@app.on_message(filters.command("encode"))
async def encode_command(client, message: Message):
    if message.reply_to_message:
        orig_msg_id = message.reply_to_message.id
        if orig_msg_id not in pending_videos:
            await message.reply("⚠️ File not found, please upload it again.")
            return
        input_path = pending_videos[orig_msg_id]
        output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
        await message.reply(f"⚙️ Encoding {os.path.basename(input_path)}...")

        def enc_callback(elapsed):
            try:
                loop.create_task(message.reply(f"Encoding elapsed: {elapsed:.0f}s"))
            except:
                pass

        encode_video(input_path, output_path, progress_callback=enc_callback)
        await message.reply(f"✅ Done {os.path.basename(input_path)}")
        await client.send_document(message.chat.id, output_path)
        os.remove(input_path)
        os.remove(output_path)
        pending_videos.pop(orig_msg_id, None)
    else:
        await message.reply("Reply to a video/document with /encode to process it.")

# --- SUBSPLEASE AUTO-DOWNLOAD ---
async def fetch_subsplease():
    import feedparser
    try:
        feed = feedparser.parse(SUBSPLEASE_FEED)
        if not feed.entries:
            print("⚠️ SubsPlease feed empty")
            return
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            if link in downloaded_episodes:
                continue
            print(f"⬇️ Auto download: {title} -> {link}")
            filename = f"{title}.mkv"
            msg = await app.send_message(CHAT_ID, f"⬇️ {filename}")
            path = await download_file(link, filename, msg)
            out_file = os.path.join(ENCODED_FOLDER, filename)
            encode_video(path, out_file)
            await app.send_document(CHAT_ID, out_file)
            os.remove(path)
            os.remove(out_file)
            downloaded_episodes.add(link)
            save_tracked()
    except Exception as e:
        print("SubsPlease auto error:", e)

# --- RUN BOT ---
if __name__ == "__main__":
    import threading
    import time
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: loop.create_task(fetch_subsplease()), "interval", minutes=10)
    scheduler.start()

    print("🚀 Bot is running...")
    app.run()
