import os
import asyncio
import aiohttp
import aiofiles
import shutil
import time
import math
from datetime import timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pyrogram import Client, filters
from pyrogram.types import Message
from dotenv import load_dotenv
import subprocess

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")

app = Client("my_userbot", api_id=API_ID, api_hash=API_HASH, session_string=SESSION_STRING)

# Global states
job_queue = []
active_job = None
cancel_flag = False
skip_flag = False


def human_readable_size(size):
    # Convert bytes to human-readable
    power = 2**10
    n = 0
    units = ["B", "KB", "MB", "GB", "TB"]
    while size >= power and n < len(units)-1:
        size /= power
        n += 1
    return f"{size:.2f}{units[n]}"


async def progress_bar(current, total, start_time, task, filename):
    percent = current * 100 / total
    elapsed = time.time() - start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0

    bar_length = 14
    filled = math.floor(percent / (100 / bar_length))
    bar = "█" * filled + "▒" * (bar_length - filled)

    return (
        f"**Name »** `{filename}`\n"
        f"**⌑ Task   »** {task}\n"
        f"**⌑ {bar} » {percent:.2f}%**\n"
        f"**⌑ Done   :** {human_readable_size(current)} of {human_readable_size(total)}\n"
        f"**⌑ Speed  :** {human_readable_size(speed)}/s\n"
        f"**⌑ ETA    :** {str(timedelta(seconds=int(eta)))}\n"
        f"**⌑ Past   :** {str(timedelta(seconds=int(elapsed)))}"
    )


async def download_file(url, path, msg: Message, filename):
    global cancel_flag, skip_flag
    cancel_flag = False
    skip_flag = False

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            start = time.time()

            async with aiofiles.open(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 512):  # 512KB
                    if cancel_flag:
                        await msg.edit_text("⚠️ Download cancelled.")
                        return False
                    if skip_flag:
                        await msg.edit_text("⏭️ Download skipped.")
                        return False

                    await f.write(chunk)
                    downloaded += len(chunk)

                    if downloaded % (1024 * 1024) < 512 * 1024:  # update every ~1MB
                        text = await progress_bar(downloaded, total, start, "Downloading", filename)
                        try:
                            await msg.edit_text(text)
                        except:
                            pass

    await msg.edit_text(f"✅ Saved {filename}")
    return True


async def encode_video(input_file, output_file, msg, filename):
    global cancel_flag, skip_flag
    cancel_flag = False
    skip_flag = False

    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-i", input_file, "-c:v", "libx264", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k", output_file,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    start = time.time()
    while True:
        if cancel_flag:
            process.kill()
            await msg.edit_text("⚠️ Encoding cancelled.")
            return False
        if skip_flag:
            process.kill()
            await msg.edit_text("⏭️ Encoding skipped.")
            return False

        line = await process.stderr.readline()
        if not line:
            break
        line = line.decode("utf-8", errors="ignore")
        if "time=" in line:
            try:
                t_str = line.split("time=")[1].split(" ")[0]
                h, m, s = t_str.split(":")
                elapsed_enc = int(float(h)) * 3600 + int(m) * 60 + float(s)
                percent = min(100, (elapsed_enc / 600) * 100)  # fake 10min est
                bar_length = 14
                filled = math.floor(percent / (100 / bar_length))
                bar = "█" * filled + "▒" * (bar_length - filled)
                text = (
                    f"**Name »** `{filename}`\n"
                    f"**⌑ Task   »** Encoding\n"
                    f"**⌑ {bar} » {percent:.2f}%**\n"
                    f"**⌑ Elapsed :** {str(timedelta(seconds=int(time.time()-start)))}"
                )
                try:
                    await msg.edit_text(text)
                except:
                    pass
            except:
                pass

    await process.wait()
    await msg.edit_text(f"✅ Encoding complete: {output_file}")
    return True


async def upload_file(path, msg, filename):
    global cancel_flag, skip_flag
    cancel_flag = False
    skip_flag = False

    file_size = os.path.getsize(path)
    start = time.time()

    async def progress(current, total):
        text = await progress_bar(current, total, start, "Uploading", filename)
        try:
            await msg.edit_text(text)
        except:
            pass

    try:
        await app.send_document(msg.chat.id, path, progress=progress)
        await msg.edit_text(f"✅ Upload complete: {filename}")
    except Exception as e:
        await msg.edit_text(f"⚠️ Upload failed: {e}")


@app.on_message(filters.command("encode") & filters.reply)
async def manual_encode(_, message: Message):
    if not message.reply_to_message or not message.reply_to_message.video:
        await message.reply_text("⚠️ Reply to a video with /encode")
        return

    video = message.reply_to_message.video
    filename = video.file_name or "video.mp4"
    input_path = f"downloads/{filename}"
    output_path = f"encoded_{filename}"

    status_msg = await message.reply_text(f"📥 Starting download: {filename}")
    await app.download_media(video, file_name=input_path)

    # Encoding auto after download
    success = await encode_video(input_path, output_path, status_msg, filename)
    if success:
        await upload_file(output_path, status_msg, filename)
    os.remove(input_path)
    os.remove(output_path)


@app.on_message(filters.command("cancel"))
async def cancel_task(_, message: Message):
    global cancel_flag
    cancel_flag = True
    await message.reply_text("🛑 Cancel requested.")


@app.on_message(filters.command("skip"))
async def skip_task(_, message: Message):
    global skip_flag
    skip_flag = True
    await message.reply_text("⏭️ Skip requested.")


@app.on_message(filters.command("start"))
async def start_cmd(_, message: Message):
    await message.reply_text("✅ Bot is running (Session Mode)")


if __name__ == "__main__":
    print("Bot is running...")
    scheduler = AsyncIOScheduler()
    # Disabled SubsPlease auto fetch since JSON errors
    # scheduler.add_job(fetch_subsplease, "interval", minutes=10)
    scheduler.start()
    app.run()
