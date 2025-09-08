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
SESSION_STRING = os.getenv("SESSION_STRING")  # Your Pyrogram session string
CHAT_ID = int(os.getenv("CHAT_ID"))
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

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


# === Progress Utilities ===
def make_progress_bar(percent, length=20):
    filled_len = int(length * percent // 100)
    bar = "█" * filled_len + "▒" * (length - filled_len)
    return bar


async def edit_progress(msg, percent, done_bytes, total_bytes, speed, eta, past, task_name, filename):
    bar = make_progress_bar(percent)
    text = (
        f"Name » {filename}\n"
        f"⌑ Task   » {task_name}\n"
        f"⌑ {bar} » {percent:.2f}%\n"
        f"⌑ Done   : {done_bytes:.2f}MB of {total_bytes:.2f}MB\n"
        f"⌑ Speed  : {speed:.2f}MB/s\n"
        f"⌑ ETA    : {eta}s\n"
        f"⌑ Past   : {past}s\n"
        f"⌑ ENG    : PyroF v2.2.11\n"
        f"⌑ User   : Ānī"
    )
    await msg.edit(text)


# === Download File with Progress ===
async def download_file(session, url, output_path, msg: Message):
    async with session.get(url) as r:
        total = int(r.headers.get("Content-Length", 0))
        chunk_size = 1024 * 1024  # 1MB
        done = 0
        start_time = time.time()
        with open(output_path, "wb") as f:
            async for chunk in r.content.iter_chunked(chunk_size):
                f.write(chunk)
                done += len(chunk)
                percent = done * 100 / total if total else 0
                elapsed = time.time() - start_time
                speed = done / (1024 * 1024 * elapsed) if elapsed > 0 else 0
                eta = (total - done) / (1024 * 1024 * speed) if speed > 0 else 0
                await edit_progress(msg, percent, done / (1024 * 1024), total / (1024 * 1024), speed, int(eta), int(elapsed), "Downloading", os.path.basename(output_path))
    return output_path


# === Encode Video with Progress ===
async def encode_video(input_path, output_path, msg: Message):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

    # Prepare ffmpeg command
    command = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-c:s", "copy",
        output_path
    ]

    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )

    start_time = time.time()
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        line = line.decode("utf-8").strip()
        # Parse time= for progress
        if "time=" in line:
            time_str = line.split("time=")[1].split(" ")[0]
            h, m, s = [float(x) for x in time_str.split(":")]
            elapsed = h * 3600 + m * 60 + s
            # Rough estimate: percent = elapsed / total (we approximate total duration)
            percent = min(elapsed / 420 * 100, 100)  # assuming 7 min per episode ~420s
            await edit_progress(msg, percent, 0, 0, 0, 0, int(time.time() - start_time), "Encoding", os.path.basename(input_path))
    await process.wait()
    return output_path


# === Pyrogram Client ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}


# === Manual Encode Command ===
@app.on_message(filters.command("encode") & filters.private)
async def manual_encode(client, message: Message):
    if not message.reply_to_message:
        await message.reply("⚠️ Reply to a video/document with /encode")
        return

    media = message.reply_to_message.document or message.reply_to_message.video
    if not media:
        await message.reply("⚠️ Reply to a video/document")
        return

    filename = media.file_name if media else "video.mp4"
    temp_msg = await message.reply(f"⌑ Downloading » {filename}")
    headers = {"User-Agent": "Mozilla/5.0"}

    async with aiohttp.ClientSession(headers=headers) as session:
        file_path = os.path.join(DOWNLOAD_FOLDER, filename)
        await download_file(session, media.file_id, file_path, temp_msg)

    output_path = os.path.join(ENCODED_FOLDER, filename)
    await encode_video(file_path, output_path, temp_msg)
    await client.send_document(message.chat.id, output_path)
    os.remove(file_path)
    os.remove(output_path)
    await temp_msg.edit(f"✅ Finished » {filename}")


# === Auto-download Task ===
async def auto_download_task(client: Client):
    headers = {"User-Agent": "Mozilla/5.0"}
    async with aiohttp.ClientSession(headers=headers) as session:
        while True:
            try:
                async with session.get(SUBS_API_URL) as resp:
                    if "application/json" not in resp.headers.get("Content-Type", ""):
                        print("SubsPlease returned non-JSON content, retrying in 60s")
                        await asyncio.sleep(60)
                        continue
                    res = await resp.json()
                    for ep in res.get("data", []):
                        title = ep["release_title"]
                        link = ep["link"]
                        if link not in downloaded_episodes:
                            temp_msg = await client.send_message(CHAT_ID, f"⬇️ Downloading {title}")
                            file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(link)[1])
                            await download_file(session, link, file_path, temp_msg)
                            output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                            await encode_video(file_path, output_file, temp_msg)
                            await client.send_document(CHAT_ID, output_file)
                            os.remove(file_path)
                            os.remove(output_file)
                            downloaded_episodes.add(link)
                            save_tracked()
                            await temp_msg.edit(f"✅ Done {title}")
                await asyncio.sleep(600)  # 10 min interval
            except Exception as e:
                print("Auto download error:", e)
                await asyncio.sleep(60)


# === Run Bot ===
async def main():
    async with app:
        asyncio.create_task(auto_download_task(app))
        print("Bot is running...")
        await idle()


if __name__ == "__main__":
    from pyrogram import idle
    asyncio.run(main())
