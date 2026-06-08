#!/usr/bin/env python3
"""
RemoteLink Pro - Robust Signaling & Static Server
Supports WebRTC signaling, Remote Input Control, and File Browsing.
"""

import asyncio
import json
import os
import sys
import uuid
import time
import hashlib
import threading
import subprocess
import re
import io
from aiohttp import web

# --- Optional Dependencies ---
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import pyautogui
    HAS_PYAUTOGUI = True
    pyautogui.FAILSAFE = True
except:
    HAS_PYAUTOGUI = False

# --- Configuration ---
PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- Unicode Fix for Windows ---
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# --- Session Management ---
active_sessions = {}   # session_id -> { "host": ws, "client": ws, "token": str }
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

KEY_MAPPING = {
    "Enter": "enter", "Backspace": "backspace", "Tab": "tab", "Escape": "escape",
    "ArrowUp": "up", "ArrowDown": "down", "ArrowLeft": "left", "ArrowRight": "right",
    " ": "space"
}

# --- WebSocket Signaling ---
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
                    name = data.get("device_name", "Unknown Device")
                    device_registry[code] = {"ws": ws, "device_name": name, "status": get_device_stats()}
                    ws_connections[ws] = {"code": code, "role": "host"}
                    await ws.send_json({"type": "registered", "access_code": code, "device_name": name, "message": "Successfully Registered"})
                    print(f"🖥️ Device Registered: {name} ({code})")

                elif mtype == "connect":
                    code = data.get("access_code")
                    if code in device_registry:
                        host = device_registry[code]
                        sid = generate_session_id()
                        active_sessions[sid] = {"host": host["ws"], "client": ws}
                        ws_connections[ws] = {"session_id": sid, "role": "client"}
                        await ws.send_json({"type": "connected", "session_id": sid, "host_name": host["device_name"]})
                        await host["ws"].send_json({"type": "incoming_connection", "session_id": sid, "client_name": data.get("device_name", "Remote User")})
                    else:
                        await ws.send_json({"type": "error", "message": "Device Not Found"})

                elif mtype == "accept_connection":
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        token = hashlib.sha256(os.urandom(16)).hexdigest()
                        active_sessions[sid]["token"] = token
                        msg = {"type": "connection_accepted", "session_id": sid, "session_token": token, "message": "Connection Accepted"}
                        await active_sessions[sid]["client"].send_json(msg)
                        await active_sessions[sid]["host"].send_json(msg)

                elif mtype == "local_control" and HAS_PYAUTOGUI:
                    action = data.get("action")
                    try:
                        if action == "mouse_move":
                            w, h = pyautogui.size()
                            pyautogui.moveTo(int(data["x"] * w), int(data["y"] * h))
                        elif action == "mouse_click":
                            pyautogui.click(button=data.get("button", "left"))
                        elif action == "key_down":
                            key = data.get("key")
                            pyautogui.press(KEY_MAPPING.get(key, key.lower()))
                    except Exception as e: print(f"Control Error: {e}")

                elif mtype in ["offer", "answer", "ice-candidate", "chat", "remote_input", "ring", "clipboard"]:
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        sess = active_sessions[sid]
                        target = sess["client"] if ws == sess["host"] else sess["host"]
                        await target.send_json(data)
    except Exception as e:
        print(f"WS Exception: {e}")
    finally:
        # Cleanup
        if ws in ws_connections:
            info = ws_connections[ws]
            if "code" in info:
                code = info["code"]
                if code in device_registry: del device_registry[code]

            # Close sessions
            for sid, sess in list(active_sessions.items()):
                if ws in [sess["host"], sess["client"]]:
                    other = sess["client"] if ws == sess["host"] else sess["host"]
                    try: await other.send_json({"type": "peer_disconnected", "message": "Peer Disconnected"})
                    except: pass
                    del active_sessions[sid]
            del ws_connections[ws]
        print(f"🔌 WebSocket Closed: {conn_id}")
    return ws

# --- API Endpoints ---
async def api_status(request):
    return web.json_response({"status": "online", "devices": len(device_registry), "sessions": len(active_sessions)})

async def api_devices(request):
    devs = [{"code": k, "name": v["device_name"], "status": v["status"]} for k,v in device_registry.items()]
    return web.json_response(devs)

async def api_files(request):
    path = request.query.get("path", BASE_DIR)
    if not os.path.exists(path) or not os.path.abspath(path).startswith(os.path.abspath(BASE_DIR)):
        path = BASE_DIR
    try:
        items = []
        for e in os.scandir(path):
            items.append({"name": e.name, "path": e.path, "is_dir": e.is_dir(), "size": e.stat().st_size if e.is_file() else 0})
        return web.json_response({"current": path, "items": items})
    except Exception as e: return web.json_response({"error": str(e)}, status=500)

async def index_handler(request):
    return web.FileResponse(os.path.join(BASE_DIR, 'index.html'))

# --- App Setup ---
app = web.Application()
app.router.add_get('/', index_handler)
app.router.add_get('/ws', websocket_handler)
app.router.add_get('/api/status', api_status)
app.router.add_get('/api/devices', api_devices)
app.router.add_get('/api/files', api_files)

# Serve static files like app.js and style.css
app.router.add_static('/', path=BASE_DIR, name='static')

def start_tunnel():
    """Start localhost.run tunnel in background."""
    if os.environ.get("RENDER"): return
    def run():
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-R", f"80:127.0.0.1:{PORT}", "nokey@localhost.run"]
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in iter(proc.stdout.readline, ""):
                m = re.search(r"https://[a-zA-Z0-9.-]+\.lhr\.life", line)
                if m:
                    print(f"\n🌍 PUBLIC LINK: {m.group(0)}\n")
                    with open("tunnel_url.txt", "w") as f: f.write(m.group(0))
                    break
        except: pass
    threading.Thread(target=run, daemon=True).start()

if __name__ == "__main__":
    print(f"🚀 RemoteLink Pro starting on 0.0.0.0:{PORT}")
    start_tunnel()
    web.run_app(app, host='0.0.0.0', port=PORT)
