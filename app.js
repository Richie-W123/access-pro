// ─── Global State ───
let ws = null;
let peerConnection = null;
let currentSessionId = null;
let localStream = null;
let accessCode = null;
let isHost = false;
let isControlActive = false;

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
        const res = await fetch('/api/ws-info');
        const info = await res.json();
        
        let wsUrl = info.ws_url;
        
        // Handle GitHub Dev Tunnels
        if (window.location.hostname.endsWith('.github.dev')) {
            // Replace the 8080 port in the subdomain with 8765 for the websocket tunnel
            const wsHostname = window.location.hostname.replace('-8080.app.github.dev', '-8765.app.github.dev')
                                                       .replace('-8080.github.dev', '-8765.github.dev');
            wsUrl = `wss://${wsHostname}`;
        } else if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
            wsUrl = info.ws_local;
        }

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

// ─── Signaling Handlers ───
async function handleSignalingMessage(data) {
    switch (data.type) {
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

function setupDataChannel(channel) {
    controlChannel = channel;
    controlChannel.onopen = () => console.log("Control channel opened");
    controlChannel.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'cursor') {
            updateRemoteCursor(data.x, data.y);
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
    video.addEventListener('mousemove', (e) => {
        if (!isControlActive || !controlChannel || controlChannel.readyState !== 'open') return;
        
        const rect = video.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;
        
        controlChannel.send(JSON.stringify({ type: 'cursor', x, y }));
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
    // Check initial status endpoint to test basic connectivity
    fetch('/api/status')
        .then(res => res.json())
        .then(data => {
            updateStatus("Server Online", true);
        })
        .catch(err => {
            updateStatus("Server Offline", false);
        });
});
