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
CHAT_ID = os.getenv("CHAT_ID")
DOWNLOAD_FOLDER = "downloads"
ENCODED_FOLDER = "encoded"
TRACK_FILE = "downloaded.json"
SUBS_API_URL = "https://subsplease.org/api/?f=latest&tz=UTC"

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(ENCODED_FOLDER, exist_ok=True)

if os.path.exists(TRACK_FILE):
    with open(TRACK_FILE, "r") as f:
        downloaded_episodes = set(json.load(f))
else:
    downloaded_episodes = set()

def save_tracked():
    with open(TRACK_FILE, "w") as f:
        json.dump(list(downloaded_episodes), f)

# === Encode Function ===
def encode_video(input_path, output_path, message: Message):
    import json
    ext = os.path.splitext(input_path)[1].lower()
    output_path = os.path.splitext(output_path)[0] + ext

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

    last_update = time.time()
    progress_msg = message.reply_text(f"⚙️ Encoding {os.path.basename(input_path)}...")

    for line in process.stdout:
        if ("frame=" in line or "time=" in line) and time.time() - last_update > 20:
            last_update = time.time()
            try:
                progress_msg.edit_text(f"📊 {line.strip()}")
            except:
                pass

    process.wait()
    return output_path

# === SubsPlease Auto Download ===
def get_recent_releases():
    releases = []
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(SUBS_API_URL, headers=headers, timeout=15)
        try:
            data = res.json()
        except ValueError:
            print("⚠️ SubsPlease returned non-JSON, skipping this check.")
            return []

        for ep in data.get("data", []):
            title = ep["release_title"]
            link = ep["link"]
            releases.append((title, link))
    except Exception as e:
        print("SubsPlease API error:", e)
    return releases

def download_file(url, output_path, message: Message):
    r = requests.get(url, stream=True)
    total_length = r.headers.get('content-length')
    if total_length is None:
        with open(output_path, "wb") as f:
            f.write(r.content)
        return output_path

    dl = 0
    total_length = int(total_length)
    start_time = time.time()
    progress_msg = message.reply_text(f"⬇️ Downloading {os.path.basename(output_path)}...")
    last_update = time.time()

    with open(output_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
                dl += len(chunk)
                if time.time() - last_update > 20:
                    last_update = time.time()
                    percent = dl / total_length * 100
                    elapsed = time.time() - start_time
                    speed = dl / elapsed
                    try:
                        progress_msg.edit_text(
                            f"⬇️ Downloading {os.path.basename(output_path)}\n"
                            f"{percent:.2f}% ({dl//1024//1024}MB / {total_length//1024//1024}MB)\n"
                            f"Speed: {speed/1024/1024:.2f} MB/s"
                        )
                    except:
                        pass
    return output_path

def auto_mode(client: Client):
    while True:
        try:
            recent = get_recent_releases()
            for title, url in recent:
                if url not in downloaded_episodes:
                    file_path = os.path.join(DOWNLOAD_FOLDER, title + os.path.splitext(url)[1])
                    dummy_msg = client.send_message(CHAT_ID, f"📥 Auto-downloading {title}")
                    download_file(url, file_path, dummy_msg)

                    output_file = os.path.join(ENCODED_FOLDER, os.path.basename(file_path))
                    encode_video(file_path, output_file, dummy_msg)

                    client.send_document(CHAT_ID, output_file)

                    os.remove(file_path)
                    os.remove(output_file)
                    downloaded_episodes.add(url)
                    save_tracked()
            time.sleep(600)
        except Exception as e:
            print("Auto mode error:", e)
            time.sleep(60)

# === Pyrogram Client ===
app = Client(name="anime_userbot", session_string=SESSION_STRING, api_id=API_ID, api_hash=API_HASH)

@app.on_message(filters.video | filters.document)
def handle_video(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    file_path = os.path.join(DOWNLOAD_FOLDER, file_name)

    # download and encode directly (no /encode step)
    download_file(message.download(file_path), file_path, message)
    output_path = os.path.join(ENCODED_FOLDER, file_name)
    encode_video(file_path, output_path, message)

    message.reply_document(output_path)

    os.remove(file_path)
    os.remove(output_path)

if __name__ == "__main__":
    threading.Thread(target=auto_mode, args=(app,), daemon=True).start()
    print("Bot is running...")
    app.run()
