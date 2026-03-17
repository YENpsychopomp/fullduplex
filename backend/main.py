from __future__ import annotations
import os
import asyncio
import json
import io
from dataclasses import dataclass, field
from datetime import datetime
import uuid
import requests
from typing import Optional
from openai import AzureOpenAI
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import logging
from dotenv import load_dotenv
import time
from pydub import AudioSegment
from pydub.utils import mediainfo


load_dotenv()

app = FastAPI(title="full-duplexV3", version="0.1.0")
logger = logging.getLogger("uvicorn.error")

GROQ_API_URL = os.getenv(
    "GROQ_API_URL",
    "https://api.groq.com/openai/v1/audio/transcriptions"
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    logger.warning("GROQ_API_KEY not found in environment variables")

#  AzureOpenAI API 設定（環境變數）
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

if not AZURE_OPENAI_ENDPOINT or not AZURE_OPENAI_API_KEY:
    logger.warning("Azure OpenAI credentials not found in environment variables")

llm_client = AzureOpenAI(
    api_version="2025-01-01-preview",
    azure_endpoint=AZURE_OPENAI_ENDPOINT,
    api_key=AZURE_OPENAI_API_KEY,
) if AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY else None

# === 固定 TTS 音色與輸出格式 ===
# 音色固定（依需求）：zh-TW-YunJheNeural
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "zh-TW-YunJheNeural")

EDGE_TTS_TRY_FORMATS = [
    "webm-24khz-16kbitrate-mono-opus",
    "webm-24khz-24kbitrate-mono-opus",
    "audio-24khz-48kbitrate-mono-mp3",
    "audio-16khz-32kbitrate-mono-mp3",
]

GROQ_STT_MODEL = os.getenv("GROQ_STT_MODEL", "whisper-large-v3-turbo")
STT_SILENCE_SECONDS = float(os.getenv("STT_SILENCE_SECONDS", "1.2"))
STT_MIN_AUDIO_BYTES = int(os.getenv("STT_MIN_AUDIO_BYTES", "2048"))
VAD_SILENCE_TIMEOUT = float(os.getenv("VAD_SILENCE_TIMEOUT", "1.0")) # 靜音 1 秒視為結束
VAD_ENERGY_THRESHOLD = int(os.getenv("VAD_ENERGY_THRESHOLD", "-40")) # dBFS 音量閾值 (需視麥克風調整)

class _HTTPOnlyStaticFiles:
    def __init__(self, static_app: StaticFiles):
        self._static_app = static_app

    async def __call__(self, scope, receive, send):
        """
        對 StaticFiles 進行封裝，以確保只處理 HTTP 請求。
        """
        if scope.get("type") != "http":
            if scope.get("type") == "websocket":
                await send({"type": "websocket.close", "code": 1000})
            return
        await self._static_app(scope, receive, send)


@dataclass
class SessionState:
    connected: bool = True
    audio_header: bytes = b""
    audio_buffer: bytearray = field(default_factory=bytearray)
    audio_ext: str = ".webm"
    stt_task: Optional[asyncio.Task] = None
    prompt_context: str = ""
    last_speech_time: float = 0.0
    is_speaking: bool = False
        
class SessionManager:
    """
    管理 WebSocket session 相關所有狀態。
    """
    def __init__(self):
        self.sessions: dict[uuid.UUID, SessionState] = {}

    def open_session(self, session_id: uuid.UUID) -> None:
        self.sessions[session_id] = SessionState(connected=True)

    def _get_state(self, session_id: uuid.UUID) -> SessionState:
        state = self.sessions.get(session_id)
        if state is None:
            state = SessionState(connected=True)
            self.sessions[session_id] = state
        return state

    def closeSession(self, session_id: uuid.UUID) -> None:
        state = self.sessions.pop(session_id, None)
        if not state:
            return
        if state.stt_task and not state.stt_task.done():
            state.stt_task.cancel()

    def store_chunk(self, session_id: uuid.UUID, chunk: bytes, ext: str) -> None:
        state = self._get_state(session_id)
        if not state.audio_header:
            state.audio_header = chunk
            state.audio_ext = ext
            state.audio_buffer.clear()
        else:
            state.audio_buffer.extend(chunk)

    def get_combined_audio(self, session_id: uuid.UUID) -> tuple[bytes, str]:
        state = self.sessions.get(session_id)
        if not state:
            return b"", ".webm"
        return state.audio_header + bytes(state.audio_buffer), state.audio_ext

    def clear_audio_buffer(self, session_id: uuid.UUID) -> None:
        state = self.sessions.get(session_id)
        if state:
            state.audio_buffer.clear()

    def get_buffer_size(self, session_id: uuid.UUID) -> int:
        state = self.sessions.get(session_id)
        return len(state.audio_buffer) if state else 0

    def set_stt_task(self, session_id: uuid.UUID, task: asyncio.Task) -> None:
        state = self._get_state(session_id)
        state.stt_task = task

    def get_prompt_context(self, session_id: uuid.UUID) -> str:
        state = self.sessions.get(session_id)
        return state.prompt_context if state else ""

    def set_prompt_context(self, session_id: uuid.UUID, text: str) -> None:
        state = self._get_state(session_id)
        state.prompt_context = text

    def mark_speaking(self, session_id: uuid.UUID, now_ts: float) -> None:
        state = self._get_state(session_id)
        state.is_speaking = True
        state.last_speech_time = now_ts

    def get_vad_state(self, session_id: uuid.UUID, now_ts: float) -> tuple[bool, float]:
        state = self.sessions.get(session_id)
        if not state:
            return False, now_ts
        speaking = state.is_speaking
        last_ts = state.last_speech_time if state.last_speech_time > 0 else now_ts
        return speaking, last_ts

    def reset_speaking(self, session_id: uuid.UUID) -> None:
        state = self.sessions.get(session_id)
        if state:
            state.is_speaking = False


session_manager = SessionManager()

def _guess_container_ext(first_chunk: bytes) -> str:
    """根據音頻流的第一個塊猜測容器格式，這對於向 GROQ API 發送正確的文件類型很重要。"""
    # WebM (EBML): 1A 45 DF A3
    if len(first_chunk) >= 4 and first_chunk[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    # Ogg: 'OggS'
    if len(first_chunk) >= 4 and first_chunk[:4] == b"OggS":
        return ".ogg"
    # Fallback: unknown binary container
    return ".bin"


def _guess_mime_by_ext(ext: str) -> str:
    """根據副檔名猜測 MIME 類型，這對於向 GROQ API 發送正確的文件類型很重要。"""
    if ext == ".webm":
        return "audio/webm"
    if ext == ".ogg":
        return "audio/ogg"
    return "application/octet-stream"


def _store_stream_chunk(session_id: uuid.UUID, chunk: bytes) -> None:
    """
    將接收到的音頻塊存儲在內存中。
    第一個塊 (Header) 會被獨立保存，後續塊存入 buffer。
    """
    session_manager.store_chunk(session_id, chunk, _guess_container_ext(chunk))


def _transcribe_audio_bytes(audio_bytes: bytes, ext: str, prompt: str = "") -> dict:
    """將音頻字節發送到 GROQ STT API 進行轉錄，並返回轉錄結果。"""
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")

    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    data = {
        "model": GROQ_STT_MODEL,
        "response_format": "verbose_json",
        "temperature": 0,
    }
    cleaned_prompt = (prompt or "").strip()
    if cleaned_prompt:
        data["prompt"] = cleaned_prompt[:500]

    filename = f"audio{ext or '.webm'}"
    bio = io.BytesIO(audio_bytes)
    files = {
        "file": (filename, bio, _guess_mime_by_ext(ext)),
    }
    logger.info(f"Sending audio to GROQ STT API: {len(audio_bytes)} bytes, ext={ext}, prompt_len={len(cleaned_prompt)}")
    resp = requests.post(
        GROQ_API_URL,
        headers=headers,
        data=data,
        files=files,
        timeout=90,
    )
    if not resp.ok:
        detail = (resp.text or "").strip()
        raise RuntimeError(f"groq stt failed: {resp.status_code} {detail[:500]}")
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("unexpected STT response format")
    text = (body.get("text") or "").strip()
    logger.info(f"GROQ STT API response: text_len={len(text)}, full_response_keys={list(body.keys())}")
    logger.info(f"text: {text[:200]}")
    return {
        "text": text,
        "raw": body,
    }

def _check_speech_presence(audio_bytes: bytes, ext: str) -> bool:
    """
    使用 PyDub 測量音頻能量 (dBFS) 來做簡易 VAD。
    """
    try:
        # 將 bytes 轉為 AudioSegment
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format=ext.replace('.', ''))
        # 取得最後 0.5 秒的音量 (避免每次都重算整個 buffer)
        recent_audio = audio[-500:] if len(audio) > 500 else audio
        
        # dBFS 越接近 0 越大聲，純靜音通常在 -60 到 -100 之間
        return recent_audio.dBFS > VAD_ENERGY_THRESHOLD
    except Exception as e:
        # 如果 WebM header 不完整導致解碼失敗，預設當作有聲音，繼續累積
        return True

async def _vad_stt_loop(ws: WebSocket, session_id: uuid.UUID, system_prompt: str) -> None:
    """
    取代原本的 _periodic_stt_loop。
    以高頻率 (0.1s) 監控音頻狀態，基於人類行為學參數決定何時觸發 STT。
    """
    while True:
        await asyncio.sleep(0.1)  # 高頻輪詢狀態

        buffer_size = session_manager.get_buffer_size(session_id)
        if buffer_size < STT_MIN_AUDIO_BYTES:
            continue

        audio_bytes, ext = session_manager.get_combined_audio(session_id)

        # 1. 檢測這段音訊內有沒有人類說話的能量
        has_speech = await asyncio.to_thread(_check_speech_presence, audio_bytes, ext)
        current_time = time.time()

        if has_speech:
            # 狀態更新：正在說話，更新最後說話時間
            session_manager.mark_speaking(session_id, current_time)
            continue # 繼續累積音頻，不送 STT

        # 2. 目前沒有說話能量，檢查是否處於「說完後的停頓期」
        is_speaking, last_speech = session_manager.get_vad_state(session_id, current_time)
        silence_duration = current_time - last_speech

        # 3. 判斷：若之前在說話，且靜音超過人類最大猶豫期 (1.0秒)，則判定為「語句結束 (End-of-turn)」
        if is_speaking and silence_duration >= VAD_SILENCE_TIMEOUT:
            session_manager.reset_speaking(session_id) # 重置狀態

            await ws.send_text(json.dumps({"type": "stt.started", "session": str(session_id)}))

            try:
                context_text = session_manager.get_prompt_context(session_id)
                combined_prompt = f"{system_prompt} {context_text}".strip()

                result = await asyncio.to_thread(_transcribe_audio_bytes, audio_bytes, ext, combined_prompt)
                text = result.get("text", "").strip()

                if text:
                    # 清空 Buffer，準備接聽下一句話
                    session_manager.clear_audio_buffer(session_id)
                    session_manager.set_prompt_context(session_id, text)

                    await ws.send_text(json.dumps({
                        "type": "stt.result",
                        "session": str(session_id),
                        "text": text
                    }))
            except Exception as e:
                logger.error(f"STT failed: {e}")

async def _ws_loop(ws: WebSocket, session_id: uuid.UUID, system_prompt: str) -> None:
    """
    這是 WebSocket 循環處理函數，用於處理與客戶端的連接和消息。
    """
    session_manager.open_session(session_id)
    logger.info(f"New WebSocket connection: session {session_id} created with system prompt: {system_prompt}")
    await ws.send_text(
        json.dumps(
            {
                "type": "session",
                "session": str(session_id),
            },
            ensure_ascii=False,
        )
    )

    # STT 改由後端固定週期觸發，不再由前端控制。
    session_manager.set_stt_task(session_id, asyncio.create_task(_vad_stt_loop(ws, session_id, system_prompt)))

    try:
        while True:
            try:
                message = await ws.receive()
            except RuntimeError as e:
                # Starlette raises when receive() is called after a disconnect was already received.
                if "disconnect" in str(e).lower():
                    break
                raise

            msg_type = message.get("type")
            if msg_type == "websocket.disconnect":
                break

            data_bytes = message.get("bytes")
            data_text = message.get("text")
            # 優先處理二進位數據，因為前端會以二進位格式發送音頻塊。
            if isinstance(data_bytes, (bytes, bytearray)):
                # 將接收到的音頻塊存儲在內存中，STT 由固定週期任務處理。
                _store_stream_chunk(session_id, bytes(data_bytes))
                continue

            if not isinstance(data_text, str):
                continue

            try:
                msg = json.loads(data_text)
            except json.JSONDecodeError:
                await ws.send_text(
                    json.dumps({"type": "error", "message": "invalid json"}, ensure_ascii=False)
                )
                continue
            # 處理 ping 消息，回應 pong
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "pong",
                            "session": str(session_id),
                            "ts": datetime.now().isoformat(),
                        },
                        ensure_ascii=False,
                    )
                )
                continue

    except WebSocketDisconnect:
        logger.warning(f"WebSocket disconnected: session {session_id} closed")
    finally:
        session_manager.closeSession(session_id)
        logger.info(f"Active sessions: {len(session_manager.sessions)}")


@app.websocket("/ws/{system_prompt}")
async def ws_new_session(ws: WebSocket, system_prompt: str) -> None:
    """這個 WebSocket 端點用於新的會話連接，會自動生成一個新的 session UUID 並開始處理消息。"""
    await ws.accept()
    session_id = uuid.uuid4()
    await _ws_loop(ws, session_id, system_prompt)


@app.websocket("/ws/{session}/{system_prompt}")
async def ws_existing_session(ws: WebSocket, session: str, system_prompt: str) -> None:
    """這個 WebSocket 端點用於已存在的會話連接。"""
    await ws.accept()
    try:
        session_id = uuid.UUID(session)
    except Exception:
        await ws.send_text(
            json.dumps(
                {"type": "error", "message": "invalid session uuid"},
                ensure_ascii=False,
            )
        )
        await ws.close(code=1008)
        return

    await _ws_loop(ws, session_id, system_prompt)


# 掛載 frontend 資料夾為根目錄
app.mount("/", _HTTPOnlyStaticFiles(StaticFiles(directory="../frontend", html=True)), name="frontend")