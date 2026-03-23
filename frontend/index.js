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
let recorder = null;
let recordInterval = null;
let currentAudio = null; // 加在檔案前方的全域變數區

// ===== Audio streaming =====
let mediaStream = null;
let mediaRecorder = null;
let audioTrack = null;
let ttsIncoming = {
    active: false,
    chunks: [],
    mime: "audio/webm",
    ext: ".webm",
    expectedSize: 0,
};

const OPUS_CHUNK_MS = 100;

const mimeByExt = (ext) => {
    if (ext === ".webm") return "audio/webm";
    if (ext === ".ogg") return "audio/ogg";
    if (ext === ".mp3") return "audio/mpeg";
    return "application/octet-stream";
};

const resetIncomingTts = () => {
    ttsIncoming.active = false;
    ttsIncoming.chunks = [];
    ttsIncoming.mime = "audio/webm";
    ttsIncoming.ext = ".webm";
    ttsIncoming.expectedSize = 0;
};

const playIncomingTts = () => {
    return new Promise((resolve) => {
        if (!ttsIncoming.chunks.length) {
            resolve();
            return;
        }

        const blob = new Blob(ttsIncoming.chunks, { type: ttsIncoming.mime });
        const url = URL.createObjectURL(blob);

        // 如果有正在播放的聲音，先停止它
        if (currentAudio) {
            currentAudio.pause();
            currentAudio = null;
        }

        currentAudio = new Audio(url);

        // 🌟 關鍵：只有播完才會觸發 resolve
        currentAudio.onended = () => {
            URL.revokeObjectURL(url);
            console.log("Audio playback finished.");
            resolve();
        };

        // 容錯處理：如果播放失敗，也要 resolve 否則程式會卡死在那
        currentAudio.onerror = (e) => {
            console.error("Audio error:", e);
            URL.revokeObjectURL(url);
            resolve();
        };

        currentAudio.play().catch(err => {
            console.error("Playback blocked by browser:", err);
            resolve();
        });
    });
};

// ===== 小工具 =====
const setText = (el, text) => {
    if (el) el.textContent = text;
};

const setPillState = (pillEl, state, textEl, text) => {
    if (pillEl) pillEl.dataset.state = state;
    setText(textEl, text);
};

const setCallStatus = (text) => setText(callStatusEl, text);

const wsUrl = (sessionOverride) => {
    const isHttps = window.location.protocol === "https:";
    const proto = isHttps ? "wss" : "ws";
    if (sessionOverride) {
        return `${proto}://${window.location.host}/ws/resume/${encodeURIComponent(sessionOverride)}`;
    }
    // 否則走建立新會話的路由 (網址裡面不再需要 systemPrompt)
    return `${proto}://${window.location.host}/ws/new`;
};

const closeWs = () => {
    if (!ws) return;
    stopHeartbeat();
    try {
        ws.close();
    } catch { }
    ws = null;
    session = null;
};

async function startRecording() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        throw new Error("ws not open");
    }

    await stopRecording();

    // 🌟 初始化 Recorder (使用你參考文獻的設定，改為你需要的 24000Hz)
    // 注意：必須設定 compiling: true 才能邊錄邊傳
    recorder = new Recorder({
        sampleBits: 16,        // 16-bit
        sampleRate: 24000,     // 24000Hz (需與後端 pydub 設定一致)
        numChannels: 1,        // 單聲道
        compiling: true        // 允許即時獲取 PCM 數據
    });

    try {
        // 請求麥克風權限並開始錄音
        await recorder.start();
        console.log("🎤 錄音已開始 (24kHz, 16-bit PCM)");

        // 🌟 設定定時器，每 100 毫秒抓取一次新的 PCM 數據送給後端
        recordInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN && !micMuted) {
                // getNextData() 回傳 PCM ArrayBuffer
                let pcmData = recorder.getNextData();
                if (pcmData && pcmData.byteLength > 0) {
                    // 單純傳送 ArrayBuffer，WebSocket 會自動以 Binary Frame 送出
                    ws.send(pcmData);
                }
            }
        }, 100);

    } catch (error) {
        console.error("麥克風啟動失敗或被拒絕:", error);
        throw error;
    }
}

function stopRecording() {
    return new Promise((resolve) => {
        // 清除定時器
        if (recordInterval) {
            clearInterval(recordInterval);
            recordInterval = null;
        }

        // 停止並銷毀錄音實例，釋放記憶體
        if (recorder) {
            recorder.stop();
            recorder.destroy();
            recorder = null;
            console.log("🛑 錄音已停止");
        }
        resolve();
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
        const systemPrompt = (systemPromptEl?.value || "").trim() || "你是一個友善的語音助理，協助使用者解決問題。";
        try {
            if (desiredSession) {
                console.log(`Using user session from input: ${desiredSession}`);
                ws = new WebSocket(wsUrl(desiredSession));
            } else {
                ws = new WebSocket(wsUrl());
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
            } catch { }
            ws = null;
            reject(new Error("ws timeout"));
        }, 6000);

        ws.onopen = () => {
            setPillState(wsPillEl, "warn", wsTextEl, "等待 session…");
            ws.send(JSON.stringify({
                system_prompt: systemPrompt
            }));
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
            if (event.data instanceof Blob) {
                if (ttsIncoming.active) {
                    ttsIncoming.chunks.push(event.data);
                }
                return;
            }

            if (event.data instanceof ArrayBuffer) {
                if (ttsIncoming.active) {
                    ttsIncoming.chunks.push(new Blob([event.data]));
                }
                return;
            }

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
                const message = typeof msg.message === "string"
                    ? msg.message
                    : "ws error";
                if (!settled) {
                    settled = true;
                    window.clearTimeout(timeout);
                    try {
                        ws?.close();
                    } catch { }
                    ws = null;
                    reject(new Error(message));
                }
            }

            if (msg.type === "resume.error") {
                const message = typeof msg.message === "string"
                    ? msg.message
                    : "ws resume error";
                if (!settled) {
                    settled = true;
                    window.clearTimeout(timeout);
                    try {
                        ws?.close();
                    } catch { }
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
                const text = typeof msg.text === "string"
                    ? msg.text.trim()
                    : "";
                console.log("STT result:", text);
                setCallStatus("STT 完成");
                setText(
                    helperTextEl,
                    text ? `STT: ${text}` : "STT 完成（無文字）",
                );
            }

            if (msg.type === "llm.started") {
                console.log("LLM started");
                setCallStatus("LLM 生成中…");
            }

            if (msg.type === "llm.result") {
                const text = typeof msg.text === "string"
                    ? msg.text.trim()
                    : "";
                console.log("LLM result:", text);
                setCallStatus("LLM 完成");
                setText(
                    helperTextEl,
                    text ? `LLM: ${text}` : "LLM 完成（無文字）",
                );
            }

            if (msg.type === "llm.error") {
                const message = typeof msg.message === "string"
                    ? msg.message
                    : "llm error";
                console.warn("LLM error:", message);
                setCallStatus("LLM 失敗");
                setText(helperTextEl, `LLM 錯誤: ${message}`);
            }

            if (msg.type === "tts.started") {
                const ext = typeof msg.ext === "string" ? msg.ext : ".webm";
                const mime = typeof msg.mime === "string"
                    ? msg.mime
                    : mimeByExt(ext);
                resetIncomingTts();
                ttsIncoming.active = true;
                ttsIncoming.ext = ext;
                ttsIncoming.mime = mime;
                ttsIncoming.expectedSize = Number(msg.size || 0);
                setCallStatus("語音合成回傳中…");
            }

            if (msg.type === "tts.completed") {
                const audioSize = ttsIncoming.chunks.reduce(
                    (sum, chunk) => sum + (chunk.size || 0),
                    0,
                );

                setCallStatus("播放回覆語音");
                setText(
                    helperTextEl,
                    `TTS 音訊已接收 (${audioSize} bytes)` +
                    (ttsIncoming.expectedSize ? ` / 預期 ${ttsIncoming.expectedSize} bytes` : ""),
                );

                // 🌟 修改這裡：使用 async 包起來確保「先播完、再發送」
                (async () => {
                    try {
                        await playIncomingTts(); // 這行會卡住，直到聲音播完
                    } finally {
                        // 播完後，才發送 ws 訊息
                        if (ws && ws.readyState === WebSocket.OPEN) {
                            console.log("發送 tts.played 給後端");
                            ws.send(JSON.stringify({ type: "tts.played" }));
                        }
                        resetIncomingTts();
                    }
                })();
            }

            if (msg.type === "tts.error") {
                const message = typeof msg.message === "string"
                    ? msg.message
                    : "tts error";
                console.warn("TTS error:", message);
                setCallStatus("TTS 失敗");
                setText(helperTextEl, `TTS 錯誤: ${message}`);
                resetIncomingTts();
            }

            if (msg.type === "stt.error") {
                const message = typeof msg.message === "string"
                    ? msg.message
                    : "stt error";
                console.warn("STT error:", message);
                setCallStatus("STT 失敗");
                setText(helperTextEl, `STT 錯誤: ${message}`);
            }

            if (msg.type === "interrupt") {
                console.log("🛑 Received interrupt from server");
                setCallStatus("通話中 (AI 被打斷)");
                setText(helperTextEl, "偵測到您說話，已停止 AI 回覆");

                // 1. 停止目前正在播放的音訊
                if (typeof currentAudio !== 'undefined' && currentAudio) {
                    currentAudio.pause();
                    currentAudio.currentTime = 0; // 將進度條歸零
                    currentAudio = null;          // 清除記憶體參考
                }

                // 2. 重置/清空 TTS 接收緩衝區，把還沒播出來的片段全部丟掉
                ttsIncoming = {
                    active: false,
                    chunks: [],
                    mime: "audio/webm", // 這裡請對齊你原本的預設格式
                    ext: ".webm",
                    expectedSize: 0,
                };
                return; // 提早結束，不繼續執行後面的邏輯
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
        setText(
            helperTextEl,
            "點擊電話按鈕開始；若後端未啟動，狀態會顯示為連線失敗。",
        );
        setButtons();
    }
});

btnMute?.addEventListener("click", () => {
    if (!callActive) return;
    micMuted = !micMuted;
    try {
        const track = audioTrack || mediaStream?.getAudioTracks?.()?.[0] ||
            null;
        if (track) track.enabled = !micMuted;
    } catch { }
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
