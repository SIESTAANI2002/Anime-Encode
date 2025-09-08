import os
import time
import math
import shutil
import asyncio
from pyrogram import Client

async def update_progress_message(message, filename, task, done_bytes, total_bytes, start_time):
    """Update Telegram message with fancy pro-leech style progress."""
    # Calculate percentages
    percent = (done_bytes / total_bytes) * 100 if total_bytes else 0
    bar_len = 20
    filled_len = int(bar_len * percent // 100)
    bar = "█" * filled_len + "▒" * (bar_len - filled_len)
    
    # Time calculations
    elapsed = time.time() - start_time
    speed = done_bytes / elapsed if elapsed > 0 else 0
    eta = (total_bytes - done_bytes) / speed if speed > 0 else 0

    # Disk free
    total, used, free = shutil.disk_usage("/")
    free_gb = free / (1024**3)

    # Human-readable
    def human_size(size):
        for unit in ['B','KB','MB','GB','TB']:
            if size < 1024: return f"{size:.2f}{unit}"
            size /= 1024
        return f"{size:.2f}PB"
    
    msg_text = f"""
Name » {filename}
⌑ Task   » {task}
⌑ {bar} » {percent:.2f}%
⌑ Done   : {human_size(done_bytes)} of {human_size(total_bytes)}
⌑ Speed  : {human_size(speed)}/s
⌑ ETA    : {int(eta)}s
⌑ Past   : {int(elapsed)}s
⌑ ENG    : PyroF v2.2.11
⌑ User   : Ānī

____________________________
FREE: {free_gb:.2f}GB | DL: {human_size(speed)}/s
UPTM: {int(elapsed//3600)}h{int((elapsed%3600)//60)}m{int(elapsed%60)}s | UL: 0B/s
"""
    try:
        await message.edit(msg_text)
    except Exception:
        pass

async def download_file(client, url, message, filename):
    """Download file with progress."""
    local_path = os.path.join("downloads", filename)
    start_time = time.time()
    done = 0

    r = client.get(url, stream=True)
    total = int(r.headers.get('content-length', 0))
    
    with open(local_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024*1024):  # 1MB chunks
            if chunk:
                f.write(chunk)
                done += len(chunk)
                await update_progress_message(message, filename, "Downloading", done, total, start_time)
    return local_path

async def encode_file(client, input_path, message):
    """Encode file with ffmpeg and progress."""
    import subprocess, re

    filename = os.path.basename(input_path)
    output_path = os.path.join("encoded", filename)
    start_time = time.time()

    command = [
        "ffmpeg", "-i", input_path,
        "-vf", "scale=-1:720",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-y", output_path
    ]

    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True
    )

    total_duration = None
    for line in process.stdout:
        if "Duration" in line:
            m = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', line)
            if m:
                h, m_, s = m.groups()
                total_duration = int(h)*3600 + int(m_)*60 + float(s)
        if "time=" in line and total_duration:
            m = re.search(r'time=(\d+):(\d+):(\d+\.\d+)', line)
            if m:
                h, m_, s = m.groups()
                elapsed_sec = int(h)*3600 + int(m_)*60 + float(s)
                await update_progress_message(message, filename, "Encoding", elapsed_sec, total_duration, start_time)

    process.wait()
    return output_path
