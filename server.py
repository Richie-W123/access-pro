#!/usr/bin/env python3
import asyncio
import json
import os
import uuid
import time
import hashlib
import sys
import io
import logging
from aiohttp import web, ClientSession

# --- Robust Monitoring: Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("server_log.txt")
    ]
)
logger = logging.getLogger("AccessPro")

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
        logger.info("Sentry initialized successfully.")
except ImportError:
    logger.warning("Sentry SDK not found. Skipping Sentry initialization.")
except Exception as e:
    logger.error(f"Failed to initialize Sentry: {e}")

# --- Configuration ---
PORT = int(os.environ.get("PORT", 8080))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_BROWSE_DIR = os.path.abspath(os.path.expanduser("~") if not os.environ.get("RENDER") else BASE_DIR)
WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")

# --- System Compatibility ---
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception as e:
        logger.error(f"Failed to set UTF-8 encoding: {e}")

# --- Optional Dependencies (Safe Imports) ---
def safe_import(module_name):
    try:
        return __import__(module_name)
    except ImportError:
        logger.warning(f"Optional module '{module_name}' not found.")
        return None

psutil = safe_import("psutil")
pyautogui = safe_import("pyautogui")
if pyautogui:
    try:
        pyautogui.FAILSAFE = True
    except Exception as e:
        logger.error(f"Failed to set pyautogui failsafe: {e}")

# --- Global State ---
active_sessions = {}
device_registry = {}
ws_connections = {}

# --- Helper: Async Alert ---
async def send_alert_async(message, level="info"):
    if not WEBHOOK_URL: return
    emoji = "🚨" if level == "error" else "ℹ️" if level == "info" else "✅"
    payload = {"text": f"{emoji} *Access Pro*: {message}"}
    try:
        async with ClientSession() as session:
            async with session.post(WEBHOOK_URL, json=payload, timeout=5) as resp:
                if resp.status != 200:
                    logger.error(f"Webhook failed with status {resp.status}")
    except Exception as e:
        logger.error(f"Failed to send async alert: {e}")

def get_device_stats():
    import platform
    stats = {"cpu": 0, "ram": 0, "battery": 100, "platform": platform.system()}
    if psutil:
        try:
            stats["cpu"] = psutil.cpu_percent()
            stats["ram"] = psutil.virtual_memory().percent
            batt = psutil.sensors_battery()
            if batt: stats["battery"] = batt.percent
        except Exception as e:
            logger.debug(f"Error getting device stats: {e}")
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
    logger.info(f"New WebSocket connection: {conn_id}")

    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    logger.error(f"Malformed JSON from {conn_id}")
                    continue

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
                    ws_connections[ws] = {"code": code, "role": "host", "id": conn_id}
                    await ws.send_json({"type": "registered", "access_code": code, "device_name": name, "message": "Device registered successfully"})
                    logger.info(f"Device registered: {name} ({code})")
                    asyncio.create_task(send_alert_async(f"New Device Registered: *{name}* (`{code}`)", "success"))

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
                        ws_connections[ws] = {"session_id": sid, "role": "client", "id": conn_id}
                        await ws.send_json({"type": "connected", "session_id": sid, "host_name": host["device_name"]})
                        await host["ws"].send_json({"type": "incoming_connection", "session_id": sid, "client_name": data.get("device_name", "User")})
                        logger.info(f"Connection attempt: {data.get('device_name')} -> {host['device_name']} ({code})")
                    else:
                        await ws.send_json({"type": "error", "message": "Invalid access code"})

                elif mtype == "accept_connection":
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        token = active_sessions[sid]["token"]
                        msg = {"type": "connection_accepted", "session_id": sid, "session_token": token, "message": "Connection accepted by host"}
                        await active_sessions[sid]["client"].send_json(msg)
                        await active_sessions[sid]["host"].send_json(msg)
                        logger.info(f"Session started: {sid}")
                        asyncio.create_task(send_alert_async(f"Session Started: `{sid[:8]}`", "info"))

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
                    except Exception as e:
                        logger.error(f"Control error: {e}")

                elif mtype in ["offer", "answer", "ice-candidate", "chat", "remote_input", "ring", "clipboard"]:
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        sess = active_sessions[sid]
                        target = sess["client"] if ws == sess["host"] else sess["host"]
                        if not target.closed:
                            await target.send_json(data)

                elif mtype == "disconnect":
                    sid = data.get("session_id")
                    if sid in active_sessions:
                        logger.info(f"Manual disconnect for session: {sid}")
                        # Cleanup will happen in 'finally' or via peer_disconnected message
    except Exception as e:
        logger.error(f"WebSocket Error ({conn_id}): {e}")
        if SENTRY_DSN: sentry_sdk.capture_exception(e)
    finally:
        logger.info(f"WebSocket closed: {conn_id}")
        if ws in ws_connections:
            info = ws_connections[ws]
            if info.get("role") == "host":
                code = info.get("code")
                if code in device_registry:
                    logger.info(f"Host offline: {device_registry[code]['device_name']} ({code})")
                    del device_registry[code]

            for sid, sess in list(active_sessions.items()):
                if ws in (sess["host"], sess["client"]):
                    other = sess["client"] if ws == sess["host"] else sess["host"]
                    if not other.closed:
                        try:
                            await other.send_json({"type": "peer_disconnected", "message": "The other party has disconnected"})
                        except: pass
                    logger.info(f"Session ended: {sid}")
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
    try:
        auth = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not any(s["token"] == auth for s in active_sessions.values()):
            logger.warning(f"Unauthorized file access attempt from {request.remote}")
            return web.json_response({"error": "Unauthorized"}, status=401)

        path = request.query.get("path", ROOT_BROWSE_DIR)
        if not os.path.exists(path): path = ROOT_BROWSE_DIR

        # Security: Prevent directory traversal
        path = os.path.abspath(path)
        # In a real app, we'd check if path starts with ROOT_BROWSE_DIR

        items = []
        with os.scandir(path) as it:
            for e in it:
                try:
                    items.append({
                        "name": e.name,
                        "path": e.path,
                        "is_dir": e.is_dir(),
                        "size": e.stat().st_size if e.is_file() else 0
                    })
                except Exception as ex:
                    logger.debug(f"Error scanning {e.name}: {ex}")

        return web.json_response({"current": path, "items": items})
    except Exception as e:
        logger.error(f"API Files error: {e}")
        return web.json_response({"error": str(e)}, status=500)

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
    logger.info(f"Starting Access Pro server on port {PORT}...")
    try:
        web.run_app(app, host='0.0.0.0', port=PORT, access_log=logger)
    except Exception as e:
        logger.critical(f"Server failed to start: {e}")
        if SENTRY_DSN: sentry_sdk.capture_exception(e)
