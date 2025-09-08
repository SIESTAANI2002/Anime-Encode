import os
import json
import asyncio
import aiohttp
import time
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
SUBSPLEASE_FEED = "https://subsplease.org/rss/?r=1080"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# Track downloaded episodes
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
async def download_file(url, filename, msg: Message):
    path = os.path.join(DOWNLOAD_FOLDER, filename)
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as r:
            total = int(r.headers.get("Content-Length", 0))
            downloaded = 0
            chunk_size = 1024 * 1024  # 1 MB
            start_time = time.time()
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(chunk_size):
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed / 1024 / 1024
                    eta = (total - downloaded) / (downloaded / elapsed) if downloaded else 0
                    bar = get_progress_bar(downloaded, total)
                    text = (f"Filename : {filename}\n"
                            f"Downloading: {bar}\n"
                            f"Done   : {downloaded / 1024 / 1024:.2f}MB of {total / 1024 / 1024:.2f}MB\n"
                            f"Speed  : {speed:.2f}MB/s\n"
                            f"ETA    : {eta:.0f}s\n"
                            f"Elapsed: {elapsed:.0f}s")
                    try:
                        await msg.edit(text)
                    except:
                        pass
    return path

# === ENCODE ===
async def encode_video(input_path, output_path, msg: Message):
    import shlex
    import asyncio
    cmd = f'ffmpeg -i "{input_path}" -vf scale=-1:720 -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -y "{output_path}"'
    process = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    while True:
        line = await process.stdout.readline()
        if not line:
            break
        line = line.decode().strip()
        if "time=" in line:
            try:
                await msg.edit(f"⚙️ Encoding {os.path.basename(input_path)}\n{line}")
            except:
                pass
    await process.wait()
    return output_path

# === PYROGRAM CLIENT ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}

@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    msg = await message.reply(f"⬇️ Downloading {file_name}...")
    path = await message.download(file_name=os.path.join(DOWNLOAD_FOLDER, file_name))
    pending_videos[message.id] = path

    # Auto encode
    out_file = os.path.join(ENCODED_FOLDER, os.path.basename(path))
    await msg.edit(f"⚙️ Encoding {file_name}...")
    await encode_video(path, out_file, msg)
    await msg.edit(f"✅ Finished {file_name}, uploading...")
    await client.send_document(message.chat.id, out_file)
    os.remove(path)
    os.remove(out_file)
    pending_videos.pop(message.id, None)

@app.on_message(filters.command("encode"))
async def manual_encode(client, message: Message):
    if message.reply_to_message:
        orig_msg_id = message.reply_to_message.id
        if orig_msg_id not in pending_videos:
            await message.reply("⚠️ File not found, upload again.")
            return
        path = pending_videos[orig_msg_id]
        out_file = os.path.join(ENCODED_FOLDER, os.path.basename(path))
        msg = await message.reply(f"⚙️ Encoding {os.path.basename(path)}...")
        await encode_video(path, out_file, msg)
        await msg.edit(f"✅ Done {os.path.basename(path)}")
        await client.send_document(message.chat.id, out_file)
        os.remove(path)
        os.remove(out_file)
        pending_videos.pop(orig_msg_id, None)
    else:
        await message.reply("Reply to a video/document with /encode to process it.")

# === AUTO-DOWNLOAD FROM SUBSPLEASE ===
import feedparser
async def fetch_subsplease():
    try:
        feed = feedparser.parse(SUBSPLEASE_FEED)
        for entry in feed.entries:
            title = entry.title
            link = entry.link
            if link in downloaded_episodes:
                continue
            msg = await app.send_message(CHAT_ID, f"⬇️ Auto downloading {title}")
            path = await download_file(link, f"{title}.mkv", msg)
            out_file = os.path.join(ENCODED_FOLDER, f"{title}.mkv")
            await encode_video(path, out_file, msg)
            await app.send_document(CHAT_ID, out_file)
            os.remove(path)
            os.remove(out_file)
            downloaded_episodes.add(link)
            save_tracked()
    except Exception as e:
        print("SubsPlease auto error:", e)

async def scheduler_loop():
    while True:
        await fetch_subsplease()
        await asyncio.sleep(600)  # 10 minutes

# === RUN BOT ===
async def main():
    await app.start()
    print("🚀 Bot is running...")
    asyncio.create_task(scheduler_loop())
    await asyncio.Event().wait()  # Keep running

asyncio.run(main())
