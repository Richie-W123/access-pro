#!/usr/bin/env python3
import asyncio
import json
import os
import uuid
import time
import hashlib
import sys
import io
from aiohttp import web, ClientSession

# --- Monitoring: Sentry (Safe Load) ---
try:
    import sentry_sdk
    from sentry_sdk.integrations.aiohttp import AioHttpIntegration
    SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
    if SENTRY_DSN:
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            integrations=[AioHttpIntegration()],
            traces_sample_rate=1.0
        )
except ImportError:
    pass

# --- Configuration ---
PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_BROWSE_DIR = os.path.abspath(os.path.expanduser("~") if not os.environ.get("RENDER") else BASE_DIR)
WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")

# --- System Compatibility ---
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# --- Optional Dependencies ---
try:
    import psutil
except ImportError:
    psutil = None

try:
    import pyautogui
    pyautogui.FAILSAFE = True
except:
    pyautogui = None

# --- Global State ---
active_sessions = {}
device_registry = {}
ws_connections = {}

# --- Helper: Async Alert ---
async def send_alert_async(message):
    if not WEBHOOK_URL: return
    try:
        async with ClientSession() as session:
            async with session.post(WEBHOOK_URL, json={"text": f"🚨 *Access Pro*: {message}"}) as resp:
                pass
    except: pass

def get_device_stats():
    import platform
    stats = {"cpu": 0, "ram": 0, "battery": 100, "platform": platform.system()}
    if psutil:
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
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                data = json.loads(msg.data)
                mtype = data.get("type")

                if mtype == "register":
                    code = generate_access_code()
                    name = data.get("device_name", "Unknown Device")
                    device_registry[code] = {
                        "ws": ws,
                        "device_name": name,
                        "status": get_device_stats(),
                        "last_seen": time.time()
                    }
                    ws_connections[ws] = {"code": code, "role": "host"}
                    await ws.send_json({"type": "registered", "access_code": code, "device_name": name})
                    asyncio.create_task(send_alert_async(f"New Device: *{name}* (`{code}`)"))

                elif mtype == "heartbeat":
                    if ws in ws_connections:
                        code = ws_connections[ws].get("code")
                        if code in device_registry:
                            device_registry[code]["last_seen"] = time.time()
                            device_registry[code]["status"] = get_device_stats()

                elif mtype == "connect":
                    code = data.get("access_code")
                    if code in device_registry:
                        host = device_registry[code]
                        sid = generate_session_id()
                        active_sessions[sid] = {"host": host["ws"], "client": ws, "token": os.urandom(16).hex()}
                        ws_connections[ws] = {"session_id": sid, "role": "client"}
                        await ws.send_json({"type": "connected", "session_id": sid, "host_name": host["device_name"]})
                        await host["ws"].send_json({"type": "incoming_connection", "session_id": sid, "client_name": data.get("device_name", "User")})

                elif mtype == "accept_connection":
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        token = active_sessions[sid]["token"]
                        msg = {"type": "connection_accepted", "session_id": sid, "session_token": token}
                        await active_sessions[sid]["client"].send_json(msg)
                        await active_sessions[sid]["host"].send_json(msg)
                        asyncio.create_task(send_alert_async(f"Session Started: `{sid[:8]}`"))

                elif mtype == "local_control" and pyautogui:
                    try:
                        action = data.get("action")
                        if action == "mouse_move":
                            w, h = pyautogui.size()
                            pyautogui.moveTo(int(data["x"] * w), int(data["y"] * h))
                        elif action == "mouse_click":
                            pyautogui.click(button=data.get("button", "left"))
                        elif action == "key_down":
                            pyautogui.press(data.get("key", "").lower())
                    except: pass

                elif mtype in ["offer", "answer", "ice-candidate", "chat", "remote_input", "ring", "clipboard"]:
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        sess = active_sessions[sid]
                        target = sess["client"] if ws == sess["host"] else sess["host"]
                        if not target.closed: await target.send_json(data)
    finally:
        if ws in ws_connections:
            info = ws_connections[ws]
            if info.get("role") == "host":
                code = info.get("code")
                if code in device_registry: del device_registry[code]
            for sid, sess in list(active_sessions.items()):
                if ws in (sess["host"], sess["client"]):
                    other = sess["client"] if ws == sess["host"] else sess["host"]
                    if not other.closed:
                        try: await other.send_json({"type": "peer_disconnected"})
                        except: pass
                    del active_sessions[sid]
            del ws_connections[ws]
    return ws

# --- API Routes ---
async def api_status(request):
    return web.json_response({"status": "online", "devices": len(device_registry)})

async def api_devices(request):
    now = time.time()
    return web.json_response([
        {"code": k, "name": v["device_name"], "status": v["status"], "is_online": (now - v["last_seen"]) < 30}
        for k, v in device_registry.items()
    ])

async def api_files(request):
    auth = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not any(s["token"] == auth for s in active_sessions.values()):
        return web.json_response({"error": "Unauthorized"}, status=401)
    path = request.query.get("path", ROOT_BROWSE_DIR)
    if not os.path.exists(path): path = ROOT_BROWSE_DIR
    items = [{"name": e.name, "path": e.path, "is_dir": e.is_dir(), "size": e.stat().st_size if e.is_file() else 0} for e in os.scandir(path)]
    return web.json_response({"current": path, "items": items})

async def index_handler(request):
    return web.FileResponse(os.path.join(BASE_DIR, 'index.html'))

# --- App Setup ---
def create_app():
    app = web.Application()
    app.add_routes([
        web.get('/', index_handler),
        web.get('/ws', websocket_handler),
        web.get('/api/status', api_status),
        web.get('/api/devices', api_devices),
        web.get('/api/files', api_files),
        web.static('/', BASE_DIR)
    ])
    return app

app = create_app()

if __name__ == "__main__":
    web.run_app(app, host='0.0.0.0', port=PORT)
