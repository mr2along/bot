# Shopee Video Downloader Web App

Web app for Android Chrome: paste or share a Shopee video link, resolve the page, extract a downloadable video URL, preview it, and download the MP4.

## Architecture

- `frontend/` — static mobile-first web UI.
- `backend/` — FastAPI service using `yt-dlp` for supported Shopee pages and a lightweight HTML fallback.
- The browser never needs direct CORS access to Shopee; the backend performs extraction.

## Run locally

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

Serve `frontend/` with any static server and set `window.SHOPEE_API_BASE` to the backend URL, or open it from the same deployment.

For a quick local test:

```bash
cd frontend
python -m http.server 5500
```

Then open `http://localhost:5500`.

## Android share flow

The web app supports a `?url=` query parameter. If Android/Chrome shares a URL to a launcher that opens this page with the URL parameter, the app automatically fills and starts resolving it.

Example:

`https://your-domain.example/?url=https%3A%2F%2Fvn.shp.ee%2Fg3y8l4fz%3Fsmtt%3D0.0.9`

## Notes

- Only download videos you have permission to download/use. Respect Shopee's terms, seller rights, and applicable copyright rules.
- Shopee can change its page/API format. The extractor therefore reports clear errors rather than pretending a URL was found.
- The backend limits input to Shopee domains and does not act as an arbitrary URL proxy.
