#!/usr/bin/env python3
import asyncio
import json
import os
import sys
import uuid
import time
import hashlib
from aiohttp import web

# ─── Configuration ────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 8080))
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Session Management ──────────────────────────────────────────────────────
active_sessions = {}
device_registry = {}
ws_connections = {}

def get_device_stats():
    # Fallback stats if psutil is not available
    import platform
    return {
        "cpu": 0,
        "ram": 0,
        "battery": 100,
        "platform": platform.system(),
        "cloud": "RENDER" if os.environ.get("RENDER") else "LOCAL"
    }

def generate_access_code():
    return str(uuid.uuid4().int)[:6].zfill(6)

def generate_session_id():
    return hashlib.sha256(f"{time.time()}-{uuid.uuid4()}".encode()).hexdigest()[:16]

# ─── WebSocket Handler ──────────────────────────────────────────────
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    conn_id = str(uuid.uuid4())[:8]
    print(f"📡 Connection: {conn_id}")

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                mtype = data.get("type")

                if mtype == "register":
                    code = generate_access_code()
                    name = data.get("device_name", "Device")
                    device_registry[code] = {"ws": ws, "device_name": name, "status": get_device_stats()}
                    ws_connections[ws] = {"code": code, "role": "host"}
                    await ws.send_json({"type": "registered", "access_code": code, "device_name": name})

                elif mtype == "connect":
                    code = data.get("access_code")
                    if code in device_registry:
                        host = device_registry[code]
                        sid = generate_session_id()
                        active_sessions[sid] = {"host": host["ws"], "client": ws}
                        await ws.send_json({"type": "connected", "session_id": sid, "host_name": host["device_name"]})
                        await host["ws"].send_json({"type": "incoming_connection", "session_id": sid, "client_name": data.get("device_name", "User")})
                    else:
                        await ws.send_json({"type": "error", "message": "Invalid Code"})

                elif mtype == "accept_connection":
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        await active_sessions[sid]["client"].send_json({"type": "connection_accepted", "session_id": sid})
                        await active_sessions[sid]["host"].send_json({"type": "connection_accepted", "session_id": sid})

                elif mtype in ["offer", "answer", "ice-candidate", "chat", "remote_input"]:
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        sess = active_sessions[sid]
                        target = sess["client"] if ws == sess["host"] else sess["host"]
                        await target.send_json(data)

            except Exception as e: print(f"WS Error: {e}")

    # Cleanup
    if ws in ws_connections:
        code = ws_connections[ws].get("code")
        if code in device_registry: del device_registry[code]
        del ws_connections[ws]
    return ws

# ─── API & Routes ──────────────────────────────────────────────────────
async def handle_index(request):
    return web.FileResponse(os.path.join(STATIC_DIR, 'index.html'))

app = web.Application()
app.add_routes([
    web.get('/', handle_index),
    web.get('/ws', websocket_handler),
    web.get('/api/status', lambda r: web.json_response({"status": "online"})),
    web.get('/api/devices', lambda r: web.json_response([{"name": v["device_name"], "status": v["status"]} for k,v in device_registry.items()])),
    web.static('/', STATIC_DIR)
])

if __name__ == "__main__":
    print(f"Starting Access Pro on port {PORT}...")
    web.run_app(app, host='0.0.0.0', port=PORT)
