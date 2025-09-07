import os
import json
import time
import threading
import subprocess
import requests
from pyrogram import Client, filters
from pyrogram.types import Message

# === CONFIG ===
API_ID = int(os.getenv("API_ID"))        # from my.telegram.org
API_HASH = os.getenv("API_HASH")
SESSION_STRING = os.getenv("SESSION_STRING") # Pyrogram session string
CHAT_ID = os.getenv("CHAT_ID")           # channel/group id for auto-upload
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
PENDING_FILE = "pending_videos.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

# Load tracked episodes
if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

# Load pending videos
if os.path.exists(PENDING_FILE):
    with open(PENDING_FILE, "r") as f:
        pending_videos = json.load(f)
else:
    pending_videos = {}

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

def save_pending():
    with open(PENDING_FILE, "w") as f:
        json.dump(pending_videos, f)

# === Encode Function ===
def encode_video(input_path, output_path, progress_callback=None):
    import json
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

    # Detect audio streams
    probe_cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index,codec_name",
        "-of", "json", input_path
    ]
    result = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    audio_info = json.loads(result.stdout).get("streams", [])

    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:s", "copy"
    ]

    for stream in audio_info:
        idx = stream["index"]
        codec = stream["codec_name"].lower()
        if codec == "aac":
            command += [f"-c:a:{idx}", "aac", f"-b:a:{idx}", "128k"]
        elif codec == "opus":
            command += [f"-c:a:{idx}", "libopus", f"-b:a:{idx}", "128k"]
        elif codec == "mp3":
            command += [f"-c:a:{idx}", "libmp3lame", f"-b:a:{idx}", "128k"]
        elif codec == "flac":
            command += [f"-c:a:{idx}", "flac"]
        else:
            command += [f"-c:a:{idx}", "aac", f"-b:a:{idx}", "128k"]

    command += ["-y", output_path]

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        if progress_callback and ("frame=" in line or "time=" in line):
            progress_callback(line.strip())
    process.wait()
    return output_path

# === SubsPlease Auto Download ===
def get_recent_releases():
    releases = []
    try:
        res = requests.get(SUBS_API_URL, timeout=15).json()
        for ep in res.get("data", []):
            title = ep["release_title"]
            link = ep["link"]
            releases.append((title, link))
    except Exception as e:
        print("SubsPlease API error:", e)
    return releases

def download_file(url, output_path):
    r = requests.get(url, stream=True)
    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)
    return output_path

def auto_mode(client: Client):
    while True:
        try:
            recent = get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    print(f"⬇️ Downloading {title}")
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    download_file(url, file_path)

                    print(f"⚙️ Encoding {title}")
                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_video(file_path, output_file)

                    print(f"📤 Uploading {title} to chat")
                    client.send_document(CHAT_ID, output_file)

                    os.remove(file_path)
                    os.remove(output_file)

                    downloaded_episodes.add(url)
                    save_tracked()
                    print(f"✅ Done {title}\n")
            time.sleep(600)  # 10 minutes
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(300)

# === Pyrogram Client ===
app = Client(":memory:", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)
    message.download(file_path)
    pending_videos[str(message.id)] = file_path
    save_pending()
    message.reply(f"✅ Saved {file_name}. Reply to this message with /encode to process.")

@app.on_message(filters.command("encode"))
def encode_command(client, message: Message):
    if message.reply_to_message:
        orig_msg_id = str(message.reply_to_message.id)
        if orig_msg_id not in pending_videos:
            message.reply("⚠️ File not found, please upload it again.")
            return
        input_path = pending_videos[orig_msg_id]
        output_path = os.path.join(ENCODED_FOLDER, os.path.basename(input_path))

        msg = message.reply(f"⚙️ Encoding {os.path.basename(input_path)}...\nProgress: 0%")

        def progress(line):
            try:
                # Optionally parse frame/time info to estimate percentage
                msg.edit(f"⚙️ Encoding {os.path.basename(input_path)}...\n{line}")
            except: pass

        encode_video(input_path, output_path, progress_callback=progress)
        message.reply(f"✅ Done {os.path.basename(input_path)}")
        client.send_document(message.chat.id, output_path)

        os.remove(input_path)
        os.remove(output_path)
        pending_videos.pop(orig_msg_id, None)
        save_pending()
    else:
        message.reply("Reply to a video/document with /encode to process it.")

# === Run Bot ===
if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    app.run()
