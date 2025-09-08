import os
import json
import time
import math
import threading
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))  # e.g., -1001234567890
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# === Track downloaded episodes ===
if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === Helper Functions ===
def human_readable(size_bytes):
    if size_bytes == 0:
        return "0B"
    size_name = ("B","KB","MB","GB","TB")
    i = int(math.floor(math.log(size_bytes,1024)))
    p = math.pow(1024,i)
    s = round(size_bytes/p,2)
    return f"{s}{size_name[i]}"

def progress_bar(percent, length=20):
    filled_len = int(length * percent / 100)
    return "█"*filled_len + "▒"*(length-filled_len)

# === Download with fancy progress ===
def download_file(url, output_path, msg: Message):
    r = requests.get(url, stream=True)
    total_size = int(r.headers.get("content-length", 0))
    downloaded = 0
    chunk_size = 1024*64
    start_time = time.time()

    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=chunk_size):
            if chunk:
                f.write(chunk)
                downloaded += len(chunk)
                percent = downloaded*100/total_size
                elapsed = int(time.time() - start_time)
                speed = human_readable(downloaded/elapsed)+"/s" if elapsed>0 else "0B/s"
                eta = int((total_size-downloaded)/(downloaded/elapsed)) if downloaded>0 else 0
                text = (
                    f"⌑ Downloading » {os.path.basename(output_path)}\n"
                    f"⌑ {progress_bar(percent)} » {percent:.2f}%\n"
                    f"⌑ Done   : {human_readable(downloaded)} of {human_readable(total_size)}\n"
                    f"⌑ Speed  : {speed}\n"
                    f"⌑ ETA    : {eta}s\n"
                    f"⌑ Past   : {elapsed}s"
                )
                try: msg.edit(text)
                except: pass
    msg.edit(f"✅ Download complete » {os.path.basename(output_path)}")
    return output_path

# === Encode with fancy progress ===
def encode_video(input_path, output_path, msg: Message):
    ext = os.path.splitext(input_path)[1]
    output_path = os.path.splitext(output_path)[0]+ext

    command = [
        "ffmpeg", "-i", input_path,
        "-vf","scale=-1:720",
        "-c:v","libx264","-preset","fast","-crf","23",
        "-c:a","aac","-b:a","128k",
        "-y", output_path
    ]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    while True:
        line = process.stderr.readline()
        if not line:
            break
        if "frame=" in line or "time=" in line:
            try:
                msg.edit(f"⌑ Encoding » {os.path.basename(input_path)}\n⌑ {line.strip()}")
            except: pass
    process.wait()
    msg.edit(f"✅ Encoding complete » {os.path.basename(output_path)}")
    return output_path

# === Upload with fancy progress ===
def upload_file(client: Client, chat_id, file_path, msg: Message):
    def progress(current, total):
        percent = current*100/total
        bar = progress_bar(percent)
        text = (
            f"⌑ Uploading » {os.path.basename(file_path)}\n"
            f"⌑ {bar} » {percent:.2f}%\n"
            f"⌑ Done   : {human_readable(current)} of {human_readable(total)}"
        )
        try: msg.edit(text)
        except: pass

    client.send_document(chat_id, file_path, progress=progress)
    msg.edit(f"✅ Upload complete » {os.path.basename(file_path)}")

# === SubsPlease auto download every 10 minutes ===
def auto_mode(client: Client):
    while True:
        try:
            releases = requests.get(SUBS_API_URL, timeout=15).json().get("data", [])
            for ep in releases:
                title = ep["release_title"]
                url = ep["link"]
                if url in downloaded_episodes: continue

                msg = client.send_message(CHAT_ID, f"Starting download » {title}")
                local_path = os.path.join(DOWNLOAD_FOLDER, title+os.path.splitext(url)[1])
                download_file(url, local_path, msg)
                encoded_path = os.path.join(ENCODED_FOLDER, os.path.basename(local_path))
                encode_video(local_path, encoded_path, msg)
                upload_file(client, CHAT_ID, encoded_path, msg)

                os.remove(local_path)
                os.remove(encoded_path)
                downloaded_episodes.add(url)
                save_tracked()
            time.sleep(600)  # 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Pyrogram Client ===
app = Client(
    name="anime_userbot",
    api_id=API_ID,
    api_hash=API_HASH,
    session_string=SESSION_STRING
)

pending_videos = {}  # Track manual uploads

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    msg = message.reply("⌑ Preparing file...")
    file_path = os.path.join(DOWNLOAD_FOLDER, message.document.file_name if message.document else message.video.file_name)
    message.download(file_path)
    pending_videos[message.id] = file_path
    msg.edit(f"✅ Saved » {os.path.basename(file_path)}\nReply with /encode to start encoding.")

@app.on_message(filters.command("encode"))
def encode_command(client, message: Message):
    if message.reply_to_message:
        orig_id = message.reply_to_message.id
        if orig_id not in pending_videos:
            message.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
            return
        input_path = pending_videos[orig_id]
        output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))
        msg = message.reply(f"⌑ Starting encode » {os.path.basename(input_path)}")

        encode_video(input_path, output_path, msg)
        upload_file(client, message.chat.id, output_path, msg)

        os.remove(input_path)
        os.remove(output_path)
        pending_videos.pop(orig_id)
    else:
        message.reply("Reply to a video/document with /encode to process it.")

# === Run bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    app.run()
