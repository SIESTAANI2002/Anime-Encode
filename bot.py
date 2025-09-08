import os
import json
import asyncio
import aiohttp
import aiofiles
import subprocess
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Use your Pyrogram session string
CHAT_ID = int(os.getenv("CHAT_ID"))
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"
ENCODE_CHUNK = 1024*1024  # 1 MB per encode chunk for progress

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

# === TASK QUEUE ===
task_queue = asyncio.Queue()
current_task = None

# === Pyrogram client ===
app = Client(
    name="anime_bot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH,
)

# === UTILITIES ===
async def download_file(url, output_path, msg: Message):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            bar_length = 20

            async with aiofiles.open(output_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(ENCODE_CHUNK):
                    await f.write(chunk)
                    downloaded += len(chunk)
                    percent = downloaded / total * 100 if total else 0
                    filled = int(bar_length * percent / 100)
                    bar = "█"*filled + "▒"*(bar_length - filled)
                    await msg.edit(f"""
Name » {os.path.basename(output_path)}
⌑ Task   » Downloading
⌑ {bar} » {percent:.2f}%
⌑ Done   : {downloaded/1024/1024:.2f}MB of {total/1024/1024:.2f}MB
⌑ Speed  : TBD
⌑ ETA    : TBD
⌑ Past   : TBD
""")
    return output_path

async def encode_video(input_path, output_path, msg: Message):
    command = [
        "ffmpeg",
        "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-y",
        output_path
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        text=True
    )

    while True:
        line = await process.stderr.readline()
        if not line:
            break
        if "frame=" in line or "time=" in line:
            # Simple progress line, can be enhanced
            await msg.edit(f"⌑ Encoding » {os.path.basename(input_path)}\n{line.strip()}")
    await process.wait()
    return output_path

# === BOT COMMANDS ===
@app.on_message(filters.command("encode") & filters.private)
async def manual_encode(client, message: Message):
    if not message.reply_to_message:
        await message.reply("⚠️ Reply to a video/document with /encode")
        return
    await task_queue.put(("manual", message))
    await message.reply("⏳ Added to encode queue")

@app.on_message(filters.command("cancel") & filters.private)
async def cancel_task(client, message: Message):
    global task_queue
    task_queue = asyncio.Queue()  # Clear queue
    await message.reply("✅ All tasks cancelled")

@app.on_message(filters.command("skip") & filters.private)
async def skip_task(client, message: Message):
    global current_task
    if current_task:
        current_task.cancel()
        await message.reply("⏩ Current task skipped")
    else:
        await message.reply("⚠️ No task running")

# === AUTO MODE ===
async def auto_mode():
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(SUBS_API_URL) as resp:
                    data = await resp.json()
                    for ep in data.get("data", []):
                        title, url = ep["release_title"], ep["link"]
                        if url not in downloaded_episodes:
                            msg = await app.send_message(CHAT_ID, f"⬇️ Downloading {title}")
                            file_path = os.path.join(DOWNLOAD_FOLDER, title + ".mkv")
                            await download_file(url, file_path, msg)
                            output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                            await encode_video(file_path, output_file, msg)
                            await app.send_document(CHAT_ID, output_file)
                            os.remove(file_path)
                            os.remove(output_file)
                            downloaded_episodes.add(url)
                            save_tracked()
            await asyncio.sleep(600)  # 10 min
        except Exception as e:
            print("Auto mode error:", e)
            await asyncio.sleep(60)

# === WORKER ===
async def worker():
    global current_task
    while True:
        task_type, message = await task_queue.get()
        current_task = asyncio.create_task(process_task(task_type, message))
        try:
            await current_task
        except asyncio.CancelledError:
            await message.reply("⏹ Task cancelled")
        current_task = None
        task_queue.task_done()

async def process_task(task_type, message: Message):
    if task_type == "manual":
        replied = message.reply_to_message
        file_path = await replied.download(file_name=os.path.join(DOWNLOAD_FOLDER, replied.document.file_name if replied.document else replied.video.file_name))
        output_path = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
        msg = await message.reply(f"⚙️ Download complete, starting encode for {os.path.basename(file_path)}")
        await encode_video(file_path, output_path, msg)
        await app.send_document(message.chat.id, output_path)
        os.remove(file_path)
        os.remove(output_path)
        await msg.edit(f"✅ Done {os.path.basename(file_path)}")

# === MAIN ===
async def main():
    await app.start()
    print("Bot is running...")
    asyncio.create_task(auto_mode())
    asyncio.create_task(worker())
    while True:
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())
