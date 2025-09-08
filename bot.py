import os
import time
import asyncio
import aiohttp
import aiofiles
import signal
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# ================= CONFIG =================
API_ID = int(os.environ.get("API_ID", ""))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", ""))
DOWNLOAD_DIR = "./downloads"
ENCODE_DIR = "./encoded"

# Pyrogram Client
app = Client("anime_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ================= HELPERS =================
pending_tasks = {}
cancel_flag = False

async def safe_edit(msg: Message, text: str):
    """Edit message safely with floodwait handling"""
    try:
        await msg.edit_text(text)
    except Exception as e:
        if "FloodWait" in str(e):
            wait_time = int(str(e).split()[-1].replace("s", ""))
            await asyncio.sleep(wait_time + 1)
            await safe_edit(msg, text)
        else:
            print(f"safe_edit error: {e}")

def format_progress(task: str, filename: str, percent: float, elapsed: int, eta: int):
    return (
        f"Name » {filename}\n"
        f"⌑ Task   » {task}\n"
        f"⌑ {percent:.2f}%\n"
        f"⌑ Finished : {elapsed}s | ETA: {eta}s"
    )

async def run_progress(task: str, filename: str, total: int, progress_msg: Message):
    """Dummy progress loop for demo (replace with real download/encode/upload loops)"""
    start = time.time()
    last_update = 0
    for i in range(0, total + 1, 5):
        if cancel_flag:
            await safe_edit(progress_msg, f"❌ {task} cancelled for {filename}")
            return
        now = time.time()
        elapsed = int(now - start)
        eta = max(0, int((total - i) / 5))
        if now - last_update >= 20 or i == total:  # update every 20s or at end
            percent = (i / total) * 100
            msg_text = format_progress(task, filename, percent, elapsed, eta)
            await safe_edit(progress_msg, msg_text)
            last_update = now
        await asyncio.sleep(1)  # simulate work
    await safe_edit(progress_msg, f"✅ {task} complete for {filename}")

# ================= BOT COMMANDS =================
@app.on_message(filters.command("start") & filters.user(OWNER_ID))
async def start_cmd(client, message: Message):
    await message.reply_text("✅ Bot is running and ready!")

@app.on_message(filters.command("download") & filters.user(OWNER_ID))
async def download_cmd(client, message: Message):
    filename = "sample_anime.mkv"
    progress_msg = await message.reply_text(f"📥 Starting download: {filename}")
    pending_tasks[progress_msg.id] = filename
    await run_progress("Downloading", filename, 100, progress_msg)
    await run_progress("Encoding", filename, 100, progress_msg)
    await run_progress("Uploading", filename, 100, progress_msg)

@app.on_message(filters.command("cancel") & filters.user(OWNER_ID))
async def cancel_cmd(client, message: Message):
    global cancel_flag
    cancel_flag = True
    await message.reply_text("⚠️ All running tasks cancelled.")

# ================= MAIN =================
def handle_sigterm(*_):
    loop = asyncio.get_event_loop()
    loop.create_task(app.stop())

signal.signal(signal.SIGTERM, handle_sigterm)

if __name__ == "__main__":
    print("Bot is running...")
    app.run()
