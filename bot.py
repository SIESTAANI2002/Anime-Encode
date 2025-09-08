import os
import asyncio
import aiofiles
import aiohttp
import math
import shutil
import time
import logging
import psutil
from pyrogram import Client, filters
from pyrogram.types import Message
from datetime import timedelta
from dotenv import load_dotenv
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import subprocess

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))

logging.basicConfig(level=logging.INFO)

app = Client(
    name="anime_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

# -------- Progress Bar --------
def progress_bar(current, total, length=20):
    filled = int(length * current // total)
    bar = "█" * filled + "▒" * (length - filled)
    percent = (current / total) * 100
    return f"{bar} {percent:.2f}%"

def format_time(seconds: float):
    return str(timedelta(seconds=int(seconds)))

# -------- Video Encoding --------
async def encode_video(file_path, output_path, update_cb=None):
    total_time = None
    start = time.time()
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", file_path, "-c:v", "libx265", "-crf", "28", output_path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )

    async for line in process.stderr:
        line = line.decode("utf-8", errors="ignore").strip()
        if "Duration" in line:
            t_str = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = t_str.split(":")
            total_time = int(float(h)) * 3600 + int(m) * 60 + float(s)
        if "time=" in line and total_time:
            t_str = line.split("time=")[1].split(" ")[0]
            try:
                if ":" in t_str:  # format hh:mm:ss.xx
                    h, m, s = t_str.split(":")
                    cur_time = int(float(h)) * 3600 + int(m) * 60 + float(s)
                else:  # format seconds.xx
                    cur_time = float(t_str)
            except Exception:
                continue

            percent = (cur_time / total_time) * 100
            if update_cb:
                await update_cb(cur_time, total_time, start)

    await process.wait()
    return process.returncode == 0

# -------- Progress Update --------
async def progress_message(msg: Message, task: str, current, total, start, filename):
    elapsed = time.time() - start
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0
    bar = progress_bar(current, total)

    text = (
        f"📂 **Name:** {os.path.basename(filename)}\n"
        f"⌑ **Task:** {task}\n"
        f"⌑ {bar}\n"
        f"⌑ **Done:** {current/1024/1024:.2f}MB / {total/1024/1024:.2f}MB\n"
        f"⌑ **Speed:** {speed/1024/1024:.2f}MB/s\n"
        f"⌑ **ETA:** {format_time(eta)}\n"
        f"⌑ **Elapsed:** {format_time(elapsed)}"
    )

    try:
        await msg.edit_text(text)
    except Exception:
        pass

# -------- Encode Command --------
@app.on_message(filters.command("encode") & filters.reply)
async def manual_encode(_, message: Message):
    if not message.reply_to_message or not message.reply_to_message.video:
        return await message.reply("⚠️ Reply to a video with /encode")

    video = message.reply_to_message.video
    filename = video.file_name or "video.mp4"
    input_path = os.path.join("/tmp", filename)
    output_path = os.path.join("/tmp", f"encoded_{filename}")

    status = await message.reply("📥 Starting download...")

    async def dl_progress(current, total):
        await progress_message(status, "Downloading", current, total, start, filename)

    start = time.time()
    await app.download_media(video, file_name=input_path, progress=dl_progress)

    await status.edit_text(f"✅ Download complete: {filename}\n⚙️ Starting encode...")

    async def enc_update(current, total, start_time):
        await progress_message(status, "Encoding", current, total, start_time, filename)

    ok = await encode_video(input_path, output_path, update_cb=enc_update)
    if not ok:
        return await status.edit_text("❌ Encoding failed.")

    await status.edit_text("📤 Uploading...")
    await app.send_document(
        chat_id=message.chat.id,
        document=output_path,
        file_name=f"encoded_{filename}"
    )
    await status.edit_text("✅ Process complete!")

    os.remove(input_path)
    os.remove(output_path)

# -------- Start --------
if __name__ == "__main__":
    logging.info("Bot is running...")
    app.run()
