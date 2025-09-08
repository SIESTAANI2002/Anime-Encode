import os
import json
import time
import asyncio
import subprocess
import feedparser
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
    percent = current / total * 100 if total else 0
    return f"{bar} » {percent:.2f}%"


# === DOWNLOAD FUNCTION ===
async def download_file(client, media, filename, msg: Message):
    path = os.path.join(DOWNLOAD_FOLDER, filename)
    start_time = time.time()

    async for progress in client.download_media(
        media,
        file_name=path,
        progress=lambda d, t: asyncio.create_task(update_progress(msg, filename, d, t, start_time))
    ):
        pass
    return path


async def update_progress(msg, filename, downloaded, total, start_time):
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


# === ENCODE FUNCTION ===
def encode_video(input_path, output_path):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return output_path


# === PYROGRAM CLIENT ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}


@app.on_message(filters.video | filters.document)
async def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    msg = await message.reply(f"⬇️ Downloading {file_name}...")
    path = await download_file(client, message, file_name, msg)
    pending_videos[message.id] = path

    # Auto encode after download
    out_file = os.path.join(ENCODED_FOLDER, os.path.basename(path))
    await msg.edit(f"⚙️ Encoding {file_name}...")
    encode_video(path, out_file)
    await msg.edit(f"✅ Finished {file_name}, uploading...")
    await client.send_document(message.chat.id, out_file)

    os.remove(path)
    os.remove(out_file)
    pending_videos.pop(message.id, None)


@app.on_message(filters.command("encode"))
async def encode_command(client, message: Message):
    if message.reply_to_message:
        orig_id = message.reply_to_message.id
        if orig_id not in pending_videos:
            await message.reply("⚠️ File not found, please upload it again.")
            return
        input_path = pending_videos[orig_id]
        out_file = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
        await message.reply(f"⚙️ Encoding {os.path.basename(input_path)}...")
        encode_video(input_path, out_file)
        await message.reply(f"✅ Done {os.path.basename(input_path)}")
        await client.send_document(message.chat.id, out_file)
        os.remove(input_path)
        os.remove(out_file)
        pending_videos.pop(orig_id, None)
    else:
        await message.reply("Reply to a video/document with /encode to process it.")


# === SUBSPLEASE AUTO-DOWNLOAD ===
async def fetch_subsplease():
    try:
        feed = feedparser.parse(SUBSPLEASE_FEED)
        if not feed.entries:
            print("⚠️ SubsPlease feed empty")
            return
        for entry in feed.entries:
            title = entry.title
            link = entry.link

            # Skip non-http links
            if not link.startswith("http"):
                print(f"⚠️ Skipping non-HTTP link: {link}")
                continue

            if link in downloaded_episodes:
                continue

            print(f"⬇️ Auto download: {title}")
            filename = f"{title}.mkv"
            msg = await app.send_message(CHAT_ID, f"⬇️ {filename}")
            path = await download_file(app, link, filename, msg)
            out_file = os.path.join(ENCODED_FOLDER, filename)
            await msg.edit(f"⚙️ Encoding {filename}...")
            encode_video(path, out_file)
            await app.send_document(CHAT_ID, out_file)
            os.remove(path)
            os.remove(out_file)
            downloaded_episodes.add(link)
            save_tracked()
    except Exception as e:
        print("SubsPlease auto error:", e)


# === RUN BOT ===
async def main():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(fetch_subsplease, "interval", minutes=10)
    scheduler.start()

    await app.start()
    print("🚀 Bot is running...")
    await asyncio.Event().wait()  # Keep running

asyncio.run(main())
