# full-duplexV3

最小可跑的「user → STT → LLM → TTS → user」語音助理 Web Demo：
- 前端：麥克風音訊以 **WebRTC** 串流到後端
- 後端：FastAPI + **aiortc** 接收音訊，並以 WebSocket 即時下發 STT 事件（目前 STT 為 stub）

## 需求
- Python 3.10+

## 啟動
在專案根目錄：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
uvicorn backend.main:app --reload --port 8000
```

打開：
- http://localhost:8000/

測試方式：
- 輸入「房間 ID」（等同 sessionId），按「開始說話」開始串流
- 前端會透過 WS 收到 `stt.partial`（即時逐字稿事件）並更新畫面
- 按「停止」會中斷串流

## 注意事項
- 本版 Demo 走 WebRTC(user↔server) 音訊串流，WebSocket 只用於下發事件。
- `aiortc` 在部分環境安裝可能需要編譯相依套件；若安裝失敗，先確認 Python 版本與 pip wheel 支援。
- 麥克風權限通常需要安全來源（HTTPS）。`localhost` 一般被視為安全來源。

## 檔案
- backend/main.py：HTTP API（`/api/voice`, `/api/chat`）+ 靜態頁面
- frontend/index.html / frontend/index.js：WebRTC 串流 → WS 收 STT 事件 + UI
- frontend/style.css：樣式

## API（目前用到）
- `POST /api/webrtc/offer`：WebRTC offer/answer（前端送 offer SDP，後端回 answer SDP）
- `WS /ws/{session_id}/{client_id}`：事件通道（例如 `stt.partial`）
