# full-duplexV4

以瀏覽器麥克風串流到後端，實作「全雙工」語音助理 Demo 的可跑版本。

目前主流程：
1. 前端用錄音器蒐集 24kHz/16-bit/mono PCM，透過 WebSocket 持續送出
2. 後端以能量偵測 (VAD) 判斷語句結束與即時打斷
3. 後端用本地 Whisper (Breeze ASR) 轉文字
4. 後端呼叫 Azure OpenAI 產生 LLM 回覆
5. 後端以 Edge TTS 合成語音，回傳音訊並在前端播放

## 目前能力

- WebSocket Session 建立與重連（支援指定 UUID）
- 前端 PCM 串流上傳（100ms chunk）
- 後端 SessionState 管理（含對話歷史）
- 後端 VAD + 本地 ASR 轉錄
- Azure OpenAI LLM 回覆
- Edge TTS 合成與前端播放
- 語音打斷機制（使用者說話時中止 AI 回覆）

## 專案結構

- backend/main.py：FastAPI + WebSocket + VAD/STT/LLM/TTS 主流程
- backend/asr.py：本地 Whisper (Breeze ASR) 推論
- backend/tts_handler.py：Edge TTS 管線、文本清理與分段
- backend/tts_config.py：TTS 參數與格式設定
- backend/audio_utils.py：音訊驗證與轉檔工具
- backend/local_breeze_model/：本地 ASR 模型檔
- backend/requirements.txt：Python 套件
- frontend/index.html：頁面結構
- frontend/index.js：WS 連線、PCM 錄音、播放 TTS、UI 狀態
- frontend/style.css：樣式
- frontend/recorder.js：錄音器 (2fps/recorder)

## 執行需求

- Python 3.10+
- FFmpeg（含 ffprobe，用於音訊檢測）
- 可用的 Azure OpenAI 設定
- 本地 ASR 模型（已放在 backend/local_breeze_model/）
- (可選) CUDA GPU 以加速本地 ASR

## 安裝與啟動

在專案根目錄執行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

# 若 requirements 尚未包含以下套件，請另外安裝
pip install python-dotenv pydub langchain-openai langchain-core langchain edge-tts torch transformers librosa
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
AZURE_OPENAI_ENDPOINT=...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT=...
OPENAI_API_VERSION=...

# Edge TTS
EDGE_TTS_VOICE=zh-TW-YunJheNeural
TTS_OUTPUT_DIR=response
TTS_TEMP_DIR=temp_audio

# FFmpeg/ffprobe 路徑（可選）
FFPROBE_PATH=...

# VAD / STT 參數（可選）
STT_MIN_AUDIO_BYTES=2048
VAD_ENERGY_THRESHOLD=-40
SESSION_TTL_SECONDS=1800
SESSION_CLEANUP_INTERVAL_SECONDS=600
```

## WebSocket API

### 建立新 session

- 路徑：/ws/new
- 連線後需先送一筆 JSON：
	- {"system_prompt":"..."}

### 連線既有 session

- 路徑：/ws/resume/{session_id}
- 連線後可送 JSON：
	- {"system_prompt":"..."}

### 前端送出

- binary：PCM 音訊片段 (24kHz/16-bit/mono)
- text(JSON)：
	- {"type":"ping"}
	- {"type":"tts.played"}

### 後端回傳

- {"type":"session","session":"..."}
- {"type":"pong","session":"...","ts":"..."}
- {"type":"stt.started","session":"..."}
- {"type":"stt.result","session":"...","text":"..."}
- {"type":"stt.error","session":"...","message":"..."}
- {"type":"llm.started","session":"..."}
- {"type":"llm.result","session":"...","text":"..."}
- {"type":"llm.error","session":"...","message":"..."}
- {"type":"tts.started","session":"...","ext":".webm","mime":"audio/webm","size":1234}
- (binary chunks) TTS 音訊資料
- {"type":"tts.completed","session":"...","ext":".webm","size":1234}
- {"type":"tts.error","session":"...","message":"..."}
- {"type":"interrupt","session":"..."}
- {"type":"resume.error","message":"..."}

## 前端操作流程

1. 開啟頁面
2. 可輸入 system prompt 與既有 session（可選）
3. 按下通話按鈕開始串流
4. 說話後停頓，後端觸發 STT
5. 在畫面上查看 STT / LLM 結果
6. 播放 TTS 回覆（播放完成會回傳 tts.played）

## 常見問題

### 1) uvicorn 無法啟動

- 先確認你在正確目錄執行
- 若在 backend 目錄，請用 uvicorn main:app --reload --port 8000
- 若在專案根目錄，請用 uvicorn backend.main:app --reload --port 8000

### 2) STT 400 Bad Request

（本專案改用本地 ASR，如無文本回傳可檢查以下項目）

- 瀏覽器麥克風權限是否開啟
- 確認前端送出的 PCM 為 24kHz/16-bit/mono
- 降低 VAD_ENERGY_THRESHOLD（例如 -45 或 -50）重試
- 確認 backend/local_breeze_model/ 模型檔完整

### 3) 沒有 STT 結果

- 說話後停頓至少 VAD_SILENCE_TIMEOUT 秒
- 檢查 STT_MIN_AUDIO_BYTES 是否過大
- 檢查 FFmpeg 是否已安裝並可被系統找到

## 下一步建議

- 支援多輪上下文摘要，減少 prompt 成本
- 補上結構化 logger（session_id、msg.type、latency）
- 加上 TTS streaming（邊生成邊播）
