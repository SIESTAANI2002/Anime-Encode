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
SESSION_STRING = os.getenv("SESSION_STRING")  # session string login
CHAT_ID = int(os.getenv("CHAT_ID"))           # your private chat id

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


# === PROGRESS BAR ===
def get_progress_bar(current, total, length=20):
    filled = int(length * current / total)
    bar = "█" * filled + "▒" * (length - filled)
    percent = current / total * 100
    return f"{bar} » {percent:.2f}%"


# === DOWNLOAD FUNCTION ===
def download_file(url, output_path, progress_callback=None):
    r = requests.get(url, stream=True)
    total_length = r.headers.get("content-length")
    if total_length is None:
        with open(output_path, "wb") as f:
            f.write(r.content)
        return output_path

    dl = 0
    total_length = int(total_length)
    start_time = time.time()

    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):  # 1MB
            if chunk:
                f.write(chunk)
                dl += len(chunk)
                if progress_callback:
                    elapsed = time.time() - start_time
                    speed = dl / elapsed / 1024 / 1024
                    percent = dl / total_length * 100
                    eta = (total_length - dl) / (dl / elapsed) if dl else 0
                    progress_callback(dl, total_length, percent, speed, elapsed, eta)
    return output_path


# === ENCODE FUNCTION ===
def encode_video(input_path, output_path, progress_callback=None):
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if "time=" in line and progress_callback:
            progress_callback(line.strip())
    process.wait()
    return output_path


# === PYROGRAM CLIENT ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)
pending_videos = {}


# === MANUAL ENCODE ===
@app.on_message(filters.command("encode") & filters.reply)
def encode_command(client, message: Message):
    orig_msg_id = message.reply_to_message.id
    if orig_msg_id not in pending_videos:
        message.reply("⚠️ File not found, please upload it again.")
        return
    input_path = pending_videos[orig_msg_id]
    output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))

    msg = message.reply(f"⚙️ Encoding {os.path.basename(input_path)}...")

    def progress(line):
        try:
            msg.edit(f"⚙️ Encoding {os.path.basename(input_path)}\n{line}")
        except:
            pass

    encode_video(input_path, output_path, progress_callback=progress)
    msg.edit(f"✅ Encoding finished: {os.path.basename(input_path)}")
    client.send_document(message.chat.id, output_path)

    os.remove(input_path)
    os.remove(output_path)
    pending_videos.pop(orig_msg_id, None)


# === VIDEO HANDLER ===
@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    msg = message.reply(f"⬇️ Downloading {file_name}...")

    def progress(dl, total, percent, speed, elapsed, eta):
        text = (f"Filename : {file_name}\n"
                f"Downloading: {get_progress_bar(dl, total)}\n"
                f"Done   : {dl/1024/1024:.2f}MB of {total/1024/1024:.2f}MB\n"
                f"Speed  : {speed:.2f}MB/s\n"
                f"ETA    : {int(eta)}s\n"
                f"Elapsed: {int(elapsed)}s")
        try:
            msg.edit(text)
        except:
            pass

    download_file(message.document.file_id if message.document else message.video.file_id, file_path, progress_callback=progress)
    pending_videos[message.id] = file_path

    # Auto encode after download
    out_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
    msg.edit(f"⚙️ Auto encoding {file_name}...")
    encode_video(file_path, out_file, progress_callback=lambda x: msg.edit(f"⚙️ Encoding {file_name}\n{x}"))
    msg.edit(f"✅ Finished {file_name}, uploading...")
    client.send_document(message.chat.id, out_file)

    os.remove(file_path)
    os.remove(out_file)
    pending_videos.pop(message.id, None)


# === AUTO DOWNLOAD THREAD ===
def auto_mode(client: Client):
    while True:
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(SUBS_API_URL, headers=headers, timeout=15)
            try:
                data = r.json()
            except ValueError:
                print("⚠️ SubsPlease returned non-JSON, retrying in 60s")
                time.sleep(60)
                continue

            for ep in data.get("data", []):
                title = ep["release_title"]
                url = ep["link"]
                if url in downloaded_episodes:
                    continue

                print(f"⬇️ Auto downloading {title}")
                file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                download_file(url, file_path)

                out_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                encode_video(file_path, out_file)

                client.send_document(CHAT_ID, out_file)
                os.remove(file_path)
                os.remove(out_file)

                downloaded_episodes.add(url)
                save_tracked()
                print(f"✅ Done {title}")

            time.sleep(600)  # 10 min
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)


# === RUN BOT ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    print("🚀 Bot is running...")
    app.run()
