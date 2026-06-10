#!/usr/bin/env python3
"""
RemoteLink Pro - Final Corrected Server
Restores all features (Files, Control, Stats) with cloud-safe stability.
"""

import asyncio
import json
import os
import uuid
import time
import hashlib
import sys
import io
from aiohttp import web

# --- Force UTF-8 for Windows ---
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# --- Optional Dependencies ---
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# --- Configuration ---
PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# On Render, we'll limit browsing to the project dir for safety.
# Locally, it can be the user home.
ROOT_BROWSE_DIR = os.path.expanduser("~") if not os.environ.get("RENDER") else BASE_DIR

# --- Session Management ---
active_sessions = {}   # session_id -> { "host": ws, "client": ws, "token": str, "host_name": str }
device_registry = {}   # access_code -> { "ws": ws, "device_name": str, "status": dict }
ws_connections = {}    # ws -> { "code": str, "role": str }

def get_device_stats():
    import platform
    stats = {"cpu": 0, "ram": 0, "battery": 100, "platform": platform.system()}
    if HAS_PSUTIL:
        try:
            stats["cpu"] = psutil.cpu_percent()
            stats["ram"] = psutil.virtual_memory().percent
            batt = psutil.sensors_battery()
            if batt: stats["battery"] = batt.percent
        except: pass
    return stats

def generate_access_code():
    return str(uuid.uuid4().int)[:6].zfill(6)

def generate_session_id():
    return hashlib.sha256(f"{time.time()}-{uuid.uuid4()}".encode()).hexdigest()[:16]

# --- WebSocket Handler ---
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    conn_id = str(uuid.uuid4())[:8]
    print(f"📡 WebSocket Connected: {conn_id}")

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                mtype = data.get("type")

                if mtype == "register":
                    code = generate_access_code()
                    name = data.get("device_name", "Device")
                    device_registry[code] = {
                        "ws": ws,
                        "device_name": name,
                        "status": get_device_stats()
                    }
                    ws_connections[ws] = {"code": code, "role": "host"}
                    await ws.send_json({
                        "type": "registered",
                        "access_code": code,
                        "device_name": name,
                        "message": f"Registered as {name}"
                    })

                elif mtype == "connect":
                    code = data.get("access_code")
                    client_name = data.get("device_name", "Remote User")
                    if code in device_registry:
                        host = device_registry[code]
                        sid = generate_session_id()
                        active_sessions[sid] = {"host": host["ws"], "client": ws, "host_name": host["device_name"]}
                        ws_connections[ws] = {"session_id": sid, "role": "client"}
                        await ws.send_json({"type": "connected", "session_id": sid, "host_name": host["device_name"]})
                        await host["ws"].send_json({"type": "incoming_connection", "session_id": sid, "client_name": client_name})
                    else:
                        await ws.send_json({"type": "error", "message": "Device not found"})

                elif mtype == "accept_connection":
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        token = hashlib.sha256(os.urandom(16)).hexdigest()
                        active_sessions[sid]["token"] = token
                        msg = {
                            "type": "connection_accepted",
                            "session_id": sid,
                            "session_token": token,
                            "message": "Connection Established"
                        }
                        await active_sessions[sid]["client"].send_json(msg)
                        await active_sessions[sid]["host"].send_json(msg)

                elif mtype in ["offer", "answer", "ice-candidate", "chat", "remote_input", "ring", "clipboard"]:
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        sess = active_sessions[sid]
                        target = sess["client"] if ws == sess["host"] else sess["host"]
                        await target.send_json(data)
    except Exception as e:
        print(f"WS Error: {e}")
    finally:
        if ws in ws_connections:
            info = ws_connections[ws]
            if "code" in info:
                code = info["code"]
                if code in device_registry: del device_registry[code]

            for sid, sess in list(active_sessions.items()):
                if ws in [sess["host"], sess["client"]]:
                    other = sess["client"] if ws == sess["host"] else sess["host"]
                    try: await other.send_json({"type": "peer_disconnected", "message": "Peer Disconnected"})
                    except: pass
                    del active_sessions[sid]
            del ws_connections[ws]
    return ws

# --- API Routes ---
async def api_status(request):
    return web.json_response({"status": "online", "devices": len(device_registry)})

async def api_devices(request):
    return web.json_response([
        {"code": k, "name": v["device_name"], "status": v["status"]}
        for k, v in device_registry.items()
    ])

async def api_files(request):
    # Verify session via token
    auth = request.headers.get("Authorization")
    token = auth.split(" ")[1] if auth and auth.startswith("Bearer ") else request.query.get("token")

    if not any(s.get("token") == token for s in active_sessions.values()):
        return web.json_response({"error": "Unauthorized"}, status=401)

    path = request.query.get("path", ROOT_BROWSE_DIR)
    if not path or not os.path.exists(path): path = ROOT_BROWSE_DIR

    try:
        items = []
        for e in os.scandir(path):
            items.append({
                "name": e.name,
                "path": e.path,
                "is_dir": e.is_dir(),
                "size": e.stat().st_size if e.is_file() else 0
            })
        return web.json_response({"current": path, "items": items})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_download(request):
    token = request.query.get("token")
    if not any(s.get("token") == token for s in active_sessions.values()):
        return web.Response(text="Unauthorized", status=401)

    path = request.query.get("path")
    if path and os.path.exists(path) and os.path.isfile(path):
        return web.FileResponse(path)
    return web.Response(text="File not found", status=404)

# --- Static File Serving ---
async def index_handler(request):
    return web.FileResponse(os.path.join(BASE_DIR, 'index.html'))

app = web.Application()
app.add_routes([
    web.get('/', index_handler),
    web.get('/ws', websocket_handler),
    web.get('/api/status', api_status),
    web.get('/api/devices', api_devices),
    web.get('/api/files', api_files),
    web.get('/api/download', api_download),
    web.static('/', BASE_DIR)
])

if __name__ == "__main__":
    print(f"🚀 Access Pro starting on 0.0.0.0:{PORT}")
    web.run_app(app, host='0.0.0.0', port=PORT)
