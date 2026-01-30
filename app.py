import os
import shutil
import zipfile
import json
import uuid
import yt_dlp
import asyncio
import time
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi_utilities import repeat_every

app = FastAPI()

# Store download states
# {job_id: {"progress": float, "status": str, "file": str, "error": str, "playlist_title": str, "is_zip": bool, "created_at": float}}
jobs = {}

@app.on_event("startup")
@repeat_every(seconds=3600) # Every hour
def cleanup_old_files():
    now = time.time()
    one_day = 86400
    to_delete = []
    
    for job_id, job in jobs.items():
        if now - job.get("created_at", 0) > one_day:
            to_delete.append(job_id)
            if job.get("file"):
                file_path = os.path.join(DOWNLOAD_DIR, job["file"])
                if os.path.exists(file_path):
                    os.remove(file_path)
    
    for job_id in to_delete:
        del jobs[job_id]

class DownloadRequest(BaseModel):
    url: str
    format: str  # 'mp3' or 'mp4'
    selected_urls: list[str] = None

DOWNLOAD_DIR = os.path.abspath("downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

import re

def clean_ansi(text):
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)

def progress_hook(d, job_id):
    try:
        if d['status'] == 'downloading':
            # Extract percentage safely
            p_str = d.get('_percent_str', '0%')
            p_str = clean_ansi(p_str).replace('%', '').strip()
            
            try:
                progress = float(p_str)
            except:
                # Fallback to byte calculation
                downloaded = d.get('downloaded_bytes', 0)
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                if total > 0:
                    progress = (downloaded / total) * 100
                else:
                    progress = 0

            filename = os.path.basename(d.get('filename', ''))
            
            # Playlist info
            info = d.get('info_dict', {})
            idx = info.get('playlist_index')
            total_vids = info.get('n_entries')
            
            status_prefix = ""
            if idx is not None and total_vids is not None:
                status_prefix = f"[{idx}/{total_vids}] "
            
            jobs[job_id]["progress"] = progress
            jobs[job_id]["status"] = f"{status_prefix}Downloading..."
            jobs[job_id]["current_file"] = filename
            
        elif d['status'] == 'finished':
            jobs[job_id]["progress"] = 100
            jobs[job_id]["status"] = "Processing / Converting..."
    except Exception as e:
        print(f"Error in progress_hook: {e}")

import concurrent.futures

def download_task(job_id, url, format_type, selected_urls=None):
    job_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(job_dir, exist_ok=True)
    
    # Common options
    base_opts = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'concurrent_fragment_downloads': 5, # Multi-threaded fragment download
    }

    if format_type == 'mp3':
        base_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        base_opts.update({
            # Prefer H.264 video and AAC audio for maximum MP4 compatibility
            'format': 'bestvideo[vcodec^=avc1]+bestaudio[acodec^=mp4a]/best[vcodec^=avc1]/bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
        })

    try:
        jobs[job_id]["status"] = "Extracting playlist..."
        with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True}) as ydl:
            playlist_info = ydl.extract_info(url, download=False)
            playlist_title = playlist_info.get('title', 'playlist')
            jobs[job_id]["playlist_title"] = playlist_title
            
            if 'entries' in playlist_info:
                entries = [e for e in playlist_info['entries'] if e]
                if selected_urls:
                    entries = [e for e in entries if e.get('url') in selected_urls]
            else:
                entries = [playlist_info]

        if not entries:
            raise Exception("No videos selected or found.")

        total_vids = len(entries)
        completed_vids = 0
        video_progress = {} # {id: percentage}

        def video_progress_hook(d, vid_id):
            if jobs.get(job_id, {}).get("cancelled"):
                raise Exception("USER_CANCELLED")
            
            if d['status'] == 'downloading':
                p_str = clean_ansi(d.get('_percent_str', '0%')).replace('%', '').strip()
                try:
                    video_progress[vid_id] = float(p_str)
                except:
                    video_progress[vid_id] = 0
                
                # Update global progress as average of all videos
                total_progress = sum(video_progress.values()) / total_vids
                jobs[job_id]["progress"] = total_progress
                jobs[job_id]["status"] = f"Downloading [{completed_vids}/{total_vids}]"
                jobs[job_id]["current_file"] = os.path.basename(d.get('filename', ''))

        def download_single_video(entry, idx):
            if jobs.get(job_id, {}).get("cancelled"):
                raise Exception("USER_CANCELLED")
                
            nonlocal completed_vids
            vid_id = entry.get('id', str(idx))
            video_progress[vid_id] = 0
            
            opts = base_opts.copy()
            opts.update({
                'outtmpl': f'{job_dir}/%(title)s.%(ext)s',
                'progress_hooks': [lambda d: video_progress_hook(d, vid_id)],
            })
            
            try:
                video_url = entry.get('url') or entry.get('webpage_url') or entry.get('original_url')
                if not video_url:
                    raise Exception("Could not find video URL")
                    
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([video_url])
                
                if jobs.get(job_id, {}).get("cancelled"):
                    raise Exception("USER_CANCELLED")

                completed_vids += 1
                video_progress[vid_id] = 100
                jobs[job_id]["progress"] = sum(video_progress.values()) / total_vids
                jobs[job_id]["status"] = f"Downloading [{completed_vids}/{total_vids}]"
            except Exception as e:
                if "USER_CANCELLED" in str(e):
                    raise e
                print(f"Video {idx} failed: {e}")

        if total_vids == 1:
            # Single video: provide direct file
            download_single_video(entries[0], 0)
            
            files = os.listdir(job_dir)
            if not files:
                raise Exception("Download failed, no file produced.")
            
            final_file = files[0]
            shutil.move(os.path.join(job_dir, final_file), os.path.join(DOWNLOAD_DIR, final_file))
            shutil.rmtree(job_dir)
            
            jobs[job_id]["status"] = "Completed"
            jobs[job_id]["file"] = final_file
            jobs[job_id]["is_zip"] = False
            jobs[job_id]["progress"] = 100
        else:
            # Multiple videos: zip them
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                futures = {executor.submit(download_single_video, entry, i): entry for i, entry in enumerate(entries)}
                
                while futures:
                    done, not_done = concurrent.futures.wait(futures, timeout=0.5, return_when=concurrent.futures.FIRST_COMPLETED)
                    if jobs.get(job_id, {}).get("cancelled"):
                        for f in futures: f.cancel()
                        raise Exception("USER_CANCELLED")
                    futures = not_done

            if jobs.get(job_id, {}).get("cancelled"):
                raise Exception("USER_CANCELLED")

            jobs[job_id]["status"] = "Zipping files..."
            zip_filename = f"{job_id}.zip"
            zip_path = os.path.join(DOWNLOAD_DIR, zip_filename)
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for root, dirs, filenames in os.walk(job_dir):
                    for filename in filenames:
                        file_path = os.path.join(root, filename)
                        zipf.write(file_path, filename)
            
            shutil.rmtree(job_dir)
            jobs[job_id]["status"] = "Completed"
            jobs[job_id]["file"] = zip_filename
            jobs[job_id]["is_zip"] = True
            jobs[job_id]["progress"] = 100
        
    except Exception as e:
        jobs[job_id]["status"] = "Error"
        jobs[job_id]["error"] = str(e)
        if os.path.exists(job_dir):
            shutil.rmtree(job_dir)

@app.post("/api/info")
async def get_playlist_info(req: DownloadRequest):
    ydl_opts = {
        'extract_flat': 'in_playlist',
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # This is equivalent to --flat-playlist and extracting just titles/duration
            info = ydl.extract_info(req.url, download=False)
            
            if 'entries' in info:
                # It's a playlist
                entries = []
                for entry in info['entries']:
                    if entry:
                        entries.append({
                            "title": entry.get("title", entry.get("url", "Unknown Title")),
                            "url": entry.get("url") or entry.get("webpage_url"),
                            "duration": entry.get("duration")
                        })
                return {
                    "title": info.get("title", "Playlist"),
                    "entries": entries,
                    "is_playlist": True
                }
            else:
                # Single video
                return {
                    "title": info.get("title", "Video"),
                    "entries": [{
                        "title": info.get("title"), 
                        "url": info.get("webpage_url") or info.get("url"),
                        "duration": info.get("duration")
                    }],
                    "is_playlist": False
                }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/download")
async def start_download(req: DownloadRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "progress": 0, 
        "status": "Starting...", 
        "file": None, 
        "error": None, 
        "current_file": None,
        "playlist_title": "playlist",
        "cancelled": False,
        "created_at": time.time()
    }
    background_tasks.add_task(download_task, job_id, req.url, req.format, req.selected_urls)
    return {"job_id": job_id}

@app.post("/api/cancel/{job_id}")
async def cancel_download(job_id: str):
    if job_id in jobs:
        jobs[job_id]["cancelled"] = True
        jobs[job_id]["status"] = "Cancelling..."
        # Clean up zip file if it exists
        zip_filename = jobs[job_id].get("file")
        if zip_filename:
            zip_path = os.path.join(DOWNLOAD_DIR, zip_filename)
            if os.path.exists(zip_path):
                os.remove(zip_path)
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Job not found")

@app.get("/api/progress/{job_id}")
async def get_progress(job_id: str):
    async def event_generator():
        while True:
            if job_id not in jobs:
                yield f"data: {json.dumps({'error': 'Not found'})}\n\n"
                break
            
            job = jobs[job_id]
            yield f"data: {json.dumps(job)}\n\n"
            
            if job["status"] in ["Completed", "Error"]:
                break
            
            await asyncio.sleep(0.5)
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/download/{job_id}")
async def public_download(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.get("file"):
        return StreamingResponse(iter(["File not found or processing not complete"]), status_code=404)
    
    file_path = os.path.join(DOWNLOAD_DIR, job["file"])
    if not os.path.exists(file_path):
        return StreamingResponse(iter(["File missing"]), status_code=404)
    
    filename = job["file"]
    if job.get("is_zip"):
        clean_title = "".join(x for x in job.get("playlist_title", "download") if x.isalnum() or x in "._- ")
        filename = f"{clean_title}.zip"
        
    return FileResponse(file_path, filename=filename)

@app.get("/api/download/{job_id}")
async def download_file(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.get("file"):
        raise HTTPException(status_code=404, detail="File not found or processing not complete")
    
    file_path = os.path.join(DOWNLOAD_DIR, job["file"])
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Zip file missing")
    
    clean_title = "".join(x for x in job.get("playlist_title", "download") if x.isalnum() or x in "._- ")
    return FileResponse(file_path, filename=f"{clean_title}.zip", media_type="application/zip")

# Ensure static directory exists
os.makedirs("static", exist_ok=True)
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
