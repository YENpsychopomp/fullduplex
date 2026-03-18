# full-duplexV3

以瀏覽器麥克風串流到後端，實作語音助理 Demo 的最小可跑版本。

目前主流程：
1. 前端用 MediaRecorder 採集 Opus 音訊並透過 WebSocket 持續送出
2. 後端以 VAD 概念判斷語句結束
3. 後端呼叫 Groq STT 轉文字
4. 後端呼叫 Azure OpenAI 產生 LLM 回覆
5. 後端回傳 STT 與 LLM 結果給前端

## 目前能力

- WebSocket Session 建立與重連（可指定既有 session）
- 前端 Opus 串流上傳（100ms chunk）
- 後端 SessionState 管理（dataclass）
- 後端 VAD + STT（Groq Whisper）
- 後端 LLM 回覆（Azure OpenAI）
- 前端即時顯示 STT / LLM 狀態與結果

## 專案結構

- backend/main.py：FastAPI + WebSocket + VAD/STT
- backend/requirements.txt：Python 套件
- frontend/index.html：頁面結構
- frontend/index.js：WS 連線、錄音、UI 狀態
- frontend/style.css：樣式

## 執行需求

- Python 3.10+
- FFmpeg（PyDub 解碼需要）
- 可用的 Groq API Key
- 可用的 Azure OpenAI 設定（目前程式已初始化 LLM client）

## 安裝與啟動

在專案根目錄執行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

# 若 requirements 尚未包含以下套件，請另外安裝
pip install python-dotenv pydub langchain-openai langchain-core langchain
```

啟動後端（兩種方式擇一）：

```powershell
# 在專案根目錄
uvicorn backend.main:app --reload --port 8000
```

```powershell
# 或在 backend 目錄
uvicorn main:app --reload --port 8000
```

開啟：

- http://localhost:8000/

## 必要環境變數

在 backend/.env 設定：

```env
GROQ_API_KEY=your_groq_api_key
GROQ_API_URL=https://api.groq.com/openai/v1/audio/transcriptions
GROQ_STT_MODEL=whisper-large-v3-turbo

AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_CHAT_DEPLOYMENT_NAME=...
OPENAI_API_VERSION=...

# VAD / STT 參數（可選）
STT_SILENCE_SECONDS=1.2
STT_MIN_AUDIO_BYTES=2048
VAD_SILENCE_TIMEOUT=1.0
VAD_ENERGY_THRESHOLD=-40
```

## WebSocket API

### 建立新 session

- 路徑：/ws/{system_prompt}

### 連線既有 session

- 路徑：/ws/{session}/{system_prompt}

### 前端送出

- binary：Opus 音訊片段
- text(JSON)：
	- {"type":"ping"}

### 後端回傳

- {"type":"session","session":"..."}
- {"type":"pong","session":"...","ts":"..."}
- {"type":"stt.started","session":"..."}
- {"type":"stt.result","session":"...","text":"..."}
- {"type":"stt.error","session":"...","message":"..."}
- {"type":"llm.started","session":"..."}
- {"type":"llm.result","session":"...","text":"..."}
- {"type":"llm.error","session":"...","message":"..."}

## 前端操作流程

1. 開啟頁面
2. 可輸入 system prompt 與既有 session（可選）
3. 按下通話按鈕開始串流
4. 說話後停頓，後端觸發 STT
5. 在畫面上查看 STT 結果

## 常見問題

### 1) uvicorn 無法啟動

- 先確認你在正確目錄執行
- 若在 backend 目錄，請用 uvicorn main:app --reload --port 8000
- 若在專案根目錄，請用 uvicorn backend.main:app --reload --port 8000

### 2) STT 400 Bad Request

- 檢查 GROQ_API_KEY 是否正確
- 檢查音訊是否有實際收進來（瀏覽器麥克風權限）
- 降低 VAD_ENERGY_THRESHOLD（例如 -45 或 -50）重試

### 3) 沒有 STT 結果

- 說話後停頓至少 VAD_SILENCE_TIMEOUT 秒
- 檢查 STT_MIN_AUDIO_BYTES 是否過大
- 檢查 FFmpeg 是否已安裝並可被系統找到

## 下一步建議

- 接上 tts.result 的完整雙向語音回路
- 將 session 路由改為 query 參數承載 system prompt，避免 path encode 問題
- 補上結構化 logger（session_id、msg.type、latency）
