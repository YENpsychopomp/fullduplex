const baseUrl = "http://localhost:8000/api/v1/";
// ===== DOM =====
const callStatusEl = document.getElementById("call-status");
const helperTextEl = document.getElementById("helper-text");
const avatarEl = document.getElementById("avatar");
const visualizerEl = document.getElementById("visualizer");
const wsPillEl = document.getElementById("ws-pill");
const wsTextEl = document.getElementById("ws-text");
const micPillEl = document.getElementById("mic-pill");
const micTextEl = document.getElementById("mic-text");
const btnToggleCall = document.getElementById("btn-toggle-call");
const btnMute = document.getElementById("btn-mute");
const systemPromptEl = document.getElementById("system-prompt");
const userSessionEl = document.getElementById("user-session");
const bars = [];
let callActive = false;
let micMuted = false;
let visualizerTimer = null;
let ws = null;
let session = null;
let heartbeatInterval = null;

// ===== Audio streaming =====
let mediaStream = null;
let mediaRecorder = null;
let audioTrack = null;

const OPUS_CHUNK_MS = 100;

// ===== 小工具 =====
const setText = (el, text) => {
    if (el) el.textContent = text;
};

const setPillState = (pillEl, state, textEl, text) => {
    if (pillEl) pillEl.dataset.state = state;
    setText(textEl, text);
};

const setCallStatus = (text) => setText(callStatusEl, text);

const wsUrl = (sessionOverride, systemPrompt) => {
    const isHttps = window.location.protocol === "https:";
    const proto = isHttps ? "wss" : "ws";
    if (sessionOverride) {
        return `${proto}://${window.location.host}/ws/${encodeURIComponent(sessionOverride)}/${encodeURIComponent(systemPrompt)}`;
    }
    return `${proto}://${window.location.host}/ws/${encodeURIComponent(systemPrompt)}`;
};

const closeWs = () => {
    if (!ws) return;
    stopHeartbeat();
    try {
        ws.close();
    } catch {}
    ws = null;
    session = null;
};

async function startRecording() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        throw new Error("ws not open");
    }

    // Clean up any previous recording state (defensive).
    stopRecording();

    if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("getUserMedia not supported");
    }

    mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
        },
        video: false,
    });

    audioTrack = mediaStream.getAudioTracks()[0] || null;
    if (audioTrack) {
        audioTrack.enabled = !micMuted;
    }

    // Prefer Opus (WebM) then Opus (Ogg). Browser-dependent.
    const candidates = [
        "audio/webm;codecs=opus",
        "audio/ogg;codecs=opus",
    ];
    let mimeType = "";
    for (const c of candidates) {
        if (window.MediaRecorder?.isTypeSupported?.(c)) {
            mimeType = c;
            break;
        }
    }

    if (!window.MediaRecorder) {
        throw new Error("MediaRecorder not supported");
    }

    try {
        mediaRecorder = mimeType
            ? new MediaRecorder(mediaStream, { mimeType })
            : new MediaRecorder(mediaStream);
    } catch (e) {
        mediaRecorder = null;
        throw e;
    }

    mediaRecorder.ondataavailable = (event) => {
        if (!event?.data || event.data.size === 0) return;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;

        // Send Opus container bytes as binary.
        try {
            ws.send(event.data);
        } catch (e) {
            console.warn("ws.send failed", e);
        }
    };

    mediaRecorder.onerror = (event) => {
        console.warn("MediaRecorder error", event);
    };

    mediaRecorder.start(OPUS_CHUNK_MS);
}

function stopRecording() {
    const recorder = mediaRecorder;
    mediaRecorder = null;
    audioTrack = null;

    return new Promise((resolve) => {
        const cleanupTracks = () => {
            if (mediaStream) {
                for (const track of mediaStream.getTracks()) {
                    try {
                        track.stop();
                    } catch {}
                }
                mediaStream = null;
            }
            resolve();
        };

        if (!recorder) {
            cleanupTracks();
            return;
        }

        try {
            if (recorder.state === "inactive") {
                cleanupTracks();
                return;
            }

            recorder.addEventListener(
                "stop",
                () => {
                    // Let the final dataavailable flush first.
                    window.setTimeout(cleanupTracks, 20);
                },
                { once: true },
            );

            try {
                recorder.requestData();
            } catch {}
            recorder.stop();
        } catch {
            cleanupTracks();
        }
    });
}

function startHeartbeat() {
    heartbeatInterval = setInterval(() => {
        if (ws && ws.readyState === WebSocket.OPEN) {
            console.log("Ping...");
            ws.send(JSON.stringify({ type: "ping" }));
        }
    }, 30000); 
}

function stopHeartbeat() {
    if (heartbeatInterval) {
        clearInterval(heartbeatInterval);
        heartbeatInterval = null;
    }
}

const connectWs = () => {
    return new Promise((resolve, reject) => {
        const desiredSession = (userSessionEl?.value || "").trim();
        const systemPrompt = (systemPromptEl?.value || "").trim();
        try {
            if (desiredSession) {
                console.log(`Using user session from input: ${desiredSession}`);
                ws = new WebSocket(wsUrl(desiredSession, systemPrompt));
            } else {
                ws = new WebSocket(wsUrl(null, systemPrompt || "你是一個友善的語音助理，協助使用者解決問題。"));
            }
        } catch (e) {
            ws = null;
            reject(e);
            return;
        }

        let settled = false;
        const timeout = window.setTimeout(() => {
            if (settled) return;
            settled = true;
            try {
                ws?.close();
            } catch {}
            ws = null;
            reject(new Error("ws timeout"));
        }, 6000);

        ws.onopen = () => {
            setPillState(wsPillEl, "warn", wsTextEl, "等待 session…");
        };

        ws.onerror = () => {
            if (settled) return;
            settled = true;
            window.clearTimeout(timeout);
            reject(new Error("ws error"));
        };

        ws.onclose = () => {
            stopHeartbeat();
            if (!callActive) {
                setPillState(wsPillEl, "warn", wsTextEl, "已離線");
                return;
            }
            stopRecording();
            setPillState(wsPillEl, "bad", wsTextEl, "連線中斷");
            setCallStatus("連線中斷");
            callActive = false;
            micMuted = false;
            stopVisualizer();
            setPillState(micPillEl, "warn", micTextEl, "未開始");
            setButtons();
        };

        ws.onmessage = (event) => {
            let msg;
            try {
                msg = JSON.parse(event.data);
            } catch {
                console.warn("無法解析 ws 訊息", event.data);
                return;
            }
            if (!msg || typeof msg.type !== "string") return;

            if (msg.type === "session" && typeof msg.session === "string") {
                session = msg.session;
                if (!settled) {
                    settled = true;
                    window.clearTimeout(timeout);
                    resolve(session);
                    startHeartbeat();
                }
            }

            if (msg.type === "error") {
                const message = typeof msg.message === "string" ? msg.message : "ws error";
                if (!settled) {
                    settled = true;
                    window.clearTimeout(timeout);
                    try {
                        ws?.close();
                    } catch {}
                    ws = null;
                    reject(new Error(message));
                }
            }

            if (msg.type === "pong") {
                console.log("Received pong from server");
            }

            if (msg.type === "stt.started") {
                console.log("STT started");
                setCallStatus("語音辨識中…");
            }

            if (msg.type === "stt.result") {
                const text = typeof msg.text === "string" ? msg.text.trim() : "";
                console.log("STT result:", text);
                setCallStatus("STT 完成");
                setText(helperTextEl, text ? `STT: ${text}` : "STT 完成（無文字）");
                
            }

            if (msg.type === "stt.error") {
                const message = typeof msg.message === "string" ? msg.message : "stt error";
                console.warn("STT error:", message);
                setCallStatus("STT 失敗");
                setText(helperTextEl, `STT 錯誤: ${message}`);
            }
        };
    });
};

const setButtons = () => {
    // 通話按鈕：永遠可按（允許在未連線時觸發連線）
    if (btnToggleCall) {
        btnToggleCall.title = callActive ? "結束通話" : "開始通話";
        btnToggleCall.setAttribute(
            "aria-label",
            callActive ? "結束通話" : "開始通話",
        );

        const icon = btnToggleCall.querySelector("i");
        btnToggleCall.classList.toggle("btn-end", callActive);
        btnToggleCall.classList.toggle("btn-call", !callActive);
        if (icon) {
            icon.className = callActive ? "fas fa-phone-slash" : "fas fa-phone";
        }
    }

    if (btnMute) {
        btnMute.disabled = !callActive;
        btnMute.classList.toggle("muted", micMuted);
        btnMute.title = micMuted ? "取消靜音" : "靜音";
        btnMute.setAttribute("aria-label", micMuted ? "取消靜音" : "靜音");

        const icon = btnMute.querySelector("i");
        if (icon) {
            icon.className = micMuted
                ? "fas fa-microphone-slash"
                : "fas fa-microphone";
        }
    }

    if (avatarEl) {
        avatarEl.classList.toggle("active", callActive && !micMuted);
    }
};

// ===== 波形（僅做視覺提示，不做實際音量分析） =====
const ensureVisualizerBars = (count = 22) => {
    if (!visualizerEl) return;
    if (bars.length > 0) return;

    const fragment = document.createDocumentFragment();
    for (let i = 0; i < count; i++) {
        const bar = document.createElement("div");
        bar.className = "bar";
        bar.style.height = "4px";
        bars.push(bar);
        fragment.appendChild(bar);
    }
    visualizerEl.appendChild(fragment);
};

const stopVisualizer = () => {
    if (visualizerTimer) {
        window.clearInterval(visualizerTimer);
        visualizerTimer = null;
    }
    for (const bar of bars) bar.style.height = "4px";
};

const startVisualizer = () => {
    ensureVisualizerBars();
    stopVisualizer();

    visualizerTimer = window.setInterval(() => {
        const max = micMuted ? 10 : 44;
        const min = 4;
        for (let i = 0; i < bars.length; i++) {
            const jitter = Math.random();
            const height = Math.round(min + jitter * (max - min));
            bars[i].style.height = `${height}px`;
        }
    }, 110);
};

// ===== 事件 =====
btnToggleCall?.addEventListener("click", async () => {
    if (!callActive) {
        callActive = true;
        micMuted = false;
        setPillState(wsPillEl, "warn", wsTextEl, "連線中");
        setPillState(micPillEl, "ok", micTextEl, "麥克風開啟");
        setCallStatus("正在建立連線…");
        startVisualizer();
        setButtons();

        const systemPrompt = (systemPromptEl?.value || "").trim();
        if (systemPrompt) {
            setText(helperTextEl, "已設定 system prompt（尚未送出）");
        } else {
            setText(helperTextEl, "未設定 system prompt");
        }

        connectWs()
            .then((sid) => {
                setPillState(wsPillEl, "ok", wsTextEl, "已連線");
                setCallStatus("已建立連線");
                setText(helperTextEl, `session: ${sid}`);
                console.log("WebSocket connected with session:", sid);
                session = sid;
                return startRecording();
            })
            .catch((e) => {
                console.error(e);
                stopRecording();
                closeWs();
                callActive = false;
                micMuted = false;
                stopVisualizer();
                setPillState(wsPillEl, "bad", wsTextEl, "連線失敗");
                setPillState(micPillEl, "warn", micTextEl, "未開始");
                setCallStatus("連線失敗");
                setButtons();
            });
    } else {
        callActive = false;
        micMuted = false;

        await stopRecording();

        closeWs();
        stopVisualizer();
        setPillState(wsPillEl, "warn", wsTextEl, "已離線");
        setPillState(micPillEl, "warn", micTextEl, "未開始");
        setCallStatus("通話已結束");
        session = null;
        setText(helperTextEl, "點擊電話按鈕開始；若後端未啟動，狀態會顯示為連線失敗。");
        setButtons();
    }
});

btnMute?.addEventListener("click", () => {
    if (!callActive) return;
    micMuted = !micMuted;
    try {
        const track = audioTrack || mediaStream?.getAudioTracks?.()?.[0] || null;
        if (track) track.enabled = !micMuted;
    } catch {}
    setPillState(
        micPillEl,
        micMuted ? "warn" : "ok",
        micTextEl,
        micMuted ? "已靜音" : "麥克風開啟",
    );
    setCallStatus(micMuted ? "通話中（靜音）" : "通話中");
    setButtons();
});

// ===== 初始化 =====
ensureVisualizerBars();
setButtons();
setCallStatus("準備就緒");
