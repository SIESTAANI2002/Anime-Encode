import os
import time
import json
import threading
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")   # Pyrogram session string
CHAT_ID = int(os.getenv("CHAT_ID"))            # Telegram chat ID for auto uploads

DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

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


# === Progress Bar Helper ===
def progress_bar(done, total, elapsed, prefix="Progress"):
    percent = (done / total) * 100 if total else 0
    filled = int(percent // 5)
    bar = "=" * filled + ">" + " " * (20 - filled)
    elapsed_time = time.strftime("%H:%M:%S", time.gmtime(elapsed))
    total_time = time.strftime("%H:%M:%S", time.gmtime(total)) if total else "??:??:??"
    return f"{prefix}: {percent:.1f}% [{bar}] {elapsed_time} / {total_time}"


# === Encode Video ===
def encode_video(input_path, output_path, message: Message):
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    start = time.time()
    last_update = 0

    for line in process.stdout:
        if "time=" in line:
            try:
                t_str = line.split("time=")[1].split(" ")[0]
                h, m, s = t_str.split(":")
                elapsed = int(float(h)) * 3600 + int(float(m)) * 60 + float(s)
                now = time.time()
                if now - last_update > 5:  # update every 5 sec
                    msg = progress_bar(elapsed, None, elapsed, prefix="Encoding")
                    message.edit_text(msg)
                    last_update = now
            except:
                pass

    process.wait()
    return output_path


# === SubsPlease Auto Download ===
def get_recent_releases():
    try:
        res = requests.get(SUBS_API_URL, timeout=15).json()
        return [(ep["release_title"], ep["link"]) for ep in res.get("data", [])]
    except Exception as e:
        print("SubsPlease API error:", e)
        return []


def download_file(url, output_path, message: Message):
    r = requests.get(url, stream=True)
    total = int(r.headers.get("content-length", 0))
    done = 0
    start = time.time()
    last_update = 0

    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
                done += len(chunk)
                now = time.time()
                if now - last_update > 5:
                    msg = progress_bar(done, total, now - start, prefix="Downloading")
                    message.edit_text(msg)
                    last_update = now
    return output_path


def auto_mode(client: Client):
    while True:
        try:
            for title, url in get_recent_releases():
                if url not in downloaded_episodes:
                    msg = client.send_message(CHAT_ID, f"⬇️ Starting {title}")
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + ".mkv")
                    download_file(url, file_path, msg)

                    msg.edit_text("⚙️ Encoding...")
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_video(file_path, output_file, msg)

                    msg.edit_text("📤 Uploading...")
                    client.send_document(CHAT_ID, output_file, caption=title)

                    os.remove(file_path)
                    os.remove(output_file)

                    downloaded_episodes.add(url)
                    save_tracked()
                    msg.edit_text(f"✅ Done {title}")
            time.sleep(600)  # every 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(600)


# === Pyrogram Client ===
app = Client(
    "anime_bot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)


@app.on_message(filters.command("encode"))
def encode_command(client, message: Message):
    if not message.reply_to_message:
        return message.reply("⚠️ Reply to a video to encode.")

    replied = message.reply_to_message
    file_name = replied.video.file_name if replied.video else replied.document.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)

    status = message.reply("⬇️ Downloading...")
    replied.download(file_path)

    status.edit_text("⚙️ Encoding...")
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
    encode_video(file_path, output_path, status)

    status.edit_text("📤 Uploading...")
    client.send_document(message.chat.id, output_path)

    os.remove(file_path)
    os.remove(output_path)
    status.edit_text(f"✅ Done {file_name}")


if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    app.run()
