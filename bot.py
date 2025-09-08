import os
import asyncio
import shutil
import time
import psutil
from pyrogram import Client, filters
from pyrogram.types import Message

# ===== CONFIG =====
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# ===== BOT =====
app = Client(
    name="anime_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# ===== Helpers =====
def format_progress(current, total, start_time, task_name, filename):
    now = time.time()
    elapsed = now - start_time
    if elapsed == 0:
        speed = 0
    else:
        speed = current / elapsed

    percent = (current / total) * 100 if total else 0
    eta = (total - current) / speed if speed > 0 else 0

    bar_length = 12
    filled = int(bar_length * percent / 100)
    bar = "█" * filled + "▒" * (bar_length - filled)

    return (
        f"📂 File: {os.path.basename(filename)}\n"
        f"⌑ Task   » {task_name}\n"
        f"⌑ {bar} » {percent:.2f}%\n"
        f"⌑ Done   : {current/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
        f"⌑ Speed  : {speed/1024/1024:.2f}MB/s\n"
        f"⌑ ETA    : {int(eta)}s | Past: {int(elapsed)}s\n"
        f"____________________________\n"
        f"FREE: {shutil.disk_usage('/').free/1024/1024/1024:.2f}GB | "
        f"UPTM: {int(time.time() - psutil.boot_time())//3600}h"
    )

async def run_ffmpeg(input_path, output_path, msg, task_name):
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "28",
        "-c:a", "aac", "-b:a", "128k",
        output_path
    ]

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    start_time = time.time()
    while True:
        line = await process.stderr.readline()
        if not line:
            break
        if b"time=" in line:
            try:
                msg = await msg.edit_text(
                    format_progress(1, 1, start_time, task_name, os.path.basename(input_path))
                )
            except Exception:
                pass

    await process.wait()

# ===== Handlers =====
@app.on_message(filters.command("encode") & filters.reply)
async def encode_handler(_, message: Message):
    replied = message.reply_to_message
    if not replied or not (replied.video or replied.document):
        return await message.reply("⚠️ Reply to a video/document to encode.")

    file = replied.video or replied.document
    file_name = file.file_name or "video.mp4"
    download_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    output_path = os.path.join(ENCODED_FOLDER, f"encoded_{file_name}")

    progress = await message.reply(f"⬇️ Starting download: {file_name}")
    start_time = time.time()

    async def progress_callback(current, total):
        try:
            await progress.edit_text(
                format_progress(current, total, start_time, "Downloading", file_name)
            )
        except Exception:
            pass

    await replied.download(file_name=download_path, progress=progress_callback)

    await progress.edit_text(f"✅ Download complete: {file_name}\n⚙️ Starting encode...")

    await run_ffmpeg(download_path, output_path, progress, "Encoding")

    await progress.edit_text(f"📤 Uploading: {os.path.basename(output_path)}")

    start_time = time.time()

    async def upload_callback(current, total):
        try:
            await progress.edit_text(
                format_progress(current, total, start_time, "Uploading", output_path)
            )
        except Exception:
            pass

    await message.reply_document(output_path, progress=upload_callback)

    await progress.edit_text(f"✅ Done: {os.path.basename(output_path)}")

    os.remove(download_path)
    os.remove(output_path)


# ===== START =====
print("Bot is running...")
app.run()
