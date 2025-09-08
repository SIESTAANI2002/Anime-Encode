import os
import json
import time
import math
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
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === PROGRESS BAR ===
def get_progress_bar(current, total, length=20):
    filled = int(length * current / total)
    bar = "█" * filled + "▒" * (length - filled)
    percent = current / total * 100
    return f"{bar} » {percent:.2f}%"

# === DOWNLOAD ===
async def download_file(url, output_path, msg: Message):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024
            start_time = time.time()
            with open(output_path, "wb") as f:
                async for chunk in r.content.iter_chunked(chunk_size):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed / 1024 / 1024
                    eta = (total - downloaded) / (downloaded / elapsed) if downloaded else 0
                    text = (
                        f"Filename : {os.path.basename(output_path)}\n"
                        f"Downloading: {get_progress_bar(downloaded, total)}\n"
                        f"Done   : {downloaded/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
                        f"Speed  : {speed:.2f}MB/s\n"
                        f"ETA    : {int(eta)}s\n"
                        f"Elapsed: {int(elapsed)}s"
                    )
                    try:
                        await msg.edit(text)
                    except:
                        pass
            return output_path

# === ENCODE ===
async def encode_video(input_path, output_path, msg: Message):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        text=True
    )
    async for line in process.stdout:
        if "time=" in line:
            try:
                await msg.edit(f"⚙️ Encoding {os.path.basename(input_path)}\n{line.strip()}")
            except:
                pass
    await process.wait()
    return output_path

# === PYROGRAM CLIENT ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

# === VIDEO HANDLER ===
@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    msg = await message.reply(f"⬇️ Downloading {file_name}...")
    path = os.path.join(DOWNLOAD_FOLDER, file_name)

    # Download
    await download_file(message.document.file_id if message.document else message.video.file_id, path, msg)
    pending_videos[message.id] = path

    # Auto encode
    out_file = os.path.join(ENCODED_FOLDER, os.path.basename(path))
    await msg.edit(f"⚙️ Auto encoding {file_name}...")
    await encode_video(path, out_file, msg)

    await msg.edit(f"✅ Finished {file_name}, uploading...")
    await client.send_document(message.chat.id, out_file)

    os.remove(path)
    os.remove(out_file)
    pending_videos.pop(message.id, None)

# === MANUAL ENCODE ===
@app.on_message(filters.command("encode") & filters.reply)
async def encode_command(client, message: Message):
    orig_msg_id = message.reply_to_message.id
    if orig_msg_id not in pending_videos:
        await message.reply("⚠️ File not found, please upload it again.")
        return
    input_path = pending_videos[orig_msg_id]
    out_file = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
    msg = await message.reply(f"⚙️ Encoding {os.path.basename(input_path)}...")

    await encode_video(input_path, out_file, msg)
    await msg.edit(f"✅ Encoding finished: {os.path.basename(input_path)}")
    await client.send_document(message.chat.id, out_file)

    os.remove(input_path)
    os.remove(out_file)
    pending_videos.pop(orig_msg_id, None)

# === AUTO DOWNLOAD ===
async def auto_mode():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(SUBS_API_URL) as r:
                    try:
                        data = await r.json()
                    except:
                        print("⚠️ SubsPlease returned non-JSON, retrying in 60s")
                        await asyncio.sleep(60)
                        continue
            for ep in data.get("data", []):
                title = ep["release_title"]
                url = ep["link"]
                if url in downloaded_episodes:
                    continue
                msg = await app.send_message(CHAT_ID, f"⬇️ Auto downloading {title}")
                file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                await download_file(url, file_path, msg)
                out_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                await encode_video(file_path, out_file, msg)
                await app.send_document(CHAT_ID, out_file)
                os.remove(file_path)
                os.remove(out_file)
                downloaded_episodes.add(url)
                save_tracked()
            await asyncio.sleep(600)
        except Exception as e:
            print("Auto mode error:", e)
            await asyncio.sleep(60)

# === RUN BOT ===
async def main():
    await app.start()
    print("🚀 Bot is running...")
    asyncio.create_task(auto_mode())
    await asyncio.Event().wait()

asyncio.run(main())
