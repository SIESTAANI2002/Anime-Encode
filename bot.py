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
CHAT_ID = int(os.getenv("CHAT_ID"))  # Target channel/group
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

# Queue system
task_queue = []
current_task = None
cancel_flag = False

# === Helper Functions ===
def progress_bar(percent, length=20):
    done_len = int(length * percent // 100)
    bar = "█" * done_len + "▒" * (length - done_len)
    return f"[{bar}] {percent:.1f}%"

def run_ffmpeg(input_path, output_path, progress_callback=None):
    cmd = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-c:s", "copy",
        "-y", output_path
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if progress_callback:
            progress_callback(line)
    process.wait()

def download_file(url, dest, progress_callback=None):
    r = requests.get(url, stream=True)
    total = int(r.headers.get("content-length", 0))
    downloaded = 0
    chunk_size = 8192
    for chunk in r.iter_content(chunk_size=chunk_size):
        if chunk:
            dest.write(chunk)
            downloaded += len(chunk)
            if progress_callback and total:
                percent = downloaded / total * 100
                progress_callback(percent, downloaded, total)

# === Auto Download Thread ===
def auto_mode(client: Client):
    global task_queue
    while True:
        try:
            res = requests.get(SUBS_API_URL, timeout=15).json()
            for ep in res.get("data", []):
                title, url = ep["release_title"], ep["link"]
                if url not in downloaded_episodes:
                    file_ext = os.path.splitext(url)[1]
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + file_ext)
                    task_queue.append({
                        "type": "auto",
                        "title": title,
                        "url": url,
                        "file_path": file_path,
                        "chat_id": CHAT_ID
                    })
            time.sleep(600)  # every 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Task Processor ===
def process_tasks(client: Client):
    global task_queue, current_task, cancel_flag
    while True:
        if task_queue and not current_task:
            current_task = task_queue.pop(0)
            cancel_flag = False
            title = current_task.get("title")
            chat_id = current_task.get("chat_id")
            file_path = current_task.get("file_path")
            url = current_task.get("url")

            try:
                # Download
                msg = client.send_message(chat_id, f"⬇️ Downloading: {title}")
                with open(file_path, "wb") as f:
                    download_file(url, f, progress_callback=lambda p, d, t: msg.edit(
                        f"⬇️ Downloading: {title}\n{progress_bar(p)} {d/1024/1024:.2f}MB/{t/1024/1024:.2f}MB"
                    ))
                    if cancel_flag: raise Exception("Cancelled")

                # Encode
                output_path = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                msg.edit(f"⚙️ Encoding: {title}")
                run_ffmpeg(file_path, output_path, progress_callback=lambda l: None)
                if cancel_flag: raise Exception("Cancelled")

                # Upload
                client.send_document(chat_id, output_path, caption=f"✅ Done: {title}")

                # Cleanup
                os.remove(file_path)
                os.remove(output_path)
                downloaded_episodes.add(url)
                save_tracked()
            except Exception as e:
                client.send_message(chat_id, f"⚠️ Task failed: {title}\n{e}")
            finally:
                current_task = None
        else:
            time.sleep(5)

# === Pyrogram Client ===
app = Client(
    name="anime_bot",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH
)

# === Manual Encoding Command ===
@app.on_message(filters.command("encode") & filters.reply)
def manual_encode(client: Client, message: Message):
    global task_queue
    reply = message.reply_to_message
    if not reply or not (reply.video or reply.document):
        message.reply("⚠️ Reply to a video/document to encode.")
        return

    file_name = reply.video.file_name if reply.video else reply.document.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    reply.download(file_path)
    task_queue.append({
        "type": "manual",
        "title": file_name,
        "url": None,
        "file_path": file_path,
        "chat_id": message.chat.id
    })
    message.reply(f"✅ Added {file_name} to encoding queue.")

# === /cancel /skip /queue Commands ===
@app.on_message(filters.command("cancel"))
def cancel_task(client: Client, message: Message):
    global cancel_flag
    cancel_flag = True
    message.reply("⛔ Current task will be cancelled.")

@app.on_message(filters.command("skip"))
def skip_task(client: Client, message: Message):
    global current_task
    if current_task:
        cancel_flag = True
        message.reply(f"⏭ Skipping: {current_task.get('title')}")
    else:
        message.reply("⚠️ No task to skip.")

@app.on_message(filters.command("queue"))
def show_queue(client: Client, message: Message):
    global task_queue
    if not task_queue:
        message.reply("📭 Queue is empty.")
    else:
        txt = "📋 Current Queue:\n" + "\n".join(f"{i+1}. {t.get('title')}" for i, t in enumerate(task_queue))
        message.reply(txt)

# === Run Bot ===
if __name__ == "__main__":
    with app:
        threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
        threading.Thread(target=process_tasks, args=(app,), daemon=True).start()
        app.run()
