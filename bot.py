import os
import json
import time
import math
import asyncio
import threading
import subprocess
import requests
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Session string login
CHAT_ID = int(os.getenv("CHAT_ID"))           # channel/group id for auto-upload
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

# === PROGRESS BAR HELPER ===
def get_progress_bar(done, total, length=20):
    if total == 0:
        return "[{}]".format("▒"*length)
    filled = int(length * done / total)
    bar = "█"*filled + "▒"*(length-filled)
    percent = (done/total)*100
    return bar, percent

def sizeof_fmt(num, suffix="B"):
    for unit in ["","K","M","G","T","P","E","Z"]:
        if abs(num) < 1024.0:
            return f"{num:3.2f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.2f}Y{suffix}"

def format_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

# === DOWNLOAD FUNCTION WITH PROGRESS ===
async def download_file(client, url, filename, reply_msg):
    output_path = os.path.join(DOWNLOAD_FOLDER, filename)
    response = requests.get(url, stream=True)
    total_length = int(response.headers.get('content-length', 0))
    downloaded = 0
    start_time = time.time()
    last_update = 0

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024*1024):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                now = time.time()
                if now - last_update > 1:  # update every 1 sec
                    bar, percent = get_progress_bar(downloaded, total_length)
                    elapsed = now - start_time
                    speed = downloaded / elapsed
                    eta = (total_length - downloaded) / speed if speed else 0
                    text = f"""Name » {filename}
⌑ Task   » Downloading
⌑ {bar} » {percent:.2f}%
⌑ Done   : {sizeof_fmt(downloaded)} of {sizeof_fmt(total_length)}
⌑ Speed  : {sizeof_fmt(speed)}/s
⌑ ETA    : {format_time(eta)}
⌑ Past   : {format_time(elapsed)}
⌑ ENG    : PyroF v2.2.11
⌑ User   : Ānī
"""
                    try:
                        await client.edit_message_text(reply_msg.chat.id, reply_msg.message_id, text)
                    except:
                        pass
                    last_update = now
    return output_path

# === ENCODE FUNCTION WITH PROGRESS ===
async def encode_video(client, input_path, reply_msg):
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
    cmd = [
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
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    start_time = time.time()
    last_update = 0

    for line in process.stdout:
        if "time=" in line:
            parts = line.strip().split("time=")
            if len(parts) > 1:
                t_str = parts[1].split(" ")[0]
                h, m, s = t_str.split(":")
                elapsed = int(h)*3600 + int(m)*60 + float(s)
                # naive estimate: assume 10min video for bar (can improve)
                total_duration = 600
                bar, percent = get_progress_bar(elapsed, total_duration)
                now = time.time()
                if now - last_update > 1:
                    text = f"""Name » {os.path.basename(input_path)}
⌑ Task   » Encoding
⌑ {bar} » {percent:.2f}%
⌑ Past   : {format_time(elapsed)}
⌑ ENG    : PyroF v2.2.11
⌑ User   : Ānī
"""
                    try:
                        await client.edit_message_text(reply_msg.chat.id, reply_msg.message_id, text)
                    except:
                        pass
                    last_update = now
    process.wait()
    return output_path

# === Pyrogram Client ===
app = Client(
    name="anime_userbot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

pending_videos = {}

@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    reply_msg = await message.reply(f"⚑ Queued » {file_name}")
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    await message.download(file_path)
    pending_videos[message.id] = (file_path, reply_msg)
    await reply_msg.edit_text(f"✅ Download complete » {file_name}\n⌑ Ready for /encode")

@app.on_message(filters.command("encode"))
async def encode_command(client, message: Message):
    if message.reply_to_message:
        orig_msg_id = message.reply_to_message.id
        if orig_msg_id not in pending_videos:
            await message.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
            return
        input_path, reply_msg = pending_videos.pop(orig_msg_id)
        await reply_msg.edit_text(f"⌑ Encoding » {os.path.basename(input_path)}")
        output_file = await encode_video(client, input_path, reply_msg)
        await reply_msg.edit_text(f"✅ Done » {os.path.basename(input_path)}\n⌑ Uploading...")
        await client.send_document(message.chat.id, output_file)
        os.remove(input_path)
        os.remove(output_file)
        await reply_msg.edit_text(f"✅ Task complete » {os.path.basename(input_path)}")

# === Auto mode for SubsPlease every 10 min ===
async def auto_mode():
    while True:
        try:
            res = requests.get(SUBS_API_URL, timeout=15)
            data = res.json()
            for ep in data.get("data", []):
                title = ep["release_title"]
                url = ep["link"]
                if url not in downloaded_episodes:
                    reply_msg = await app.send_message(CHAT_ID, f"⚑ Queued » {title}")
                    file_path = await download_file(app, url, title, reply_msg)
                    downloaded_episodes.add(url)
                    save_tracked()
            await asyncio.sleep(600)  # 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            await asyncio.sleep(60)

# === RUN BOT ===
async def main():
    asyncio.create_task(auto_mode())
    await app.start()
    print("Bot is running...")
    await idle()
    await app.stop()

if __name__ == "__main__":
    from pyrogram import idle
    asyncio.run(main())
