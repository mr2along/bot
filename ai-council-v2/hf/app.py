import os, uuid
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AI Council V2 Relay")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
rooms: Dict[str, Set[WebSocket]] = {}

async def broadcast(room, msg, exclude=None):
    peers = rooms.get(room, set()); dead=[]
    for ws in list(peers):
        if ws is exclude: continue
        try: await ws.send_json(msg)
        except Exception: dead.append(ws)
    for ws in dead: peers.discard(ws)

@app.get("/")
async def root(): return HTMLResponse("<h2>AI Council V2 WebSocket Relay</h2><p>Use /ws and /health.</p>")

@app.get("/health")
async def health(): return {"ok": True, "rooms": len(rooms), "connections": sum(len(x) for x in rooms.values())}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    room = ws.query_params.get("room_id", "default")
    client = ws.query_params.get("client_id") or str(uuid.uuid4())
    role = ws.query_params.get("role", "client")
    rooms.setdefault(room, set()).add(ws)
    await ws.send_json({"type":"connected","room_id":room,"client_id":client,"role":role})
    await broadcast(room, {"type":"presence","event":"join","room_id":room,"client_id":client,"role":role}, ws)
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ping":
                await ws.send_json({"type":"pong","timestamp":data.get("timestamp")}); continue
            await broadcast(room, {"type":"relay","room_id":room,"client_id":client,"role":role,"data":data}, ws)
    except WebSocketDisconnect: pass
    finally:
        peers=rooms.get(room,set()); peers.discard(ws)
        if not peers: rooms.pop(room,None)
        await broadcast(room, {"type":"presence","event":"leave","room_id":room,"client_id":client,"role":role})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT","7860")))
