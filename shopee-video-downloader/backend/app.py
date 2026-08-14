import json
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

app = FastAPI(title="Shopee Video Resolver", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

ALLOWED_HOSTS = {"shopee.vn", "www.shopee.vn", "vn.shp.ee", "shopee.co.th", "shopee.com"}


class ResolveRequest(BaseModel):
    url: HttpUrl


def validate_shopee_url(value: str) -> str:
    parsed = urlparse(value)
    host = parsed.hostname.lower().rstrip(".") if parsed.hostname else ""
    if host not in ALLOWED_HOSTS and not host.endswith(".shopee.vn"):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ URL Shopee/Shopee short link.")
    return value


def run_ytdlp(url: str) -> dict:
    # Use yt-dlp for sites/extractors it supports. No arbitrary URL proxying.
    cmd = [
        "yt-dlp",
        "--dump-single-json",
        "--no-playlist",
        "--no-warnings",
        "--skip-download",
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except FileNotFoundError:
        return {}
    except subprocess.TimeoutExpired:
        return {}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def choose_video(info: dict) -> dict | None:
    if not info:
        return None
    formats = info.get("formats") or []
    candidates = [f for f in formats if f.get("url") and f.get("vcodec") not in (None, "none")]
    candidates.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
    chosen = candidates[0] if candidates else (info if info.get("url") else None)
    if not chosen:
        return None
    return {
        "url": chosen.get("url"),
        "title": info.get("title") or "Shopee video",
        "width": chosen.get("width") or info.get("width"),
        "height": chosen.get("height") or info.get("height"),
        "duration": info.get("duration"),
        "ext": chosen.get("ext") or info.get("ext") or "mp4",
    }


async def html_fallback(url: str) -> dict | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 Chrome/139 Mobile Safari/537.36"
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers=headers) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception:
        return None

    html = response.text
    # Common JSON/HTML video URL patterns. Decode escaped JSON characters.
    patterns = [
        r'"(?:video|play_url|video_url|src)"\s*:\s*"(https?:\\?/\\?/[^"\\]+\.(?:mp4|m3u8)[^"\\]*)"',
        r'<video[^>]+src=["\'](https?://[^"\']+\.(?:mp4|m3u8)[^"\']*)',
        r'(https?:\\?/\\?/[^"\'\\ ]+\.(?:mp4|m3u8)(?:\?[^"\'\\ ]*)?)',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            video_url = match.group(1).replace("\\/", "/").replace("\\u0026", "&")
            return {"url": video_url, "title": "Shopee video", "ext": "mp4"}
    return None


@app.get("/health")
def health():
    return {"ok": True, "service": "shopee-video-resolver"}


@app.post("/api/resolve")
async def resolve(request: ResolveRequest):
    url = validate_shopee_url(str(request.url))
    info = run_ytdlp(url)
    result = choose_video(info)
    if result:
        return result

    result = await html_fallback(url)
    if result:
        return result

    raise HTTPException(
        status_code=422,
        detail="Không tìm thấy URL video. Shopee có thể đã thay đổi cấu trúc trang hoặc video cần phiên đăng nhập.",
    )
