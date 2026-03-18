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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
import logging
from dotenv import load_dotenv
import time
from pydub import AudioSegment
import webrtcvad
from pydub.utils import mediainfo
from langchain_openai import AzureChatOpenAI
from langchain.agents import create_agent
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

# ==================== 導入自定義 TTS 模組 ====================
from tts_handler import TTSProcessor, EdgeTTSProvider, MarkdownCleaner, TTSTextSplitter
from audio_utils import AudioValidator, AudioProcessor, AudioQualityChecker
from tts_config import EDGE_TTS_CONFIG, AUDIO_CONFIG, DEFAULTS, validate_config

load_dotenv()

app = FastAPI(title="full-duplexV3", version="0.1.0")
logger = logging.getLogger("uvicorn.error")
llm = AzureChatOpenAI(
    azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT"),
    temperature=0.7,
    max_tokens=None,
    timeout=None,
    max_retries=2,
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version=os.getenv("OPENAI_API_VERSION"),
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
)

GROQ_API_URL = os.getenv(
    "GROQ_API_URL",
    "https://api.groq.com/openai/v1/audio/transcriptions"
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    logger.warning("GROQ_API_KEY not found in environment variables")

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
SESSION_CLEANUP_INTERVAL_SECONDS = int(os.getenv("SESSION_CLEANUP_INTERVAL_SECONDS", "600"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "1800"))
# === 固定 TTS 音色與輸出格式 ===
# 音色固定（依需求）：zh-TW-YunJheNeural
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "zh-TW-YunJheNeural")
# 優先輸出格式：優先嘗試 WebM/Opus，失敗再回退 MP3
EDGE_TTS_TRY_FORMATS = [
    "webm-24khz-16kbitrate-mono-opus",
    "webm-24khz-24kbitrate-mono-opus",
    "audio-24khz-48kbitrate-mono-mp3",
    "audio-16khz-32kbitrate-mono-mp3",
]

vad = webrtcvad.Vad(3)

# 相關工具參數引入驗證
if not validate_config():
    logger.error("TTS 配置驗證失敗，請檢查配置文件")

# 初始化 TTS 提供者
tts_provider = EdgeTTSProvider(
    voice=EDGE_TTS_CONFIG.get("voice", "zh-TW-YunJheNeural"),
    output_dir=AUDIO_CONFIG.get("output_dir", "response")
)

# 初始化 TTS 處理器
tts_processor = TTSProcessor(
    tts_provider=tts_provider,
    voice=EDGE_TTS_CONFIG.get("voice", "zh-TW-YunJheNeural"),
    output_dir=AUDIO_CONFIG.get("output_dir", "response")
)

# 初始化音頻處理器
audio_processor = AudioProcessor(
    temp_dir=AUDIO_CONFIG.get("temp_dir", "temp_audio")
)

# 初始化音頻品質檢查器
quality_checker = AudioQualityChecker()

# 🔧 以 Edge TTS 生成語音檔（優先 WebM/Opus，失敗回退 MP3）
async def synthesize_tts_edge(reply_text: str, record_id: str) -> tuple[str, str]:
    """
    使用 Edge TTS 將文字轉換為語音並保存為音檔
    
    **參考實現方式：**
    - 參考 edge.py 的 text_to_speak() 方法
    - 參考 base.py 的文本清理和分段邏輯
    - 參考 connection.py 的 TTS 初始化方式
    
    **功能特點：**
    1. 文本 Markdown 清理（移除標記，確保 TTS 輸出清晰）
    2. 多格式支持（優先 WebM/Opus，自動回退 MP3）
    3. 自動重試機制（若格式不支持則嘗試下一個）
    4. 檔案驗證（確保生成的音檔有效且非空）
    
    Args:
        reply_text: 要轉換的文本
        record_id: 錄音 ID（用於檔案命名）
        
    Returns:
        (status, filename_or_data)
        - status: "completed"（成功）或 "error"（失敗）
        - filename_or_data: 成功時為檔案名，失敗時為 None
    """
    try:
        # 使用 TTS 處理器進行完整流程（包括文本清理、分段、生成）
        status, filename = await tts_processor.process_text(reply_text, record_id)
        
        if status == "completed" and filename:
            # 執行音頻品質檢查
            filepath = os.path.join(AUDIO_CONFIG.get("output_dir", "response"), filename)
            quality_result = quality_checker.check_audio_quality(filepath)
            
            if quality_result["is_valid"]:
                logger.info(f"TTS 音頻品質檢查通過: {filename}")
                return "completed", filename
            else:
                logger.warning(f"TTS 音頻品質問題: {quality_result['issues']}")
                # 即使有警告，若檔案存在仍返回成功（由客戶端決定是否使用）
                if os.path.exists(filepath):
                    return "completed", filename
        
        return "error", None
        
    except Exception as e:
        logger.error(f"TTS 生成失敗: {str(e)}", exc_info=True)
        return "error", None

# ==================== 核心 TTS 輔助函數 ====================
def _split_tts_text(text: str, max_chars: int = 500) -> list:
    """
    將文本分段以適應 TTS API 的限制
    
    **參考實現：**
    - 參考 base.py 的 _get_segment_text() 和標點符號分割邏輯
    - 使用 TTSTextSplitter 類進行分段
    
    Args:
        text: 要分段的文本
        max_chars: 每段最大字符數
    
    Returns:
        分段後的文本列表
    """
    return TTSTextSplitter.split_text(text, max_chars)

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
    tts_header: bytes = b""
    tts_buffer: bytearray = field(default_factory=bytearray)
    tts_ext: str = ".webm"
    stt_task: Optional[asyncio.Task] = None
    system_prompt: str = ""
    prompt_context: str = ""
    paused_at: Optional[float] = None
    last_speech_time: float = 0.0
    is_speaking: bool = False
    history: list[HumanMessage | AIMessage] = field(default_factory=list)
        
class SessionManager:
    """
    管理 WebSocket session 相關所有狀態。
    """
    def __init__(self):
        self.sessions: dict[uuid.UUID, SessionState] = {}
        self.history = []
        self.history_limit = 20  # 每個 session 最多保留的歷史訊息數量

    def open_session(self, session_id: uuid.UUID, system_prompt: str) -> None:
        if session_id in self.sessions:
            # 如果 session 已經存在，只更新連線狀態與 Prompt，保留歷史紀錄與緩衝
            self.sessions[session_id].connected = True
            self.sessions[session_id].system_prompt = system_prompt
            self.sessions[session_id].paused_at = None
        else:
            # 新的 session 才實例化 SessionState
            self.sessions[session_id] = SessionState(connected=True, system_prompt=system_prompt)

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
    
    def pause_session(self, session_id: uuid.UUID) -> None:
        state = self.sessions.get(session_id)
        if state:
            state.connected = False
            state.paused_at = time.time()
            if state.stt_task and not state.stt_task.done():
                state.stt_task.cancel()

    def cleanup_expired_sessions(self, now_ts: float, ttl_seconds: int) -> int:
        """清理已 pause 且超過 TTL 的 session。"""
        expired_session_ids = []
        for session_id, state in list(self.sessions.items()):
            if state.connected:
                continue
            if state.paused_at is None:
                continue
            if now_ts - state.paused_at >= ttl_seconds:
                expired_session_ids.append(session_id)

        for session_id in expired_session_ids:
            self.closeSession(session_id)

        return len(expired_session_ids)

    def store_chunk(self, session_id: uuid.UUID, chunk: bytes, ext: str) -> None:
        state = self._get_state(session_id)
        if not state.audio_header:
            state.audio_header = chunk
            state.audio_ext = ext
            state.audio_buffer.clear()
        else:
            MAX_BUFFER_SIZE = 10 * 1024 * 1024 
            if len(state.audio_buffer) + len(chunk) > MAX_BUFFER_SIZE:
                logger.warning(f"Session {session_id} buffer overflow, clearing silence...")
                state.audio_buffer.clear()
            state.audio_buffer.extend(chunk)

    def get_combined_audio(self, session_id: uuid.UUID) -> tuple[bytes, str]:
        state = self.sessions.get(session_id)
        if not state:
            return b"", ".webm"
        return state.audio_header + bytes(state.audio_buffer), state.audio_ext

    def clear_audio_buffer(self, session_id: uuid.UUID, size: int = -1) -> None:
        state = self.sessions.get(session_id)
        if state:
            if size == -1:
                state.audio_buffer.clear()
            else:
                del state.audio_buffer[:size]

    def get_buffer_size(self, session_id: uuid.UUID) -> int:
        state = self.sessions.get(session_id)
        return len(state.audio_buffer) if state else 0

    def store_tts_chunk(self, session_id: uuid.UUID, chunk: bytes, ext: str) -> None:
        state = self._get_state(session_id)
        if not state.tts_header:
            state.tts_header = chunk
            state.tts_ext = ext
            state.tts_buffer.clear()
        else:
            max_buffer_size = 10 * 1024 * 1024
            if len(state.tts_buffer) + len(chunk) > max_buffer_size:
                logger.warning(f"Session {session_id} TTS buffer overflow, clearing old data")
                state.tts_buffer.clear()
            state.tts_buffer.extend(chunk)

    def get_combined_tts_audio(self, session_id: uuid.UUID) -> tuple[bytes, str]:
        state = self.sessions.get(session_id)
        if not state:
            return b"", ".webm"
        return state.tts_header + bytes(state.tts_buffer), state.tts_ext

    def clear_tts_audio_buffer(self, session_id: uuid.UUID, size: int = -1) -> None:
        state = self.sessions.get(session_id)
        if not state:
            return
        if size == -1:
            state.tts_header = b""
            state.tts_buffer.clear()
            return
        if size <= len(state.tts_header):
            state.tts_header = state.tts_header[size:]
            return
        buffer_offset = size - len(state.tts_header)
        state.tts_header = b""
        del state.tts_buffer[:buffer_offset]

    def set_stt_task(self, session_id: uuid.UUID, task: asyncio.Task) -> None:
        state = self._get_state(session_id)
        state.stt_task = task

    def get_prompt_context(self, session_id: uuid.UUID) -> str:
        state = self.sessions.get(session_id)
        return state.prompt_context if state else ""
    
    def set_prompt_context(self, session_id: uuid.UUID, context: str) -> None:
        state = self.sessions.get(session_id)
        if state:
            state.prompt_context = context

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
    
    def get_session_history(self, session_id: uuid.UUID) -> list[tuple[str, str]]:
        state = self.sessions.get(session_id)
        if not state:
            return []
        # 這裡簡單將 prompt_context 當作最新的 user 輸入，AI 回應則從歷史中推斷（實際應用中可改為更完整的對話歷史管理）
        return state.history
    
    def add_session_message(self, session_id: uuid.UUID, role: str, msg: str) -> None:
        state = self.sessions.get(session_id)
        if not state:
            return
        if role == "user":
            state.history.append(HumanMessage(content=msg))
        elif role == "ai":
            state.history.append(AIMessage(content=msg))
        # 限制歷史長度 
        if len(state.history) > self.history_limit:
            state.history = state.history[-self.history_limit:]
    
    def session_exists(self, session_id: uuid.UUID) -> bool:
        return session_id in self.sessions
            

session_manager = SessionManager()
session_cleanup_task: Optional[asyncio.Task] = None


async def _session_cleanup_loop() -> None:
    """背景巡檢：定期清理 pause 超過 TTL 的 session。"""
    while True:
        await asyncio.sleep(SESSION_CLEANUP_INTERVAL_SECONDS)
        removed = session_manager.cleanup_expired_sessions(time.time(), SESSION_TTL_SECONDS)
        if removed > 0:
            logger.info(
                f"Session cleanup removed {removed} expired sessions "
                f"(ttl={SESSION_TTL_SECONDS}s)"
            )


@app.on_event("startup")
async def _startup_session_cleanup_task() -> None:
    global session_cleanup_task
    if session_cleanup_task is None or session_cleanup_task.done():
        session_cleanup_task = asyncio.create_task(_session_cleanup_loop())


@app.on_event("shutdown")
async def _shutdown_session_cleanup_task() -> None:
    global session_cleanup_task
    if session_cleanup_task and not session_cleanup_task.done():
        session_cleanup_task.cancel()
        try:
            await session_cleanup_task
        except asyncio.CancelledError:
            pass
    session_cleanup_task = None

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
    if ext == ".mp3":
        return "audio/mpeg"
    return "application/octet-stream"


def _store_stream_chunk(session_id: uuid.UUID, chunk: bytes) -> None:
    """
    將接收到的音頻塊存儲在內存中。
    第一個塊 (Header) 會被獨立保存，後續塊存入 buffer。
    """
    session_manager.store_chunk(session_id, chunk, _guess_container_ext(chunk))


def _store_tts_chunk(session_id: uuid.UUID, chunk: bytes, ext: str) -> None:
    """將生成的 TTS 音訊分塊存入 session 狀態。"""
    session_manager.store_tts_chunk(session_id, chunk, ext)


def _cache_tts_file_to_session(session_id: uuid.UUID, filepath: str) -> tuple[int, str]:
    """將 TTS 檔案分塊存入 SessionManager，回傳總長度與副檔名。"""
    with open(filepath, "rb") as f:
        file_bytes = f.read()

    ext = os.path.splitext(filepath)[1].lower() or ".webm"
    session_manager.clear_tts_audio_buffer(session_id)

    if not file_bytes:
        return 0, ext

    chunk_size = 32 * 1024
    for idx in range(0, len(file_bytes), chunk_size):
        _store_tts_chunk(session_id, file_bytes[idx: idx + chunk_size], ext)

    combined, stored_ext = session_manager.get_combined_tts_audio(session_id)
    return len(combined), stored_ext


async def _send_tts_audio_via_ws(ws: WebSocket, session_id: uuid.UUID) -> None:
    """將 session 中暫存的 TTS 音訊以 binary chunks 經由 WS 回傳前端。"""
    tts_bytes, ext = session_manager.get_combined_tts_audio(session_id)
    if not tts_bytes:
        raise RuntimeError("empty tts buffer")

    try:
        await _safe_ws_send(ws, {
            "type": "tts.started",
            "session": str(session_id),
            "ext": ext,
            "mime": _guess_mime_by_ext(ext),
            "size": len(tts_bytes),
        })

        chunk_size = 32 * 1024
        for idx in range(0, len(tts_bytes), chunk_size):
            await ws.send_bytes(tts_bytes[idx: idx + chunk_size])

        await _safe_ws_send(ws, {
            "type": "tts.completed",
            "session": str(session_id),
            "ext": ext,
            "size": len(tts_bytes),
        })
    except (WebSocketDisconnect, RuntimeError):
        # 傳輸中途斷線，安靜處理即可
        pass
    finally:
        session_manager.clear_tts_audio_buffer(session_id)


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
    使用 WebRTC VAD 檢測是否有人類語音特徵，取代單純的音量判定。
    """
    try:
        # 1. 解碼音訊並強制轉換為 WebRTC 要求的格式 (16kHz, 單聲道, 16-bit)
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format=ext.replace('.', ''))
        audio = audio.set_frame_rate(16000).set_channels(1).set_sample_width(2)

        # 2. 確保音訊長度大於 30 毫秒 (WebRTC 支援 10, 20, 30ms 的 frames)
        if len(audio) < 30:
            return False

        # 3. 取得最後 30 毫秒的音訊特徵來判斷當下是否在說話
        recent_audio = audio[-30:]
        raw_data = recent_audio.raw_data

        # 4. 驗證 bytes 長度 (16000 Hz * 2 bytes * 0.03 sec = 960 bytes)
        if len(raw_data) == 960:
            # 檢測其中是否有人聲頻率
            return vad.is_speech(raw_data, 16000)

        return False
    except Exception as e:
        logger.debug(f"WebRTC VAD parsing warning: {e}")
        return False

def _llm_respond(session_id: uuid.UUID, text: str) -> str:
    """
    使用 Azure OpenAI 生成回應，並返回生成的文本。
    conversation_history 是一個 (user, ai) 的列表，用於構建對話上下文。
    """
    state = session_manager.sessions.get(session_id)
    if not state:
        logger.error(f'Session {session_id} not found state, error from "_llm_respond"')
        return ""
    history = state.history
    system_prompt = state.system_prompt
    messages = [SystemMessage(content=system_prompt), *history, HumanMessage(content=text)]
    result = llm.invoke(messages)
    if result and result.content:
        content = result.content if isinstance(result.content, str) else str(result.content)
        session_manager.add_session_message(session_id, "user", text)
        session_manager.add_session_message(session_id, "ai", content)
        return content
    logger.error(f'LLM response is empty or invalid for session {session_id}')
    return ""

async def _safe_ws_send(ws: WebSocket, payload: dict) -> None:
    """安全地發送 WebSocket 訊息，若連線已斷開則忽略"""
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except (WebSocketDisconnect, RuntimeError):
        # 客戶端已斷線，無需處理
        pass

async def _process_ai_pipeline(
    ws: WebSocket, 
    session_id: uuid.UUID, 
    audio_bytes: bytes, 
    ext: str, 
    combined_prompt: str, 
    system_prompt: str,
    current_buffer_size: int
) -> None:
    try:
        # === 1. STT 階段 ===
        result = await asyncio.to_thread(_transcribe_audio_bytes, audio_bytes, ext, combined_prompt)
        text = result.get("text", "").strip()
        
        session_manager.set_prompt_context(session_id, text)
        # 精準刪除剛才拿去處理的音訊，保留用戶可能剛說的新話
        session_manager.clear_audio_buffer(session_id, current_buffer_size)
        
        if not text:
            return

        logger.info(f"STT result for session {session_id}: {text[:200]}")
        await _safe_ws_send(ws, {"type": "stt.result", "session": str(session_id), "text": text})
        await _safe_ws_send(ws, {"type": "llm.started", "session": str(session_id)})

        # === 2. LLM 階段 ===
        llm_text = await asyncio.to_thread(_llm_respond, session_id, text)
        if not llm_text:
            await _safe_ws_send(ws, {"type": "llm.error", "session": str(session_id), "message": "empty llm response"})
            return
            
        await _safe_ws_send(ws, {"type": "llm.result", "session": str(session_id), "text": llm_text})

        # === 3. TTS 階段 ===
        tts_status, tts_filename = await synthesize_tts_edge(llm_text, str(session_id))
        if tts_status == "completed" and tts_filename:
            output_dir = AUDIO_CONFIG.get("output_dir", "response")
            tts_path = os.path.join(output_dir, tts_filename)
            tts_size, _ = await asyncio.to_thread(_cache_tts_file_to_session, session_id, tts_path)
            
            if tts_size > 0:
                await _send_tts_audio_via_ws(ws, session_id)
                try:
                    if os.path.exists(tts_path):
                        os.remove(tts_path)
                        logger.info(f"Deleted temp TTS file: {tts_path}")
                except Exception as e:
                    logger.warning(f"Failed to delete TTS file {tts_path}: {e}")
                    
            else:
                raise RuntimeError("generated tts file is empty")

    except Exception as e:
        logger.error(f"Pipeline failed for session {session_id}: {e}")
        await _safe_ws_send(ws, {"type": "error", "session": str(session_id), "message": str(e)})

async def _vad_stt_loop(ws: WebSocket, session_id: uuid.UUID, system_prompt: str) -> None:
    """
    取代原本的 _periodic_stt_loop。
    以高頻率 (0.1s) 監控音頻狀態，基於人類行為學參數決定何時觸發 STT。
    """
    while True:
        await asyncio.sleep(0.25)  # 高頻輪詢狀態

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

        is_speaking, last_speech = session_manager.get_vad_state(session_id, current_time)
        silence_duration = current_time - last_speech
        current_buffer_size = session_manager.get_buffer_size(session_id)
        if is_speaking and silence_duration >= VAD_SILENCE_TIMEOUT:
            session_manager.reset_speaking(session_id)

            await _safe_ws_send(ws, {"type": "stt.started", "session": str(session_id)})

            context_text = session_manager.get_prompt_context(session_id)
            combined_prompt = f"{system_prompt} {context_text}".strip()

            # 🔥 關鍵修正：將整個耗時流程丟入背景執行，讓 VAD 迴圈立刻回到上方繼續監聽！
            asyncio.create_task(
                _process_ai_pipeline(
                    ws, session_id, audio_bytes, ext, combined_prompt, system_prompt, current_buffer_size
                )
            )

async def _ws_loop(ws: WebSocket, session_id: uuid.UUID, system_prompt: str) -> None:
    """
    這是 WebSocket 循環處理函數，用於處理與客戶端的連接和消息。
    """
    session_manager.open_session(session_id, system_prompt)
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
        # session_manager.closeSession(session_id)
        session_manager.pause_session(session_id)
        logger.info(f"Active sessions: {len(session_manager.sessions)}")

@app.websocket("/ws/new")
async def ws_new_session(ws: WebSocket) -> None:
    """新的會話連接，連線後等待前端傳送初始化 JSON 設定。"""
    await ws.accept()
    
    try:
        # 連線成功後，等待前端發送的第一筆 JSON 資料
        init_data = await ws.receive_json()
        system_prompt = init_data.get("system_prompt", "你是一個有用的助手")
    except Exception as e:
        logger.error(f"無法接收初始化設定: {e}")
        await ws.close(code=1003, reason="Missing initial configuration")
        return

    session_id = uuid.uuid4()
    await _ws_loop(ws, session_id, system_prompt)


@app.websocket("/ws/resume/{session_id}")
async def ws_existing_session(ws: WebSocket, session_id: str) -> None:
    """已存在的會話連接 (斷線重連)"""
    await ws.accept()
    try:
        uid = uuid.UUID(session_id)
    except Exception:
        await ws.send_text(json.dumps(
            {"type": "error", "message": "invalid session uuid"},
            ensure_ascii=False
        ))
        await ws.close(code=1008)
        return

    try:
        init_data = await ws.receive_json()
        system_prompt = init_data.get("system_prompt", "你是一個有用的助手")
    except Exception:
        system_prompt = "你是一個有用的助手"
    session_exists = session_manager.session_exists(uid)
    if not session_exists:
        await ws.send_text(json.dumps(
            {"type": "resume.error", "message": f"session {session_id} not found for resuming"},
            ensure_ascii=False
        ))
        await ws.close(code=1008)
        return
    await _ws_loop(ws, uid, system_prompt)


# 掛載 frontend 資料夾為根目錄
app.mount("/", _HTTPOnlyStaticFiles(StaticFiles(directory="../frontend", html=True)), name="frontend")