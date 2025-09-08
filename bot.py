import os
import asyncio
import time
import json
import threading
import subprocess
from pyrogram import Client, filters
from pyrogram.errors import FloodWait

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))
USER_ID = int(os.getenv("USER_ID"))  # Your Telegram user ID
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

# === Queue and cancel system ===
task_queue = []
current_task = None
cancel_flag = False

# === Safe message edit ===
async def safe_edit(msg, text):
    try:
        await msg.edit(text)
    except FloodWait as e:
        await asyncio.sleep(e.x)
        await msg.edit(text)
    except:
        pass

# === Encode function ===
def encode_video(input_path, output_path, progress_callback=None):
    ext = os.path.splitext(input_path)[1]
    output_path = os.path.splitext(output_path)[0] + ext

    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-c:s", "copy",
        "-y", output_path
    ]

    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    for line in process.stdout:
        if progress_callback and ("frame=" in line or "time=" in line):
            progress_callback(line.strip())
    process.wait()
    return output_path

# === Pyrogram client ===
app = Client("anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

pending_videos = {}

# === Auto-download SubsPlease ===
async def auto_mode():
    global cancel_flag
    while True:
        try:
            import requests
            res = requests.get(SUBS_API_URL, timeout=15)
            data = res.json()
            for ep in data.get("data", []):
                title = ep["release_title"]
                url = ep["link"]
                if url in downloaded_episodes:
                    continue
                file_path = os.path.join(DOWNLOAD_FOLDER, title + ".mkv")
                msg = await app.send_message(USER_ID, f"⌑ Task » Downloading: {title}")
                with open(file_path, "wb") as f:
                    f.write(requests.get(url).content)
                downloaded_episodes.add(url)
                save_tracked()
                await start_encode(file_path, msg)
            await asyncio.sleep(600)  # check every 10 minutes
        except Exception as e:
            await asyncio.sleep(60)

# === Start encoding after download ===
async def start_encode(file_path, progress_msg):
    global cancel_flag
    await safe_edit(progress_msg, f"⌑ Task » Encoding: {os.path.basename(file_path)}")
    def callback(line):
        asyncio.create_task(safe_edit(progress_msg, f"⌑ Task » Encoding: {line}"))
    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
    encode_video(file_path, output_file, progress_callback=callback)
    await safe_edit(progress_msg, f"✅ Task Done » {os.path.basename(file_path)}")
    await app.send_document(CHAT_ID, output_file)
    os.remove(file_path)
    os.remove(output_file)

# === Manual encode command ===
@app.on_message(filters.command("encode") & filters.user(USER_ID))
async def encode_cmd(client, message):
    if not message.reply_to_message:
        await message.reply("⚠️ Reply to a video/document to encode it.")
        return
    file_path = await message.reply_to_message.download(file_name=os.path.join(DOWNLOAD_FOLDER, "manual.mkv"))
    progress_msg = await message.reply(f"⌑ Task » Downloading: {os.path.basename(file_path)}")
    await start_encode(file_path, progress_msg)

# === Cancel command ===
@app.on_message(filters.command("cancel") & filters.user(USER_ID))
async def cancel_cmd(client, message):
    global cancel_flag
    cancel_flag = True
    await message.reply("⚠️ All current tasks cancelled.")

# === Run bot ===
if __name__ == "__main__":
    print("Bot is running...")
    loop = asyncio.get_event_loop()
    loop.create_task(auto_mode())
    app.run()
