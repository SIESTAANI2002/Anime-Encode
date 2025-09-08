import os
import json
import time
import math
import asyncio
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))  # Your Telegram channel/group ID
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


# === Progress helper ===
def progress_bar(current, total, length=20):
    percent = current / total
    done = int(length * percent)
    bar = "█" * done + "▒" * (length - done)
    return bar, percent * 100


# === Download with progress ===
async def download_file(session: aiohttp.ClientSession, url: str, file_path: str, message: Message):
    async with session.get(url) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        start_time = time.time()
        with open(file_path, "wb") as f:
            async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1 MB chunks
                f.write(chunk)
                downloaded += len(chunk)
                bar, percent = progress_bar(downloaded, total)
                elapsed = time.time() - start_time
                speed = downloaded / elapsed if elapsed > 0 else 0
                eta = (total - downloaded) / speed if speed > 0 else 0
                text = (
                    f"Name » {os.path.basename(file_path)}\n"
                    f"⌑ Task   » Downloading\n"
                    f"⌑ {bar} » {percent:.2f}%\n"
                    f"⌑ Done   : {downloaded / (1024*1024):.2f}MB of {total / (1024*1024):.2f}MB\n"
                    f"⌑ Speed  : {speed / (1024*1024):.2f} MB/s\n"
                    f"⌑ ETA    : {int(eta)}s\n"
                    f"⌑ Past   : {int(elapsed)}s"
                )
                try:
                    await message.edit(text)
                except:
                    pass
        return file_path


# === Encode Function with progress ===
async def encode_video(input_path: str, output_path: str, message: Message):
    import subprocess
    import shlex

    # ffmpeg command
    cmd = f'ffmpeg -i "{input_path}" -vf scale=-1:720 -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -y "{output_path}"'
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )

    async for line in process.stdout:
        line = line.decode()
        if "frame=" in line or "time=" in line:
            try:
                await message.edit(f"⌑ Encoding » {os.path.basename(input_path)}\n{line.strip()}")
            except:
                pass

    await process.wait()
    return output_path


# === SubsPlease Auto Download ===
async def auto_download_task(client: Client):
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(SUBS_API_URL) as resp:
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


# === Pyrogram Client ===
app = Client(
    "anime_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

pending_videos = {}  # track manual uploads


@app.on_message(filters.video | filters.document)
async def handle_video(client: Client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    msg = await message.reply(f"⬇️ Downloading {file_name}...")
    await message.download(file_path)
    pending_videos[message.id] = file_path
    await msg.edit(f"✅ Saved {file_name}. Reply to this message with /encode to process.")


@app.on_message(filters.command("encode"))
async def encode_command(client: Client, message: Message):
    if message.reply_to_message:
        orig_msg_id = message.reply_to_message.id
        if orig_msg_id not in pending_videos:
            await message.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
            return
        input_path = pending_videos[orig_msg_id]
        output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
        status_msg = await message.reply(f"⚙️ Encoding {os.path.basename(input_path)}...")

        await encode_video(input_path, output_path, status_msg)

        await message.reply(f"✅ Done {os.path.basename(input_path)}")
        await client.send_document(message.chat.id, output_path)

        os.remove(input_path)
        os.remove(output_path)
        pending_videos.pop(orig_msg_id, None)
    else:
        await message.reply("Reply to a video/document with /encode to process it.")


# === Run Bot ===
async def main():
    await asyncio.gather(
        auto_download_task(app),
        app.start()
    )
    print("Bot running...")
    await app.idle()


if __name__ == "__main__":
    asyncio.run(main())
