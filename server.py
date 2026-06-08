#!/usr/bin/env python3
import asyncio
import json
import os
import uuid
import time
import hashlib
from aiohttp import web

# --- Configuration ---
PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Session Management ---
active_sessions = {}
device_registry = {}
ws_connections = {}

def get_device_stats():
    # Safe fallback for cloud environments
    return {
        "cpu": 0, "ram": 0, "battery": 100,
        "platform": "Cloud",
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
                    ws_connections[ws] = {"code": code}
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
            except: pass

    if ws in ws_connections:
        code = ws_connections[ws].get("code")
        if code in device_registry: del device_registry[code]
        del ws_connections[ws]
    return ws

# --- App Setup ---
async def index_handler(request):
    return web.FileResponse(os.path.join(BASE_DIR, 'index.html'))

app = web.Application()
app.router.add_get('/', index_handler)
app.router.add_get('/ws', websocket_handler)
app.router.add_get('/api/status', lambda r: web.json_response({"status": "online"}))
app.router.add_get('/api/devices', lambda r: web.json_response([{"code": k, "name": v["device_name"], "status": v["status"]} for k,v in device_registry.items()]))
app.router.add_static('/', path=BASE_DIR, name='static')

if __name__ == "__main__":
    print(f"Starting server on 0.0.0.0:{PORT}")
    web.run_app(app, host='0.0.0.0', port=PORT)
