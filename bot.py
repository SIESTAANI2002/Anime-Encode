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
CHAT_ID = int(os.getenv("CHAT_ID"))
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"
FFMPEG_BIN = "./ffmpeg"  # Push ffmpeg binary in repo

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# === Load tracked episodes ===
if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === Queue ===
task_queue = []
current_task = None
cancel_flag = False

# === Fancy progress bar ===
def progress_bar(percent, length=20):
    done = int(length * percent // 100)
    return f"[{'█'*done}{'▒'*(length-done)}] {percent:.2f}%"

# === Video Encoder ===
def encode_video(input_path, output_path, msg, stage="Encoding"):
    global cancel_flag
    cmd = [
        FFMPEG_BIN, "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-c:s", "copy",
        "-y", output_path
    ]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    total_time = None
    for line in process.stdout:
        if cancel_flag:
            process.kill()
            msg.edit("❌ Task canceled.")
            return None
        if "time=" in line:
            try:
                time_str = line.split("time=")[1].split(" ")[0]
                h, m, s = map(float, time_str.split(":"))
                elapsed_sec = int(h*3600 + m*60 + s)
                if total_time:
                    percent = min(100, (elapsed_sec/total_time)*100)
                    msg.edit(f"⌑ {stage} » {progress_bar(percent)} {elapsed_sec//60:02}:{elapsed_sec%60:02}")
            except:
                pass
        if "Duration" in line and total_time is None:
            try:
                dur_str = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = map(float, dur_str.split(":"))
                total_time = int(h*3600 + m*60 + s)
            except:
                pass
    process.wait()
    return output_path

# === Download Function ===
def download_file(url, output_path, msg=None):
    global cancel_flag
    with requests.get(url, stream=True) as r:
        total = int(r.headers.get('content-length', 0))
        downloaded = 0
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if cancel_flag:
                    msg.edit("❌ Task canceled during download.")
                    return None
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if msg:
                        percent = (downloaded/total)*100 if total else 0
                        msg.edit(f"⌑ Downloading » {progress_bar(percent)} {downloaded//1024}KB / {total//1024 if total else '?'}KB")
    return output_path

# === Auto mode ===
def auto_mode(client: Client):
    while True:
        try:
            recent = requests.get(SUBS_API_URL, timeout=15).json().get("data", [])
            for ep in recent:
                title = ep["release_title"]
                link = ep["link"]
                if link not in downloaded_episodes:
                    task_queue.append({"type": "subs", "title": title, "url": link})
            time.sleep(600)  # 10 min
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Task Processor ===
def process_queue(client: Client):
    global current_task, cancel_flag
    while True:
        if not current_task and task_queue:
            current_task = task_queue.pop(0)
            cancel_flag = False
            title = current_task.get("title")
            url = current_task.get("url")
            media_msg = current_task.get("media_msg")
            msg = None
            try:
                if current_task["type"] == "subs":
                    file_path = f"{DOWNLOAD_FOLDER}/{title}.mkv"
                    msg = client.send_message(CHAT_ID, f"⬇️ Downloading {title}")
                    download_file(url, file_path, msg)
                    output_path = f"{ENCODED_FOLDER}/{title}.mkv"
                    encode_video(file_path, output_path, msg)
                    client.send_document(CHAT_ID, output_path)
                    os.remove(file_path)
                    os.remove(output_path)
                    downloaded_episodes.add(url)
                    save_tracked()
                elif current_task["type"] == "manual":
                    file_name = media_msg.document.file_name if media_msg.document else media_msg.video.file_name
                    file_path = f"{DOWNLOAD_FOLDER}/{file_name}"
                    msg = client.send_message(media_msg.chat.id, f"⬇️ Downloading {file_name}")
                    media_msg.download(file_path)
                    output_path = f"{ENCODED_FOLDER}/{file_name}"
                    encode_video(file_path, output_path, msg)
                    client.send_document(media_msg.chat.id, output_path)
                    os.remove(file_path)
                    os.remove(output_path)
            except Exception as e:
                if msg:
                    msg.edit(f"❌ Error: {e}")
            current_task = None
        else:
            time.sleep(5)

# === Pyrogram Client ===
app = Client("anime_bot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    task_queue.append({"type": "manual", "media_msg": message})
    message.reply("✅ Added to queue for download & encoding.")

@app.on_message(filters.command(["cancel"]))
def cancel_task(client, message: Message):
    global cancel_flag
    if current_task:
        cancel_flag = True
        message.reply("⚠️ Current task will be canceled.")
    else:
        message.reply("❌ No running task.")

@app.on_message(filters.command(["skip"]))
def skip_task(client, message: Message):
    global current_task
    if current_task:
        cancel_flag = True
        message.reply("⚠️ Skipping current task.")
    else:
        message.reply("❌ No running task.")

@app.on_message(filters.command(["queue"]))
def show_queue(client, message: Message):
    if task_queue:
        text = "📃 Current Queue:\n"
        for i, t in enumerate(task_queue, 1):
            text += f"{i}. {t.get('title') or (t['media_msg'].document.file_name if t['media_msg'].document else t['media_msg'].video.file_name)}\n"
        message.reply(text)
    else:
        message.reply("✅ Queue is empty.")

# === Start Threads ===
threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
threading.Thread(target=process_queue, args=(app,), daemon=True).start()

# === Run Bot ===
app.run()
