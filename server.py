#!/usr/bin/env python3
import asyncio
import json
import os
import uuid
import time
import hashlib
import sys
import io
from aiohttp import web

# --- Force UTF-8 for Windows compatibility ---
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# --- Configuration ---
PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Session Management ---
active_sessions = {}   # session_id -> { "host": ws, "client": ws, "host_name": str }
device_registry = {}   # access_code -> { "ws": ws, "device_name": str, "status": dict }
ws_connections = {}    # ws -> { "access_code": str, "role": str }

def get_device_stats():
    # Minimal stats to avoid dependency issues on cloud
    import platform
    return {
        "cpu": 0, "ram": 0, "battery": 100,
        "platform": platform.system(),
        "uptime": int(time.time())
    }

def generate_access_code():
    return str(uuid.uuid4().int)[:6].zfill(6)

def generate_session_id():
    return hashlib.sha256(f"{time.time()}-{uuid.uuid4()}".encode()).hexdigest()[:16]

# --- WebSocket Handler ---
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
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
                    await ws.send_json({"type": "registered", "access_code": code, "device_name": name})

                elif mtype == "connect":
                    code = data.get("access_code")
                    if code in device_registry:
                        host = device_registry[code]
                        sid = generate_session_id()
                        active_sessions[sid] = {"host": host["ws"], "client": ws, "host_name": host["device_name"]}
                        ws_connections[ws] = {"session_id": sid, "role": "client"}
                        await ws.send_json({"type": "connected", "session_id": sid, "host_name": host["device_name"]})
                        await host["ws"].send_json({"type": "incoming_connection", "session_id": sid, "client_name": data.get("device_name", "User")})
                    else:
                        await ws.send_json({"type": "error", "message": "Invalid Code"})

                elif mtype == "accept_connection":
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        await active_sessions[sid]["client"].send_json({"type": "connection_accepted", "session_id": sid})
                        await active_sessions[sid]["host"].send_json({"type": "connection_accepted", "session_id": sid})

                elif mtype in ["offer", "answer", "ice-candidate", "chat", "remote_input", "ring", "clipboard"]:
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        sess = active_sessions[sid]
                        target = sess["client"] if ws == sess["host"] else sess["host"]
                        await target.send_json(data)
            except Exception as e:
                print(f"WS Error: {e}")

    # Cleanup
    if ws in ws_connections:
        info = ws_connections[ws]
        if "code" in info:
            code = info["code"]
            if code in device_registry: del device_registry[code]
        del ws_connections[ws]
    return ws

# --- API Routes ---
async def api_status(request):
    return web.json_response({"status": "online", "devices": len(device_registry)})

async def api_devices(request):
    devs = [{"code": k, "name": v["device_name"], "status": v["status"]} for k,v in device_registry.items()]
    return web.json_response(devs)

async def api_files(request):
    # Dummy file browser for cloud (Safety first)
    return web.json_response({"current": "/", "items": [{"name": "README.md", "path": "/README.md", "is_dir": False, "size": 1024}]})

# --- App Setup ---
async def index_handler(request):
    return web.FileResponse(os.path.join(BASE_DIR, 'index.html'))

app = web.Application()
app.router.add_get('/', index_handler)
app.router.add_get('/ws', websocket_handler)
app.router.add_get('/api/status', api_status)
app.router.add_get('/api/devices', api_devices)
app.router.add_get('/api/files', api_files)

# Serve static files
app.router.add_static('/', path=BASE_DIR, name='static')

if __name__ == "__main__":
    print(f"Starting Access Pro on 0.0.0.0:{PORT}")
    web.run_app(app, host='0.0.0.0', port=PORT)
