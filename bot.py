import os
import json
import time
import threading
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING")
CHAT_ID = int(os.getenv("CHAT_ID"))  # Telegram channel/group ID
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


# === FFmpeg Setup (Static) ===
FFMPEG_PATH = os.path.join(os.getcwd(), "ffmpeg")
if not os.path.exists(FFMPEG_PATH):
    print("⬇️ Downloading static ffmpeg...")
    url = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-i686-static.tar.xz"
    subprocess.run(["wget", url, "-O", "ffmpeg.tar.xz"])
    subprocess.run(["tar", "xf", "ffmpeg.tar.xz", "--strip-components=1", "-C", "."])
    print("✅ FFMPEG ready")


# === Pyrogram Client ===
app = Client(
    name="anime_bot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

# Task queue
task_queue = []
current_task = None
cancel_flag = False


# === UTILITIES ===
def progress_bar(percent, size=20):
    filled = int(size * percent / 100)
    empty = size - filled
    return f"[{'█' * filled}{'▒' * empty}] {percent:.0f}%"


def download_file(url, output_path, message: Message):
    try:
        r = requests.get(url, stream=True)
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        chunk_size = 1024 * 32

        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if cancel_flag:
                    message.edit("⚠️ Task cancelled.")
                    return False
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    percent = downloaded / total * 100 if total else 0
                    message.edit(f"⬇️ Downloading {os.path.basename(output_path)}\n"
                                 f"{progress_bar(percent)} {downloaded/1024/1024:.2f}MB/{total/1024/1024:.2f}MB")
        return True
    except Exception as e:
        message.edit(f"❌ Download error: {e}")
        return False


def encode_video(input_path, output_path, message: Message):
    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if "frame=" in line or "time=" in line:
            message.edit(f"⚙️ Encoding {os.path.basename(input_path)}\n{line.strip()}")
    process.wait()
    return output_path


# === AUTO DOWNLOAD ===
def auto_mode():
    while True:
        try:
            recent = requests.get(SUBS_API_URL, timeout=15).json().get("data", [])
            for ep in recent:
                title = ep["release_title"]
                url = ep["link"]
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + ".mkv")
                    msg = app.send_message(CHAT_ID, f"⬇️ Auto downloading {title}...")
                    success = download_file(url, file_path, msg)
                    if not success:
                        continue
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    msg.edit(f"⚙️ Auto encoding {title}...")
                    encode_video(file_path, output_file, msg)
                    app.send_document(CHAT_ID, output_file)
                    os.remove(file_path)
                    os.remove(output_file)
                    downloaded_episodes.add(url)
                    save_tracked()
                    msg.edit(f"✅ Done {title}")
            time.sleep(600)  # 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)


# === MANUAL ENCODING ===
pending_videos = {}


@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    pending_videos[message.id] = {"file_name": file_name, "message": message}
    message.reply(f"✅ Saved {file_name}. Reply with /encode to start encoding.")


@app.on_message(filters.command("encode"))
def encode_command(client, message: Message):
    global current_task
    if message.reply_to_message:
        orig_msg_id = message.reply_to_message.id
        if orig_msg_id not in pending_videos:
            message.reply("⚠️ File not found. Make sure the video is fully uploaded/downloaded.")
            return
        task_queue.append(pending_videos.pop(orig_msg_id))
        if not current_task:
            threading.Thread(target=process_tasks, args=(app,), daemon=True).start()
    else:
        message.reply("Reply to a video/document with /encode to process it.")


@app.on_message(filters.command("cancel"))
def cancel_task(client, message: Message):
    global cancel_flag
    cancel_flag = True
    message.reply("⚠️ Current task will be cancelled.")


@app.on_message(filters.command("skip"))
def skip_task(client, message: Message):
    global current_task
    if current_task:
        current_task["skip"] = True
        message.reply("⏭️ Skipping current task.")


def process_tasks(client):
    global current_task, cancel_flag
    while task_queue:
        cancel_flag = False
        current_task = task_queue.pop(0)
        msg_obj = current_task["message"]
        input_path = os.path.join(DOWNLOAD_FOLDER, current_task["file_name"])
        output_path = os.path.join(ENCODED_FOLDER, current_task["file_name"])
        msg_status = msg_obj.reply("⬇️ Downloading...")

        # Download first
        url = None  # Already uploaded video
        msg_status.edit(f"⚙️ Processing {current_task['file_name']}...")
        encode_video(input_path, output_path, msg_status)
        app.send_document(msg_obj.chat.id, output_path)
        msg_status.edit(f"✅ Done {current_task['file_name']}")
        os.remove(input_path)
        os.remove(output_path)
    current_task = None


# === RUN BOT ===
if __name__ == "__main__":
    app.start()
    threading.Thread(target=auto_mode, daemon=True).start()
    print("✅ Bot is running...")
    app.run()
