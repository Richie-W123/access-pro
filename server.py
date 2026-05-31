#!/usr/bin/env python3
"""
RemoteLink Pro - Remote Device Access Server
=============================================
A WebSocket-based signaling server for WebRTC peer-to-peer remote access.
Supports screen sharing, remote input control, file transfer, and chat.
"""

import asyncio
import json
import os
import sys
import io
import uuid
import time
import hashlib
import http.server
import socketserver
import threading
import socket
import webbrowser
import subprocess
import re

# Force UTF-8 on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── Configuration ────────────────────────────────────────────────────────────
HTTP_PORT = 8080
WS_PORT = 8765
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Session Management ──────────────────────────────────────────────────────
active_sessions = {}   # session_id -> { "host": ws, "client": ws, "created": timestamp }
device_registry = {}   # access_code -> { "ws": ws, "device_name": str, "created": timestamp }
ws_connections = {}    # ws -> { "access_code": str, "device_name": str, "role": str }


def generate_access_code():
    """Generate a 6-digit numeric access code for easy sharing."""
    return str(uuid.uuid4().int)[:6].zfill(6)


def generate_session_id():
    """Generate a unique session identifier."""
    return hashlib.sha256(f"{time.time()}-{uuid.uuid4()}".encode()).hexdigest()[:16]


# ─── WebSocket Signaling Server ──────────────────────────────────────────────
# We'll use a simple asyncio-based WebSocket server

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False


async def handle_websocket(websocket, path=None):
    """Handle incoming WebSocket connections for signaling."""
    connection_id = str(uuid.uuid4())[:8]
    print(f"  📡 New WebSocket connection: {connection_id}")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                msg_type = data.get("type", "")

                if msg_type == "register":
                    # Device registers itself and gets an access code
                    access_code = generate_access_code()
                    device_name = data.get("device_name", "Unknown Device")

                    device_registry[access_code] = {
                        "ws": websocket,
                        "device_name": device_name,
                        "created": time.time()
                    }
                    ws_connections[websocket] = {
                        "access_code": access_code,
                        "device_name": device_name,
                        "role": "host"
                    }

                    await websocket.send(json.dumps({
                        "type": "registered",
                        "access_code": access_code,
                        "device_name": device_name,
                        "message": f"Your device '{device_name}' is now accessible with code: {access_code}"
                    }))
                    print(f"  🖥️  Device registered: {device_name} → Code: {access_code}")

                elif msg_type == "connect":
                    # Client wants to connect to a host device using access code
                    target_code = data.get("access_code", "")
                    client_name = data.get("device_name", "Remote User")

                    if target_code in device_registry:
                        host_info = device_registry[target_code]
                        host_ws = host_info["ws"]

                        # Create session
                        session_id = generate_session_id()
                        active_sessions[session_id] = {
                            "host": host_ws,
                            "client": websocket,
                            "created": time.time(),
                            "host_name": host_info["device_name"],
                            "client_name": client_name
                        }

                        ws_connections[websocket] = {
                            "access_code": target_code,
                            "device_name": client_name,
                            "role": "client",
                            "session_id": session_id
                        }

                        if websocket in ws_connections:
                            ws_connections[websocket]["session_id"] = session_id
                        if host_ws in ws_connections:
                            ws_connections[host_ws]["session_id"] = session_id

                        # Notify both sides
                        await websocket.send(json.dumps({
                            "type": "connected",
                            "session_id": session_id,
                            "host_name": host_info["device_name"],
                            "message": f"Connected to {host_info['device_name']}!"
                        }))

                        await host_ws.send(json.dumps({
                            "type": "incoming_connection",
                            "session_id": session_id,
                            "client_name": client_name,
                            "message": f"{client_name} wants to access your device"
                        }))

                        print(f"  🔗 Session created: {client_name} → {host_info['device_name']} ({session_id})")
                    else:
                        await websocket.send(json.dumps({
                            "type": "error",
                            "message": f"No device found with access code: {target_code}"
                        }))

                elif msg_type == "accept_connection":
                    # Host accepts the incoming connection request
                    session_id = data.get("session_id", "")
                    if session_id in active_sessions:
                        session = active_sessions[session_id]
                        await session["client"].send(json.dumps({
                            "type": "connection_accepted",
                            "session_id": session_id,
                            "message": "Connection accepted! Starting remote session..."
                        }))
                        await session["host"].send(json.dumps({
                            "type": "connection_accepted",
                            "session_id": session_id,
                            "message": "You accepted the connection. Starting screen sharing..."
                        }))
                        print(f"  ✅ Connection accepted for session: {session_id}")

                elif msg_type == "reject_connection":
                    session_id = data.get("session_id", "")
                    if session_id in active_sessions:
                        session = active_sessions[session_id]
                        await session["client"].send(json.dumps({
                            "type": "connection_rejected",
                            "session_id": session_id,
                            "message": "Connection was rejected by the host."
                        }))
                        del active_sessions[session_id]
                        print(f"  ❌ Connection rejected for session: {session_id}")

                elif msg_type in ["offer", "answer", "ice-candidate"]:
                    # WebRTC signaling - forward to the other peer
                    session_id = data.get("session_id", "")
                    if session_id in active_sessions:
                        session = active_sessions[session_id]
                        target_ws = session["client"] if websocket == session["host"] else session["host"]
                        await target_ws.send(json.dumps(data))

                elif msg_type == "chat":
                    # Forward chat messages
                    session_id = data.get("session_id", "")
                    if session_id in active_sessions:
                        session = active_sessions[session_id]
                        target_ws = session["client"] if websocket == session["host"] else session["host"]
                        await target_ws.send(json.dumps({
                            "type": "chat",
                            "sender": data.get("sender", "Unknown"),
                            "message": data.get("message", ""),
                            "timestamp": time.time()
                        }))

                elif msg_type == "remote_input":
                    # Forward remote input events (mouse, keyboard)
                    session_id = data.get("session_id", "")
                    if session_id in active_sessions:
                        session = active_sessions[session_id]
                        target_ws = session["host"] if websocket == session["client"] else session["client"]
                        await target_ws.send(json.dumps(data))

                elif msg_type == "disconnect":
                    session_id = data.get("session_id", "")
                    if session_id in active_sessions:
                        session = active_sessions[session_id]
                        target_ws = session["client"] if websocket == session["host"] else session["host"]
                        await target_ws.send(json.dumps({
                            "type": "peer_disconnected",
                            "message": "The remote user has disconnected."
                        }))
                        del active_sessions[session_id]
                        print(f"  🔌 Session ended: {session_id}")

                elif msg_type == "ping":
                    await websocket.send(json.dumps({"type": "pong", "timestamp": time.time()}))

            except json.JSONDecodeError:
                print(f"  ⚠️  Invalid JSON from {connection_id}")
            except Exception as e:
                print(f"  ⚠️  Error processing message: {e}")

    except Exception as e:
        print(f"  🔌 WebSocket disconnected: {connection_id} ({e})")
    finally:
        # Cleanup on disconnect
        if websocket in ws_connections:
            conn_info = ws_connections[websocket]
            access_code = conn_info.get("access_code", "")
            if access_code in device_registry and device_registry[access_code]["ws"] == websocket:
                del device_registry[access_code]
                print(f"  🗑️  Device unregistered: {conn_info.get('device_name', 'Unknown')}")
            del ws_connections[websocket]

        # Clean up any sessions involving this websocket
        sessions_to_remove = []
        for sid, session in active_sessions.items():
            if websocket in (session["host"], session["client"]):
                other_ws = session["client"] if websocket == session["host"] else session["host"]
                try:
                    await other_ws.send(json.dumps({
                        "type": "peer_disconnected",
                        "message": "The remote user has disconnected."
                    }))
                except:
                    pass
                sessions_to_remove.append(sid)
        for sid in sessions_to_remove:
            del active_sessions[sid]


# ─── HTTP Static File Server ──────────────────────────────────────────────────

class RemoteLinkHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """Custom HTTP handler for serving static files and API endpoints."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/api/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            status = {
                "server": "RemoteLink Pro",
                "version": "1.0.0",
                "ws_port": WS_PORT,
                "active_devices": len(device_registry),
                "active_sessions": len(active_sessions),
                "uptime": time.time()
            }
            self.wfile.write(json.dumps(status).encode())
        elif self.path == "/api/ws-info":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            local_ip = get_local_ip()
            info = {
                "ws_url": f"ws://{local_ip}:{WS_PORT}",
                "ws_local": f"ws://localhost:{WS_PORT}",
                "local_ip": local_ip,
                "http_port": HTTP_PORT,
                "ws_port": WS_PORT
            }
            self.wfile.write(json.dumps(info).encode())
        else:
            super().do_GET()

    def log_message(self, format, *args):
        if "/api/" not in str(args[0]):
            pass  # Suppress routine static file logs


def get_local_ip():
    """Get the local IP address on the LAN."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"


# ─── Tunnel Management ───────────────────────────────────────────────────────

tunnel_url = None

def start_ssh_tunnel():
    """Attempt to create an SSH tunnel for remote access across networks."""
    global tunnel_url

    tunnel_hosts = [
        {
            "name": "Localhost.run",
            "cmd": ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL",
                    "-R", f"80:127.0.0.1:{HTTP_PORT}", "nokey@localhost.run"],
            "pattern": r"https://[a-zA-Z0-9.-]+\.lhr\.life"
        },
        {
            "name": "Serveo.net",
            "cmd": ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL",
                    "-R", f"80:127.0.0.1:{HTTP_PORT}", "serveo.net"],
            "pattern": r"https://[a-zA-Z0-9]+\.serveo\.net"
        }
    ]

    for host in tunnel_hosts:
        try:
            print(f"  🌐 Trying tunnel via {host['name']}...")
            process = subprocess.Popen(
                host["cmd"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                errors="replace"
            )

            for line in iter(process.stdout.readline, ""):
                cleaned = line.strip()
                if cleaned:
                    match = re.search(host["pattern"], cleaned)
                    if match:
                        tunnel_url = match.group(0)
                        print(f"  ✅ Tunnel active: {tunnel_url}")
                        # Keep process alive
                        process.wait()
                        break

            if tunnel_url:
                break
        except Exception as e:
            print(f"  ⚠️  {host['name']} failed: {e}")
            continue

    if not tunnel_url:
        print("  ℹ️  No tunnel established. Use local network access.")


# ─── Main Server Launcher ────────────────────────────────────────────────────

def run_http_server():
    """Start the HTTP server in a thread."""
    with socketserver.TCPServer(("0.0.0.0", HTTP_PORT), RemoteLinkHTTPHandler) as httpd:
        print(f"  🌍 HTTP server running on port {HTTP_PORT}")
        httpd.serve_forever()


async def run_ws_server():
    """Start the WebSocket signaling server."""
    global websockets, HAS_WEBSOCKETS
    if not HAS_WEBSOCKETS:
        print("  ⚠️  websockets library not found. Installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
        # Re-import after install
        import websockets as ws_mod
        websockets = ws_mod
        HAS_WEBSOCKETS = True

    server = await websockets.serve(handle_websocket, "0.0.0.0", WS_PORT)
    print(f"  📡 WebSocket signaling server running on port {WS_PORT}")
    await server.wait_closed()


def main():
    print()
    print("=" * 60)
    print("  🚀 RemoteLink Pro - Remote Device Access Server")
    print("=" * 60)
    print()

    local_ip = get_local_ip()

    # Start HTTP server in a thread
    http_thread = threading.Thread(target=run_http_server, daemon=True)
    http_thread.start()

    # Start SSH tunnel in a thread
    tunnel_thread = threading.Thread(target=start_ssh_tunnel, daemon=True)
    tunnel_thread.start()

    print(f"  📍 Local Access:  http://localhost:{HTTP_PORT}")
    print(f"  📍 Network Access: http://{local_ip}:{HTTP_PORT}")
    print(f"  📍 WebSocket:     ws://{local_ip}:{WS_PORT}")
    print()
    print("  💡 Open the URL above in your browser to start!")
    print("  💡 Share your Access Code with the remote user.")
    print()

    # Open browser after 1.5 second delay
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{HTTP_PORT}")).start()

    # Run WebSocket server on the main asyncio event loop
    asyncio.run(run_ws_server())


if __name__ == "__main__":
    main()
