import json
import os
import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Dict

import yt_dlp
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from apscheduler.schedulers.asyncio import AsyncIOScheduler

app = FastAPI(title="FB Reels Downloader")
templates = Jinja2Templates(directory="templates")
scheduler = AsyncIOScheduler()

SETTINGS_FILE = "settings.json"
monitored_pages = {}
downloaded_reels = set()
download_tasks: Dict[str, dict] = {}  # task_id -> progress

DEFAULT_SETTINGS = {
    "download_dir": "/downloads",
    "quality": "best",
    "monitor_interval": 60,
    "max_reels": 50,
    "auto_monitor": False,
    "cookies_path": "",
    "dark_mode": True
}

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE) as f:
                return {**DEFAULT_SETTINGS, **json.load(f)}
        except:
            pass
    return DEFAULT_SETTINGS.copy()

def save_settings(settings):
    with open(SETTINGS_FILE, "w") as f:
        json.dump(settings, f, indent=2)

settings = load_settings()

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "", name)[:100]

async def fetch_reels(page_url: str, limit: int = 50) -> List[dict]:
    ydl_opts = {
        'quiet': True,
        'extract_flat': True,
        'playlistend': limit,
        'cookiefile': settings["cookies_path"] if settings["cookies_path"] else None
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(page_url + "/reels", download=False)
            reels = []
            for entry in info.get('entries', []):
                if entry:
                    reels.append({
                        'id': entry['id'],
                        'title': entry.get('title', 'Untitled Reel'),
                        'url': f"https://www.facebook.com/reel/{entry['id']}",
                        'upload_date': entry.get('upload_date') or datetime.now().strftime("%Y%m%d"),
                        'duration': entry.get('duration'),
                        'thumbnail': entry.get('thumbnail')
                    })
            return reels
        except Exception as e:
            return [{"error": str(e)}]

def download_reel(reel: dict, download_dir: str, quality: str, task_id: str):
    try:
        download_tasks[task_id]["progress"] = 10
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        ydl_opts = {
            'outtmpl': f'{download_dir}/{reel["upload_date"]}_{timestamp}_{{title}}.%(ext)s',
            'format': 'best' if quality == 'best' else 'bestvideo[height<=720]+bestaudio/best' if quality == 'hd' else 'worst',
            'merge_output_format': 'mp4',
            'progress_hooks': [lambda d: update_progress(d, task_id)],
            'quiet': False,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([reel['url']])
        download_tasks[task_id]["progress"] = 100
        download_tasks[task_id]["status"] = "Completed"
    except Exception as e:
        download_tasks[task_id]["status"] = f"Error: {str(e)}"

def update_progress(d, task_id):
    if d['status'] == 'downloading':
        p = d.get('_percent_str') or '0%'
        try:
            download_tasks[task_id]["progress"] = float(p.strip('%'))
        except:
            pass

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "settings": settings})

@app.get("/settings")
async def get_settings():
    return settings

@app.post("/settings")
async def update_settings(
    download_dir: str = Form(...), quality: str = Form(...),
    monitor_interval: int = Form(...), max_reels: int = Form(...),
    auto_monitor: bool = Form(False), cookies_path: str = Form(""),
    dark_mode: bool = Form(True)
):
    global settings
    settings = {**settings, **{
        "download_dir": download_dir, "quality": quality,
        "monitor_interval": monitor_interval, "max_reels": max_reels,
        "auto_monitor": auto_monitor, "cookies_path": cookies_path,
        "dark_mode": dark_mode
    }}
    save_settings(settings)
    return {"status": "✅ Settings saved!"}

@app.post("/fetch")
async def fetch_reels_endpoint(page_url: str = Form(...)):
    reels = await fetch_reels(page_url, settings["max_reels"])
    return {"reels": reels}

@app.post("/download")
async def download_selected(
    reel_urls: List[str] = Form(...),
    download_dir: str = Form(None),
    quality: str = Form(None)
):
    dir_to_use = download_dir or settings["download_dir"]
    qual_to_use = quality or settings["quality"]
    os.makedirs(dir_to_use, exist_ok=True)

    task_id = datetime.now().isoformat()
    download_tasks[task_id] = {"progress": 0, "status": "Starting...", "total": len(reel_urls), "done": 0}

    for url in reel_urls:
        # In real app you'd fetch full reel info; simplified here
        reel = {"url": url, "upload_date": datetime.now().strftime("%Y%m%d"), "title": "Reel"}
        asyncio.create_task(download_reel(reel, dir_to_use, qual_to_use, task_id))
        download_tasks[task_id]["done"] += 1

    return {"task_id": task_id}

@app.get("/progress/{task_id}")
async def get_progress(task_id: str):
    return download_tasks.get(task_id, {"progress": 100, "status": "Done"})

# Monitoring (same as before, uses settings)
async def monitor_page(...): ...

@app.post("/export-settings")
async def export_settings():
    return JSONResponse(settings, media_type="application/json", headers={"Content-Disposition": "attachment; filename=settings.json"})

# Import handled via form in frontend
