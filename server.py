#!/usr/bin/env python3
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
import shutil
from aiohttp import web, WSCloseCode

# ─── Configuration ────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 8080))
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_BROWSE_DIR = os.path.expanduser("~")

# ─── Tunnel Logic ─────────────────────────────────────────────────────────────
def start_ssh_tunnel():
    """Attempt to create an SSH tunnel for immediate access."""
    if not shutil.which("ssh"):
        print("\n⚠️ WARNING: 'ssh' executable not found on the system path. Cannot start SSH tunnel.")
        print("Please install an SSH client or expose port 8080 manually.\n")
        return

    cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-R", f"80:127.0.0.1:{PORT}", "nokey@localhost.run"]
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in iter(process.stdout.readline, ""):
            match = re.search(r"https://[a-zA-Z0-9.-]+\.lhr\.life", line)
            if match:
                url = match.group(0)
                print(f"\n🚀 IMMEDIATE ACCESS LINK: {url}\n")
                with open('tunnel_url.txt', 'w') as f: f.write(url)
                break
    except Exception as e:
        print(f"Tunnel failed: {e}")

# ─── Session Management ──────────────────────────────────────────────────────
active_sessions = {}   # session_id -> { "host": ws, "client": ws, "host_name": str, "client_name": str }
device_registry = {}   # access_code -> { "ws": ws, "device_name": str, "status": dict }
ws_connections = {}    # ws -> { "access_code": str, "role": str }

def get_device_stats():
    import platform
    stats = {"cpu": 0, "ram": 0, "battery": 100, "platform": platform.system()}
    try:
        import psutil
        stats["cpu"] = psutil.cpu_percent()
        stats["ram"] = psutil.virtual_memory().percent
        battery = psutil.sensors_battery()
        if battery:
            stats["battery"] = battery.percent
    except Exception as e:
        print(f"Stats error (likely missing psutil): {e}")
    return stats

def generate_access_code():
    return str(uuid.uuid4().int)[:6].zfill(6)

def generate_session_id():
    return hashlib.sha256(f"{time.time()}-{uuid.uuid4()}".encode()).hexdigest()[:16]

KEY_MAPPING = {
    "Enter": "enter",
    "Backspace": "backspace",
    "Tab": "tab",
    "Escape": "escape",
    "ArrowUp": "up",
    "ArrowDown": "down",
    "ArrowLeft": "left",
    "ArrowRight": "right",
    "Delete": "delete",
    "Home": "home",
    "End": "end",
    "PageUp": "pageup",
    "PageDown": "pagedown",
    " ": "space",
}

# ─── WebSocket Handler ──────────────────────────────────────────────
async def websocket_handler(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    connection_id = str(uuid.uuid4())[:8]
    print(f"📡 New WebSocket connection: {connection_id}")

    async for msg in ws:
        if msg.type == web.WSMsgType.TEXT:
            try:
                data = json.loads(msg.data)
                msg_type = data.get("type")

                if msg_type == "register":
                    access_code = generate_access_code()
                    device_name = data.get("device_name", "Unknown Device")
                    device_registry[access_code] = {
                        "ws": ws,
                        "device_name": device_name,
                        "status": get_device_stats()
                    }
                    ws_connections[ws] = {"access_code": access_code, "role": "host", "device_name": device_name}
                    await ws.send_json({"type": "registered", "access_code": access_code, "device_name": device_name, "message": f"Registered as {device_name}"})

                elif msg_type == "connect":
                    target_code = data.get("access_code")
                    client_name = data.get("device_name", "Remote User")
                    if target_code in device_registry:
                        host_info = device_registry[target_code]
                        session_id = generate_session_id()
                        active_sessions[session_id] = {"host": host_info["ws"], "client": ws, "host_name": host_info["device_name"], "client_name": client_name}
                        ws_connections[ws] = {"session_id": session_id, "role": "client", "device_name": client_name}

                        await ws.send_json({"type": "connected", "session_id": session_id, "host_name": host_info["device_name"]})
                        await host_info["ws"].send_json({"type": "incoming_connection", "session_id": session_id, "client_name": client_name})
                    else:
                        await ws.send_json({"type": "error", "message": "Invalid access code"})

                elif msg_type == "accept_connection":
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        session = active_sessions[sid]
                        session_token = uuid.uuid4().hex
                        session["session_token"] = session_token
                        await session["client"].send_json({
                            "type": "connection_accepted",
                            "session_id": sid,
                            "session_token": session_token,
                            "message": "Host accepted!"
                        })
                        await session["host"].send_json({
                            "type": "connection_accepted",
                            "session_id": sid,
                            "session_token": session_token,
                            "message": "You accepted!"
                        })

                elif msg_type == "local_control":
                    info = ws_connections.get(ws)
                    if info and info.get("role") == "host":
                        action = data.get("action")
                        try:
                            import pyautogui
                            pyautogui.FAILSAFE = True
                            if action == "mouse_move":
                                x = data.get("x")
                                y = data.get("y")
                                if x is not None and y is not None:
                                    w, h = pyautogui.size()
                                    target_x = int(x * w)
                                    target_y = int(y * h)
                                    pyautogui.moveTo(target_x, target_y)
                            elif action == "mouse_click":
                                button = data.get("button", "left")
                                if button in ["left", "right", "middle"]:
                                    pyautogui.click(button=button)
                            elif action == "key_down":
                                key = data.get("key")
                                if key:
                                    mapped_key = KEY_MAPPING.get(key, key.lower() if len(key) > 1 else key)
                                    pyautogui.press(mapped_key)
                        except Exception as e:
                            print(f"Error executing local control {action}: {e}")

                elif msg_type in ["offer", "answer", "ice-candidate", "chat", "remote_input"]:
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        session = active_sessions[sid]
                        target = session["client"] if ws == session["host"] else session["host"]
                        await target.send_json(data)

            except Exception as e:
                print(f"Error: {e}")
        elif msg.type == web.WSMsgType.ERROR:
            print(f"WS connection closed with exception {ws.exception()}")

    # Cleanup
    if ws in ws_connections:
        info = ws_connections[ws]
        if info.get("role") == "host":
            code = info.get("access_code")
            if code in device_registry: del device_registry[code]

        # End any sessions
        for sid, session in list(active_sessions.items()):
            if ws in [session["host"], session["client"]]:
                other = session["client"] if ws == session["host"] else session["host"]
                try: await other.send_json({"type": "peer_disconnected", "message": "Peer left"})
                except: pass
                del active_sessions[sid]
        del ws_connections[ws]

    return ws

# ─── API Routes ──────────────────────────────────────────────────────
async def get_status(request):
    return web.json_response({
        "server": "Access Pro",
        "active_devices": len(device_registry),
        "active_sessions": len(active_sessions)
    })

async def get_devices(request):
    # Mask access code for security - client must type code manually
    devices = [{"name": i["device_name"], "status": i["status"]} for c, i in device_registry.items()]
    return web.json_response(devices)

async def browse_files(request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        return web.json_response({"error": "Unauthorized"}, status=401)
    token = auth_header.split(" ")[1]
    
    session_valid = False
    for session in active_sessions.values():
        if session.get("session_token") == token:
            session_valid = True
            break
    if not session_valid:
        return web.json_response({"error": "Forbidden"}, status=403)

    path = request.query.get("path", ROOT_BROWSE_DIR)
    if not path:
        path = ROOT_BROWSE_DIR
    try:
        abs_path = os.path.abspath(path)
        abs_root = os.path.abspath(ROOT_BROWSE_DIR)
        if not abs_path.startswith(abs_root):
            return web.json_response({"error": "Access denied"}, status=403)

        items = []
        for entry in os.scandir(abs_path):
            items.append({"name": entry.name, "path": entry.path, "is_dir": entry.is_dir(), "size": entry.stat().st_size if not entry.is_dir() else 0})
        return web.json_response({"current": abs_path, "items": items})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

async def download_file(request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        token = request.query.get("token")
        if not token:
            return web.json_response({"error": "Unauthorized"}, status=401)
    else:
        token = auth_header.split(" ")[1]

    session_valid = False
    for session in active_sessions.values():
        if session.get("session_token") == token:
            session_valid = True
            break
    if not session_valid:
        return web.json_response({"error": "Forbidden"}, status=403)

    path = request.query.get("path")
    if not path:
        return web.json_response({"error": "Path required"}, status=400)

    abs_path = os.path.abspath(path)
    abs_root = os.path.abspath(ROOT_BROWSE_DIR)
    if not abs_path.startswith(abs_root):
        return web.json_response({"error": "Access denied"}, status=403)

    if not os.path.isfile(abs_path):
        return web.json_response({"error": "File not found"}, status=404)

    return web.FileResponse(abs_path)

# ─── App Setup ──────────────────────────────────────────────────────
app = web.Application()
app.add_routes([
    web.get('/ws', websocket_handler),
    web.get('/api/status', get_status),
    web.get('/api/devices', get_devices),
    web.get('/api/files', browse_files),
    web.get('/api/download', download_file),
    web.static('/', STATIC_DIR, show_index=True)
])

if __name__ == "__main__":
    import sys
    import io
    # Handle Windows encoding issues
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    print(f"Access Pro starting on port {PORT}...")

    # Start tunnel in background for immediate link if not on Render
    if not os.environ.get("RENDER"):
        threading.Thread(target=start_ssh_tunnel, daemon=True).start()
    else:
        print("Running on Render. Public tunnel startup skipped.")

    web.run_app(app, host='0.0.0.0', port=PORT)
