import os
import json
import time
import asyncio
import subprocess
import psutil
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import Message

# ================= CONFIG =================
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")  # Pyrogram string session
CHAT_ID = int(os.getenv("CHAT_ID"))           # Target channel/group for auto upload

DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"

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

# ================= BOT =================
app = Client(
    "anime_bot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

task_queue = asyncio.Queue()
current_task = None
cancel_flag = False

# ================= PROGRESS HELPERS =================
def format_bytes(size):
    for unit in ['B','KB','MB','GB','TB']:
        if size < 1024:
            return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}PB"

def get_disk_info():
    usage = psutil.disk_usage("/")
    free = format_bytes(usage.free)
    total = format_bytes(usage.total)
    return free, total

def get_uptime(start_time):
    return str(datetime.utcnow() - start_time).split('.')[0]

# Fancy progress bar text
def make_progress_bar(done, total, length=20):
    if total == 0:
        percent = 0
    else:
        percent = done / total
    filled = int(length * percent)
    bar = '█' * filled + '▒' * (length - filled)
    return f"{bar} » {percent*100:.2f}%"

# ================= TASK WORKER =================
async def worker():
    global current_task, cancel_flag
    while True:
        task = await task_queue.get()
        current_task = task
        cancel_flag = False
        task_type, file_path, message = task

        if task_type == "manual":
            await handle_manual_encode(file_path, message)

        elif task_type == "auto":
            await handle_auto_download(file_path)

        current_task = None
        task_queue.task_done()

# ================= MANUAL ENCODE =================
async def handle_manual_encode(file_path, message: Message):
    global cancel_flag
    filename = os.path.basename(file_path)
    out_file = os.path.join(ENCODED_FOLDER, filename)
    
    await message.reply(f"⌑ Task   » Encoding\n⌑ Name   » {filename}")

    total_size = os.path.getsize(file_path)
    chunk_size = 1024 * 1024  # 1MB

    process = subprocess.Popen(
        ["ffmpeg","-i", file_path,"-vf","scale=-1:720","-c:v","libx264","-preset","fast","-crf","23","-c:a","aac","-b:a","128k", out_file, "-y"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    for line in process.stdout:
        if cancel_flag:
            process.kill()
            await message.reply("⚠️ Task cancelled!")
            return
        if "time=" in line:
            # just a dummy example, replace with proper parsing
            await message.edit(f"⌑ Encoding » {filename}\n⌑ {line.strip()}")
            await asyncio.sleep(0.5)

    await message.reply(f"✅ Encoding done: {filename}")
    await app.send_document(message.chat.id, out_file)
    os.remove(file_path)
    os.remove(out_file)

# ================= AUTO DOWNLOAD =================
async def handle_auto_download(url):
    # Implement your auto download and encode here, similar to manual
    pass

# ================= COMMANDS =================
@app.on_message(filters.command("encode"))
async def cmd_encode(client, message: Message):
    if message.reply_to_message and (message.reply_to_message.document or message.reply_to_message.video):
        file_path = await message.reply_to_message.download(DOWNLOAD_FOLDER)
        await task_queue.put(("manual", file_path, message))
        await message.reply(f"✅ Task added to queue: {os.path.basename(file_path)}")
    else:
        await message.reply("⚠️ Reply to a video or document to encode.")

@app.on_message(filters.command("cancel"))
async def cmd_cancel(client, message: Message):
    global cancel_flag
    if current_task:
        cancel_flag = True
        await message.reply("⚠️ Current task will be cancelled.")
    else:
        await message.reply("⚠️ No task running now.")

@app.on_message(filters.command("skip"))
async def cmd_skip(client, message: Message):
    if not task_queue.empty():
        skipped = await task_queue.get()
        await message.reply(f"⚠️ Skipped: {os.path.basename(skipped[1])}")
        task_queue.task_done()
    else:
        await message.reply("⚠️ No queued tasks.")

# ================= MAIN =================
async def main():
    start_time = datetime.utcnow()
    await app.start()
    print("Bot is running...")

    # Start the worker
    asyncio.create_task(worker())

    # Keep bot running
    await app.idle()

if __name__ == "__main__":
    asyncio.get_event_loop().run_until_complete(main())
