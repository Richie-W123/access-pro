// ─── Global State ───
let ws = null;
let peerConnection = null;
let currentSessionId = null;
let currentSessionToken = null;
let localStream = null;
let accessCode = null;
let isHost = false;
let isControlActive = false;
let isCameraActive = false;

// ─── Dashboard Logic ───
async function loadDashboard() {
    try {
        const res = await fetch('/api/status'); // Check if server is up
        const status = await res.json();

        // Fetch devices
        const devRes = await fetch('/api/devices');
        const devices = await devRes.json();
        const list = document.getElementById('dashboardList');
        const dashboard = document.getElementById('deviceDashboard');

        if (devices.length > 0) {
            dashboard.style.display = 'block';
            list.innerHTML = devices.map(d => `
                <div class="dashboard-item glass-card" onclick="autoConnect('${d.code}')" style="cursor:pointer; margin-bottom: 10px; padding: 10px; border: 1px solid #444; border-radius: 8px;">
                    <div class="device-info">
                        <strong>${d.name}</strong><br>
                        <small>${d.status.platform || 'System'} • ${d.status.battery}% Bat • CPU: ${d.status.cpu}%</small>
                    </div>
                </div>
            `).join('');
        }
    } catch (e) { console.error("Dashboard error:", e); }
}

function autoConnect(code) {
    showPanel('client');
    const inputs = document.querySelectorAll('.code-input');
    code.split('').forEach((char, i) => { if(inputs[i]) inputs[i].value = char; });
    // Trigger connection automatically
    setTimeout(connectToDevice, 500);
}

// ─── Camera Access ───
async function toggleCamera() {
    if (!isHost) {
        showToast("Only the host can toggle their camera.", "info");
        return;
    }

    try {
        isCameraActive = !isCameraActive;
        const constraints = isCameraActive
            ? { video: true, audio: true }
            : { video: { cursor: "always" }, audio: true };

        const newStream = isCameraActive
            ? await navigator.mediaDevices.getUserMedia(constraints)
            : await navigator.mediaDevices.getDisplayMedia(constraints);

        const videoTrack = newStream.getVideoTracks()[0];
        const sender = peerConnection.getSenders().find(s => s.track.kind === 'video');
        if (sender) sender.replaceTrack(videoTrack);

        localStream.getTracks().forEach(t => t.stop());
        localStream = newStream;

        const video = document.getElementById('remoteVideo');
        video.srcObject = localStream;

        showToast(isCameraActive ? "Camera active" : "Screen sharing active", "success");
    } catch (e) {
        showToast("Media source switch failed: " + e.message, "error");
        isCameraActive = !isCameraActive;
    }
}

// ─── Clipboard Sync ───
async function syncClipboard() {
    try {
        const text = await navigator.clipboard.readText();
        if (controlChannel && controlChannel.readyState === 'open') {
            controlChannel.send(JSON.stringify({ type: 'clipboard', text }));
            showToast("Clipboard sent to remote device", "success");
        }
    } catch (e) {
        showToast("Clipboard access denied.", "error");
    }
}

// ─── File Management ───
let currentPath = "";

async function toggleFileTransfer() {
    const sidebar = document.getElementById('fileSidebar');
    sidebar.classList.toggle('active');
    if (sidebar.classList.contains('active')) {
        loadFiles("");
    }
}

async function loadFiles(path) {
    try {
        const headers = {};
        if (currentSessionToken) {
            headers['Authorization'] = `Bearer ${currentSessionToken}`;
        }
        const res = await fetch(`/api/files?path=${encodeURIComponent(path)}`, { headers });
        const data = await res.json();
        currentPath = data.current;

        const list = document.getElementById('fileList');
        list.innerHTML = data.items.map(item => `
            <div class="file-item" onclick="${item.is_dir ? `loadFiles('${item.path.replace(/\\/g, '/')}')` : `window.open('/api/download?path=${encodeURIComponent(item.path)}&token=${encodeURIComponent(currentSessionToken)}')`}" style="padding: 8px; cursor: pointer; border-bottom: 1px solid #333; display: flex; align-items: center; gap: 10px;">
                <span>${item.is_dir ? '📁' : '📄'}</span>
                <span>${item.name}</span>
            </div>
        `).join('');
    } catch (e) {
        showToast("Failed to load files", "error");
    }
}

// WebRTC Configuration
const rtcConfig = {
    iceServers: [
        { urls: "stun:stun.l.google.com:19302" },
        { urls: "stun:stun1.l.google.com:19302" }
    ]
};

// ─── UI Management ───
function showPanel(panelName) {
    document.getElementById('heroSection').style.display = 'none';
    document.getElementById('hostPanel').style.display = 'none';
    document.getElementById('clientPanel').style.display = 'none';
    document.getElementById('sessionPanel').style.display = 'none';

    if (panelName === 'hero') {
        document.getElementById('heroSection').style.display = 'grid';
        if (ws) { ws.close(); ws = null; }
        if (peerConnection) { peerConnection.close(); peerConnection = null; }
        updateStatus("Disconnected", false);
    } else if (panelName === 'host') {
        document.getElementById('hostPanel').style.display = 'block';
        document.getElementById('hostSetup').style.display = 'block';
        document.getElementById('hostActive').style.display = 'none';
        document.getElementById('incomingRequest').style.display = 'none';
    } else if (panelName === 'client') {
        document.getElementById('clientPanel').style.display = 'block';
        document.getElementById('clientConnect').style.display = 'block';
        document.getElementById('connectionStatus').style.display = 'none';
        setupCodeInput();
    } else if (panelName === 'session') {
        document.getElementById('sessionPanel').style.display = 'flex';
        document.getElementById('navbar').style.display = 'none';
    }
}

function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.classList.add('hiding');
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function updateStatus(text, isConnected) {
    document.getElementById('navStatusText').textContent = text;
    const dot = document.getElementById('navStatusDot');
    if (isConnected) dot.classList.add('connected');
    else dot.classList.remove('connected');
}

// ─── Access Code Input ───
function setupCodeInput() {
    const inputs = document.querySelectorAll('.code-input');
    inputs.forEach((input, idx) => {
        input.addEventListener('input', (e) => {
            if (e.target.value && idx < inputs.length - 1) inputs[idx + 1].focus();
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace' && !e.target.value && idx > 0) {
                inputs[idx - 1].focus();
            }
        });
    });
}

function getAccessCode() {
    return Array.from(document.querySelectorAll('.code-input')).map(i => i.value).join('');
}

function displayAccessCode(code) {
    const digits = document.querySelectorAll('.code-digit');
    for (let i = 0; i < code.length; i++) {
        if (digits[i]) digits[i].textContent = code[i];
    }
}

// ─── WebSocket Connection ───
async function connectWebSocket(onReady) {
    try {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${protocol}//${window.location.host}/ws`;

        ws = new WebSocket(wsUrl);

        ws.onopen = () => {
            updateStatus("Server Connected", true);
            if (onReady) onReady();
        };

        ws.onmessage = async (event) => {
            const data = JSON.parse(event.data);
            handleSignalingMessage(data);
        };

        ws.onclose = () => {
            updateStatus("Server Disconnected", false);
            showToast("Connection to server lost.", "error");
        };

    } catch (e) {
        showToast("Failed to connect to signaling server.", "error");
        console.error(e);
    }
}

// ─── Host Functions ───
async function startHosting() {
    const deviceName = document.getElementById('hostDeviceName').value || "My Device";
    isHost = true;

    // Get screen stream before registering
    try {
        localStream = await navigator.mediaDevices.getDisplayMedia({
            video: { cursor: "always" },
            audio: true
        });
        
        // Stop stream if user stops sharing via browser UI
        localStream.getVideoTracks()[0].onended = () => {
            showToast("Screen sharing stopped", "info");
            showPanel('hero');
        };

    } catch (e) {
        showToast("Screen sharing permission denied.", "error");
        return;
    }

    connectWebSocket(() => {
        ws.send(JSON.stringify({
            type: "register",
            device_name: deviceName
        }));
    });
}

function acceptConnection() {
    ws.send(JSON.stringify({
        type: "accept_connection",
        session_id: currentSessionId
    }));
    document.getElementById('incomingRequest').style.display = 'none';
}

function rejectConnection() {
    ws.send(JSON.stringify({
        type: "reject_connection",
        session_id: currentSessionId
    }));
    document.getElementById('incomingRequest').style.display = 'none';
    currentSessionId = null;
}

// ─── Client Functions ───
function connectToDevice() {
    const code = getAccessCode();
    if (code.length !== 6) {
        showToast("Please enter a valid 6-digit code.", "error");
        return;
    }

    const deviceName = document.getElementById('clientDeviceName').value || "Remote User";
    isHost = false;

    document.getElementById('clientConnect').style.display = 'none';
    document.getElementById('connectionStatus').style.display = 'block';

    connectWebSocket(() => {
        ws.send(JSON.stringify({
            type: "connect",
            access_code: code,
            device_name: deviceName
        }));
    });
}

// ─── Chat Logic ───
function toggleChat() {
    document.getElementById('chatSidebar').classList.toggle('active');
}

function sendChatMessage() {
    const input = document.getElementById('chatInput');
    const message = input.value.trim();
    if (!message || !ws || !currentSessionId) return;

    ws.send(JSON.stringify({
        type: "chat",
        session_id: currentSessionId,
        sender: isHost ? "Host" : "Client",
        message: message
    }));

    appendChatMessage("You", message);
    input.value = "";
}

function appendChatMessage(sender, message) {
    const container = document.getElementById('chatMessages');
    const div = document.createElement('div');
    div.className = `chat-msg ${sender === 'You' ? 'msg-own' : ''}`;
    div.innerHTML = `<strong>${sender}:</strong> <span>${message}</span>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// ─── Fullscreen & Utility ───
function toggleFullscreen() {
    const container = document.getElementById('remoteScreenContainer');
    if (!document.fullscreenElement) {
        container.requestFullscreen().catch(err => {
            showToast(`Error attempting to enable fullscreen: ${err.message}`, "error");
        });
    } else {
        document.exitFullscreen();
    }
}

function handleFileSelect(event) {
    const files = event.target.files;
    if (files.length > 0) {
        showToast(`Selected ${files.length} files. Upload feature coming soon!`, "info");
    }
}

// ─── Signaling Handlers ───
async function handleSignalingMessage(data) {
    switch (data.type) {
        case "chat":
            appendChatMessage(data.sender, data.message);
            if (!document.getElementById('chatSidebar').classList.contains('active')) {
                showToast(`New message from ${data.sender}`, "info");
            }
            break;
        case "registered":
            accessCode = data.access_code;
            displayAccessCode(accessCode);
            document.getElementById('hostSetup').style.display = 'none';
            document.getElementById('hostActive').style.display = 'block';
            showToast(data.message, "success");
            break;

        case "incoming_connection":
            currentSessionId = data.session_id;
            document.getElementById('requestTitle').textContent = `Connection Request`;
            document.getElementById('requestMessage').textContent = `${data.client_name} wants to access your screen.`;
            document.getElementById('incomingRequest').style.display = 'block';
            break;

        case "connected":
            currentSessionId = data.session_id;
            document.getElementById('connStatusTitle').textContent = "Waiting for Host";
            document.getElementById('connStatusMessage').textContent = "Waiting for the host to accept your connection...";
            break;

        case "connection_accepted":
            currentSessionId = data.session_id;
            currentSessionToken = data.session_token;
            showToast(data.message, "success");
            startWebRTC();
            break;

        case "connection_rejected":
            showToast(data.message, "error");
            showPanel('hero');
            break;

        case "offer":
            await handleOffer(data.offer);
            break;

        case "answer":
            await handleAnswer(data.answer);
            break;

        case "ice-candidate":
            await handleIceCandidate(data.candidate);
            break;

        case "peer_disconnected":
            showToast(data.message, "info");
            endSessionLocally();
            break;

        case "error":
            showToast(data.message, "error");
            showPanel('hero');
            break;
    }
}

// ─── WebRTC Logic ───
async function startWebRTC() {
    showPanel('session');
    
    peerConnection = new RTCPeerConnection(rtcConfig);

    peerConnection.onicecandidate = (event) => {
        if (event.candidate) {
            ws.send(JSON.stringify({
                type: "ice-candidate",
                session_id: currentSessionId,
                candidate: event.candidate
            }));
        }
    };

    if (isHost) {
        // Add local stream tracks to PC
        localStream.getTracks().forEach(track => {
            peerConnection.addTrack(track, localStream);
        });

        // Create Data Channel for control
        const dataChannel = peerConnection.createDataChannel("control");
        setupDataChannel(dataChannel);

        const offer = await peerConnection.createOffer();
        await peerConnection.setLocalDescription(offer);

        ws.send(JSON.stringify({
            type: "offer",
            session_id: currentSessionId,
            offer: offer
        }));
        
        document.getElementById('screenOverlay').style.display = 'none';
        const video = document.getElementById('remoteVideo');
        video.srcObject = localStream;

    } else {
        // Client receives remote stream
        peerConnection.ontrack = (event) => {
            document.getElementById('screenOverlay').style.display = 'none';
            const video = document.getElementById('remoteVideo');
            if (video.srcObject !== event.streams[0]) {
                video.srcObject = event.streams[0];
            }
        };

        peerConnection.ondatachannel = (event) => {
            setupDataChannel(event.channel);
        };
        
        setupRemoteControl();
    }
}

async function handleOffer(offer) {
    if (!peerConnection) startWebRTC();
    await peerConnection.setRemoteDescription(new RTCSessionDescription(offer));
    
    const answer = await peerConnection.createAnswer();
    await peerConnection.setLocalDescription(answer);
    
    ws.send(JSON.stringify({
        type: "answer",
        session_id: currentSessionId,
        answer: answer
    }));
}

async function handleAnswer(answer) {
    await peerConnection.setRemoteDescription(new RTCSessionDescription(answer));
}

async function handleIceCandidate(candidate) {
    if (peerConnection) {
        try {
            await peerConnection.addIceCandidate(new RTCIceCandidate(candidate));
        } catch (e) {
            console.error("Error adding ice candidate", e);
        }
    }
}

// ─── Data Channel & Control ───
let controlChannel = null;

function ringDevice() {
    if (controlChannel && controlChannel.readyState === 'open') {
        controlChannel.send(JSON.stringify({ type: 'ring' }));
        showToast("Ringing remote device...", "success");
    }
}

function startVoiceCommand() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        showToast("Speech recognition not supported in this browser.", "error");
        return;
    }

    const recognition = new SpeechRecognition();
    recognition.onstart = () => showToast("Listening for commands...", "info");
    recognition.onresult = (event) => {
        const command = event.results[0][0].transcript.toLowerCase();
        showToast(`Heard: "${command}"`, "success");
        processVoiceCommand(command);
    };
    recognition.start();
}

function processVoiceCommand(command) {
    if (command.includes("ring")) ringDevice();
    else if (command.includes("camera")) toggleCamera();
    else if (command.includes("chat")) toggleChat();
    else if (command.includes("file")) toggleFileTransfer();
    else if (command.includes("disconnect")) endSession();
    else {
        // Forward unknown commands to peer chat as a message
        if (ws && currentSessionId) {
            ws.send(JSON.stringify({
                type: "chat",
                session_id: currentSessionId,
                sender: "AI Voice",
                message: `Command: ${command}`
            }));
        }
    }
}

function setupDataChannel(channel) {
    controlChannel = channel;
    controlChannel.onopen = () => console.log("Control channel opened");
    controlChannel.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (isHost) {
            // Forward commands to the python server via websocket
            if (ws && ws.readyState === WebSocket.OPEN) {
                if (data.type === 'mouse_move') {
                    ws.send(JSON.stringify({ type: 'local_control', action: 'mouse_move', x: data.x, y: data.y }));
                } else if (data.type === 'mouse_click') {
                    ws.send(JSON.stringify({ type: 'local_control', action: 'mouse_click', button: data.button || 'left' }));
                } else if (data.type === 'key_down') {
                    ws.send(JSON.stringify({ type: 'local_control', action: 'key_down', key: data.key }));
                }
            }
            // Update local visual cursor on host if needed
            if (data.type === 'cursor' || data.type === 'mouse_move') {
                updateRemoteCursor(data.x, data.y);
            }
        } else {
            // Client receives updates
            if (data.type === 'cursor' || data.type === 'mouse_move') {
                updateRemoteCursor(data.x, data.y);
            } else if (data.type === 'clipboard') {
                navigator.clipboard.writeText(data.text);
                showToast("Remote clipboard synced!", "success");
            } else if (data.type === 'ring') {
                const audio = new Audio('https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3');
                audio.play();
                showToast("🚨 DEVICE RINGING! Someone is looking for this device.", "error");
            } else if (data.type === 'key_down') {
                console.log("Remote Key Pressed:", data.key);
            }
        }
    };
}

function toggleRemoteControl() {
    isControlActive = !isControlActive;
    const btn = document.getElementById('btnToggleControl');
    if (isControlActive) {
        btn.classList.add('active');
        showToast("Remote control activated", "success");
    } else {
        btn.classList.remove('active');
        showToast("Remote control deactivated", "info");
    }
}

function setupRemoteControl() {
    const video = document.getElementById('remoteVideo');

    // Mouse Move
    video.addEventListener('mousemove', (e) => {
        if (!isControlActive || !controlChannel || controlChannel.readyState !== 'open') return;
        
        const rect = video.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;
        
        controlChannel.send(JSON.stringify({ type: 'mouse_move', x, y }));
    });

    // Mouse Click (Left)
    video.addEventListener('click', (e) => {
        if (!isControlActive || !controlChannel || controlChannel.readyState !== 'open') return;
        controlChannel.send(JSON.stringify({ type: 'mouse_click', button: 'left' }));
    });

    // Mouse Click (Right)
    video.addEventListener('contextmenu', (e) => {
        if (!isControlActive || !controlChannel || controlChannel.readyState !== 'open') return;
        e.preventDefault();
        controlChannel.send(JSON.stringify({ type: 'mouse_click', button: 'right' }));
    });

    // Touch Support (Mobile)
    let touchStartX = 0;
    let touchStartY = 0;
    let touchMoved = false;

    video.addEventListener('touchstart', (e) => {
        if (!isControlActive || !controlChannel || controlChannel.readyState !== 'open') return;
        const touch = e.touches[0];
        touchStartX = touch.clientX;
        touchStartY = touch.clientY;
        touchMoved = false;
        sendTouchMove(touch);
    }, { passive: true });

    video.addEventListener('touchmove', (e) => {
        if (!isControlActive || !controlChannel || controlChannel.readyState !== 'open') return;
        const touch = e.touches[0];
        const dx = touch.clientX - touchStartX;
        const dy = touch.clientY - touchStartY;
        if (Math.abs(dx) > 5 || Math.abs(dy) > 5) {
            touchMoved = true;
        }
        sendTouchMove(touch);
    }, { passive: true });

    video.addEventListener('touchend', (e) => {
        if (!isControlActive || !controlChannel || controlChannel.readyState !== 'open') return;
        if (!touchMoved) {
            controlChannel.send(JSON.stringify({ type: 'mouse_click', button: 'left' }));
        }
    });

    function sendTouchMove(touch) {
        const rect = video.getBoundingClientRect();
        const x = (touch.clientX - rect.left) / rect.width;
        const y = (touch.clientY - rect.top) / rect.height;
        const clampedX = Math.max(0, Math.min(1, x));
        const clampedY = Math.max(0, Math.min(1, y));
        controlChannel.send(JSON.stringify({ type: 'mouse_move', x: clampedX, y: clampedY }));
    }

    // Keyboard
    window.addEventListener('keydown', (e) => {
        if (!isControlActive || !controlChannel || controlChannel.readyState !== 'open') return;
        if (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') return;

        controlChannel.send(JSON.stringify({
            type: 'key_down',
            key: e.key,
            keyCode: e.keyCode
        }));

        // Prevent default browser actions for some keys
        if (['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.key)) {
            e.preventDefault();
        }
    });
}

function updateRemoteCursor(x, y) {
    const canvas = document.getElementById('cursorCanvas');
    const ctx = canvas.getContext('2d');
    
    // Resize canvas to match video
    const video = document.getElementById('remoteVideo');
    canvas.width = video.clientWidth;
    canvas.height = video.clientHeight;
    
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    const px = x * canvas.width;
    const py = y * canvas.height;
    
    // Draw cursor pointer
    ctx.beginPath();
    ctx.moveTo(px, py);
    ctx.lineTo(px + 15, py + 15);
    ctx.lineTo(px + 5, py + 15);
    ctx.lineTo(px, py + 22);
    ctx.closePath();
    ctx.fillStyle = 'white';
    ctx.fill();
    ctx.strokeStyle = 'black';
    ctx.lineWidth = 1;
    ctx.stroke();
}

function endSession() {
    if (ws && currentSessionId) {
        ws.send(JSON.stringify({
            type: "disconnect",
            session_id: currentSessionId
        }));
    }
    endSessionLocally();
}

function endSessionLocally() {
    if (peerConnection) { peerConnection.close(); peerConnection = null; }
    if (localStream) {
        localStream.getTracks().forEach(t => t.stop());
        localStream = null;
    }
    document.getElementById('navbar').style.display = 'flex';
    showPanel('hero');
}

function copyAccessCode() {
    if (accessCode) {
        navigator.clipboard.writeText(accessCode).then(() => {
            showToast("Access code copied to clipboard", "success");
        });
    }
}

// ─── Initialize ───
document.addEventListener('DOMContentLoaded', () => {
    loadDashboard();
    // Refresh dashboard every 10s
    setInterval(loadDashboard, 10000);

    fetch('/api/status')
        .then(res => res.json())
        .then(data => {
            updateStatus("Server Online", true);
        })
        .catch(err => {
            updateStatus("Server Offline", false);
        });
});
