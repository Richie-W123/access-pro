#!/usr/bin/env python3
"""
Access Pro - Signaling & Static Server
A robust implementation for WebRTC signaling, file browsing, and device management.
"""

import asyncio
import json
import os
import sys
import uuid
import time
import hashlib
import io
from aiohttp import web

# --- System Compatibility ---
if sys.platform == "win32":
    # Ensure UTF-8 output on Windows
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# --- Configuration ---
PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# For safety on cloud, limit browsing to project dir. Locally, allow home.
ROOT_BROWSE_DIR = os.path.abspath(os.path.expanduser("~") if not os.environ.get("RENDER") else BASE_DIR)

# --- Global State ---
active_sessions = {}   # session_id -> { "host": ws, "client": ws, "token": str, "host_name": str }
device_registry = {}   # access_code -> { "ws": ws, "device_name": str, "status": dict }
ws_connections = {}    # ws -> { "code": str, "role": str, "session_id": str }

# --- Helpers ---
def get_device_stats():
    """Mock stats for the signaling server or relay info."""
    import platform
    stats = {
        "cpu": 0,
        "ram": 0,
        "battery": 100,
        "platform": platform.system(),
        "uptime": int(time.time()),
        "server_mode": "cloud" if os.environ.get("RENDER") else "local"
    }
    # Optional: If psutil is present, use it
    try:
        import psutil
        stats["cpu"] = psutil.cpu_percent()
        stats["ram"] = psutil.virtual_memory().percent
        batt = psutil.sensors_battery()
        if batt: stats["battery"] = batt.percent
    except:
        pass
    return stats

def generate_access_code():
    return str(uuid.uuid4().int)[:6].zfill(6)

def generate_session_id():
    return hashlib.sha256(f"{time.time()}-{uuid.uuid4()}".encode()).hexdigest()[:16]

def get_token_from_request(request):
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth.split(" ")[1]
    return request.query.get("token")

# --- WebSocket Signaling ---
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    conn_id = str(uuid.uuid4())[:8]
    print(f"[WS] Connected: {conn_id}")

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    mtype = data.get("type")

                    if mtype == "register":
                        code = generate_access_code()
                        name = data.get("device_name", "Unknown Device")
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
                            "message": f"Device registered with code: {code}"
                        })
                        print(f"[WS] Host Registered: {name} ({code})")

                    elif mtype == "connect":
                        code = data.get("access_code")
                        client_name = data.get("device_name", "Remote User")
                        if code in device_registry:
                            host = device_registry[code]
                            sid = generate_session_id()
                            active_sessions[sid] = {
                                "host": host["ws"],
                                "client": ws,
                                "host_name": host["device_name"],
                                "client_name": client_name
                            }
                            ws_connections[ws] = {"session_id": sid, "role": "client"}
                            # Notify both
                            await ws.send_json({
                                "type": "connected",
                                "session_id": sid,
                                "host_name": host["device_name"]
                            })
                            await host["ws"].send_json({
                                "type": "incoming_connection",
                                "session_id": sid,
                                "client_name": client_name
                            })
                        else:
                            await ws.send_json({"type": "error", "message": "Invalid access code"})

                    elif mtype == "accept_connection":
                        sid = data.get("session_id")
                        if sid in active_sessions:
                            # Create security token for this session
                            token = hashlib.sha256(os.urandom(16)).hexdigest()
                            active_sessions[sid]["token"] = token

                            accept_msg = {
                                "type": "connection_accepted",
                                "session_id": sid,
                                "session_token": token,
                                "message": "Connection accepted by host"
                            }
                            await active_sessions[sid]["client"].send_json(accept_msg)
                            await active_sessions[sid]["host"].send_json(accept_msg)
                            print(f"[WS] Session Started: {sid}")

                    elif mtype in ["offer", "answer", "ice-candidate", "chat", "remote_input", "ring", "clipboard"]:
                        sid = data.get("session_id")
                        if sid in active_sessions:
                            sess = active_sessions[sid]
                            # Relay to the other party
                            target = sess["client"] if ws == sess["host"] else sess["host"]
                            if not target.closed:
                                await target.send_json(data)

                    elif mtype == "disconnect":
                        # Handled by cleanup but can be explicit
                        pass

                except Exception as e:
                    print(f"[WS] Message Error: {e}")
            elif msg.type == web.WSMsgType.ERROR:
                print(f"[WS] Conn error: {ws.exception()}")
    finally:
        # Cleanup logic
        if ws in ws_connections:
            info = ws_connections[ws]
            if info.get("role") == "host":
                code = info.get("code")
                if code in device_registry:
                    del device_registry[code]
                    print(f"[WS] Host Unregistered: {code}")

            # Close active sessions
            for sid, sess in list(active_sessions.items()):
                if ws in (sess["host"], sess["client"]):
                    other = sess["client"] if ws == sess["host"] else sess["host"]
                    if not other.closed:
                        try:
                            await other.send_json({"type": "peer_disconnected", "message": "The remote peer disconnected."})
                        except: pass
                    del active_sessions[sid]
                    print(f"[WS] Session Ended: {sid}")
            del ws_connections[ws]
        print(f"[WS] Disconnected: {conn_id}")
    return ws

# --- API Routes ---
async def api_status(request):
    return web.json_response({
        "status": "online",
        "devices": len(device_registry),
        "sessions": len(active_sessions),
        "timestamp": time.time()
    })

async def api_devices(request):
    devs = []
    for code, info in device_registry.items():
        devs.append({
            "code": code,
            "name": info["device_name"],
            "status": info["status"]
        })
    return web.json_response(devs)

async def api_files(request):
    token = get_token_from_request(request)
    # Check if token belongs to any active session
    if not any(s.get("token") == token for s in active_sessions.values()):
        return web.json_response({"error": "Unauthorized session"}, status=401)

    path = request.query.get("path", ROOT_BROWSE_DIR)
    # Security: Ensure path is within bounds
    target_path = os.path.abspath(path)
    if not target_path.startswith(ROOT_BROWSE_DIR):
        target_path = ROOT_BROWSE_DIR

    try:
        items = []
        if os.path.exists(target_path) and os.path.isdir(target_path):
            for entry in os.scandir(target_path):
                items.append({
                    "name": entry.name,
                    "path": entry.path,
                    "is_dir": entry.is_dir(),
                    "size": entry.stat().st_size if entry.is_file() else 0,
                    "mtime": entry.stat().st_mtime
                })
        return web.json_response({"current": target_path, "items": items})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def api_download(request):
    token = get_token_from_request(request)
    if not any(s.get("token") == token for s in active_sessions.values()):
        return web.Response(text="Unauthorized", status=401)

    file_path = request.query.get("path")
    if file_path and os.path.exists(file_path) and os.path.isfile(file_path):
        # Security: check bounds
        if os.path.abspath(file_path).startswith(ROOT_BROWSE_DIR):
            return web.FileResponse(file_path)
    return web.Response(text="File not found", status=404)

# --- Static Serving ---
async def index_handler(request):
    return web.FileResponse(os.path.join(BASE_DIR, 'index.html'))

# --- Main App Setup ---
def create_app():
    app = web.Application()
    app.add_routes([
        web.get('/', index_handler),
        web.get('/ws', websocket_handler),
        web.get('/api/status', api_status),
        web.get('/api/devices', api_devices),
        web.get('/api/files', api_files),
        web.get('/api/download', api_download),
        # Static files last
        web.static('/', BASE_DIR)
    ])
    return app

if __name__ == "__main__":
    print(f"Starting Access Pro on 0.0.0.0:{PORT}")
    app = create_app()
    web.run_app(app, host='0.0.0.0', port=PORT)
