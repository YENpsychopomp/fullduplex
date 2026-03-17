"""
TTS 處理引擎
參考 xiaozhi-server 的 base.py 和 connection.py 的實現方式
處理文字轉語音的完整流程，包括文本分段、音頻隊列管理、格式轉換等
"""

import os
import re
import uuid
import queue
import threading
import asyncio
import logging
from typing import Callable, Optional, List, Tuple
from datetime import datetime
import edge_tts

# 日誌配置
logger = logging.getLogger("tts_handler")


class MarkdownCleaner:
    """
    Markdown 文本清理器
    參考 base.py 的 MarkdownCleaner 類
    移除 Markdown 標記，確保 TTS 輸出清晰無誤
    """
    
    @staticmethod
    def clean_markdown(text: str) -> str:
        """
        清理文本中的 Markdown 標記
        
        Args:
            text: 原始文本
            
        Returns:
            清理後的文本
        """
        if not text:
            return ""
        
        # 移除代碼塊標記（```）
        text = re.sub(r'```[\s\S]*?```', '', text)
        # 移除行內代碼標記（`）
        text = re.sub(r'`[^`]*`', '', text)
        
        # 移除 HTML 標籤
        text = re.sub(r'<[^>]+>', '', text)
        
        # 移除 Markdown 粗體標記（**text** 和 __text__）
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'__([^_]+)__', r'\1', text)
        
        # 移除 Markdown 斜體標記（*text* 和 _text_）
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        text = re.sub(r'_([^_]+)_', r'\1', text)
        
        # 移除 Markdown 連結標記（[text](url)）
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # 移除 Markdown 標題標記（# 開頭）
        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
        
        # 移除 Markdown 無序列表標記
        text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
        
        # 移除多餘的換行符
        text = re.sub(r'\n+', '\n', text)
        
        # 移除多餘的空白
        text = re.sub(r'\s+', ' ', text).strip()
        
        return text


class TTSTextSplitter:
    """
    TTS 文本分段器
    參考 base.py 的 _get_segment_text() 實現
    根據標點符號和字符限制進行智能分段
    """
    
    # 分段標點符號集合
    PUNCTUATIONS = ("。", "？", "?", "！", "!", "；", ";", "：", ":", "\n", "）", ")", "，")
    FIRST_SENTENCE_PUNCTUATIONS = ("，", "~", "、", ",", "。", "？", "?", "！", "!", "；", ";", "：", ":", "）", ")")
    
    # 單個音頻段落的最大字符數（避免 TTS API 超時）
    MAX_SEGMENT_CHARS = 500
    
    @classmethod
    def split_text(cls, text: str, max_chars: int = None) -> List[str]:
        """
        將文本分段以適應 TTS API 的限制
        
        參考 base.py 的 _get_segment_text() 邏輯，使用標點符號進行分段
        
        Args:
            text: 要分段的文本
            max_chars: 每段最大字符數（默認為 MAX_SEGMENT_CHARS）
            
        Returns:
            分段後的文本列表
        """
        if max_chars is None:
            max_chars = cls.MAX_SEGMENT_CHARS
        
        text = text.strip()
        if not text:
            return []
        
        segments = []
        current_segment = ""
        is_first_sentence = True
        
        for char in text:
            current_segment += char
            
            # 選擇適用的標點符號集合
            punctuations = cls.FIRST_SENTENCE_PUNCTUATIONS if is_first_sentence else cls.PUNCTUATIONS
            
            # 如果遇到標點或達到最大長度，則分段
            if char in punctuations or len(current_segment) >= max_chars:
                segment = current_segment.strip()
                if segment:
                    segments.append(segment)
                    # 第一句話後，改為普通標點符號集合
                    if is_first_sentence and char in cls.FIRST_SENTENCE_PUNCTUATIONS:
                        is_first_sentence = False
                current_segment = ""
        
        # 添加剩餘文本
        if current_segment.strip():
            segments.append(current_segment.strip())
        
        return segments


class EdgeTTSProvider:
    """
    Edge TTS 提供者
    參考 edge.py 的實現方式
    使用 Microsoft Edge 的 TTS 服務進行文字轉語音
    """
    
    # 優先輸出格式（按優先級排序）
    # 注意: Edge TTS 的 Opus 格式已內建正確的 frame duration (20ms)
    OUTPUT_FORMATS = [
        ("webm-24khz-16kbitrate-mono-opus", "webm"),   # 24kHz, 20ms frames
        ("audio-24khz-48kbitrate-mono-mp3", "mp3"),
        ("audio-16khz-32kbitrate-mono-mp3", "mp3"),
    ]
    
    def __init__(
        self,
        voice: str = "zh-TW-YunJheNeural",
        output_dir: str = "response"
    ):
        """
        初始化 Edge TTS 提供者
        
        Args:
            voice: TTS 音色（預設為繁體中文女聲）
            output_dir: 輸出檔案目錄
        """
        self.voice = voice
        self.output_dir = output_dir
        
        # 確保輸出目錄存在
        os.makedirs(output_dir, exist_ok=True)
        
        logger.info(f"EdgeTTSProvider 初始化: voice={voice}, output_dir={output_dir}")
    
    async def generate_audio(
        self,
        text: str,
        record_id: str,
        delete_file: bool = False
    ) -> Tuple[str, Optional[str]]:
        """
        生成語音檔案
        
        參考 edge.py 的 text_to_speak() 方法，支持：
        - 多種輸出格式（WebM/Opus、MP3）
        - 檔案保存或直接返回二進位數據
        - 自動重試機制
        
        Args:
            text: 要轉換的文本
            record_id: 錄音 ID（用於檔案命名）
            delete_file: 是否刪除暫存檔案
            
        Returns:
            (status, filename_or_data)
            - status: "completed" 或 "error"
            - filename_or_data: 若成功則為檔案名，若失敗則為 None
        """
        # 清理文本
        cleaned_text = MarkdownCleaner.clean_markdown(text)
        
        if not cleaned_text:
            logger.warning(f"TTS 文本清理後為空")
            return "error", None
        
        # 嘗試各種輸出格式（優先 WebM/Opus）
        for edge_format, ext in self.OUTPUT_FORMATS:
            filename = f"{record_id}.{ext}"
            filepath = os.path.join(self.output_dir, filename)
            
            try:
                # 清理舊檔案
                if os.path.exists(filepath):
                    os.remove(filepath)
                
                # 建立 Edge TTS 通訊物件
                communicate = edge_tts.Communicate(
                    text=cleaned_text,
                    voice=self.voice
                )
                
                # 嘗試設置輸出格式
                try:
                    communicate._output_format = edge_format
                except Exception as e:
                    logger.debug(f"設置格式失敗（可忽略）: {e}")
                
                # 保存音檔
                await communicate.save(filepath)
                
                # 驗證檔案是否成功生成且非空
                if os.path.exists(filepath) and os.path.getsize(filepath) > 1000:
                    logger.info(f"TTS 生成成功: {filename} (格式: {edge_format}, 大小: {os.path.getsize(filepath)} bytes)")
                    return "completed", filename
                else:
                    logger.warning(f"TTS 檔案異常（為空或過小）: {filepath}")
                    if os.path.exists(filepath):
                        os.remove(filepath)
                    continue
                    
            except Exception as e:
                logger.warning(f"TTS 格式 {edge_format} 失敗: {str(e)}")
                if os.path.exists(filepath):
                    try:
                        os.remove(filepath)
                    except Exception:
                        pass
                continue
        
        logger.error(f"TTS 生成失敗: 所有格式均已嘗試")
        return "error", None


class TTSQueue:
    """
    TTS 音頻隊列管理
    參考 base.py 的 tts_audio_queue 管理方式
    管理 TTS 文本隊列和音頻隊列，支持優先級處理
    """
    
    def __init__(self, maxsize: int = 0):
        """
        初始化隊列
        
        Args:
            maxsize: 隊列最大容量（0 為無限制）
        """
        self.text_queue = queue.Queue(maxsize=maxsize)
        self.audio_queue = queue.Queue(maxsize=maxsize)
    
    def put_text(self, text: str, priority: int = 0):
        """
        添加文本到隊列
        
        Args:
            text: 要轉換的文本
            priority: 優先級（0 為普通）
        """
        self.text_queue.put((priority, text))
    
    def get_text(self, timeout: Optional[float] = None) -> str:
        """
        從隊列獲取文本
        
        Args:
            timeout: 超時時間（秒）
            
        Returns:
            文本內容
            
        Raises:
            queue.Empty: 隊列為空且超時
        """
        priority, text = self.text_queue.get(timeout=timeout)
        return text
    
    def put_audio(self, audio_data: bytes):
        """
        添加音頻到隊列
        
        Args:
            audio_data: 音頻二進位數據
        """
        self.audio_queue.put(audio_data)
    
    def get_audio(self, timeout: Optional[float] = None) -> bytes:
        """
        從隊列獲取音頻
        
        Args:
            timeout: 超時時間（秒）
            
        Returns:
            音頻二進位數據
            
        Raises:
            queue.Empty: 隊列為空且超時
        """
        return self.audio_queue.get(timeout=timeout)
    
    def empty(self) -> bool:
        """檢查隊列是否為空"""
        return self.text_queue.empty() and self.audio_queue.empty()
    
    def clear(self):
        """清空隊列"""
        while not self.text_queue.empty():
            try:
                self.text_queue.get_nowait()
            except queue.Empty:
                break
        
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break


class TTSProcessor:
    """
    TTS 處理器
    參考 base.py 的完整流程，集成：
    - 文本分段
    - TTS 生成
    - 隊列管理
    - 線程安全
    """
    
    def __init__(
        self,
        tts_provider: Optional[EdgeTTSProvider] = None,
        voice: str = "zh-TW-YunJheNeural",
        output_dir: str = "response"
    ):
        """
        初始化 TTS 處理器
        
        Args:
            tts_provider: Edge TTS 提供者（若為 None 則自動建立）
            voice: TTS 音色
            output_dir: 輸出檔案目錄
        """
        self.tts_provider = tts_provider or EdgeTTSProvider(voice, output_dir)
        self.queue = TTSQueue()
        self.stop_event = threading.Event()
        
        logger.info("TTSProcessor 初始化完成")
    
    async def process_text(
        self,
        text: str,
        record_id: str
    ) -> Tuple[str, Optional[str]]:
        """
        處理文本並生成語音檔案
        
        該方法包含完整的 TTS 流程：
        1. 清理 Markdown
        2. 文本分段
        3. 逐段生成 TTS
        4. 檔案管理
        
        Args:
            text: 要轉換的文本
            record_id: 錄音 ID
            
        Returns:
            (status, filename)
            - status: "completed" 或 "error"
            - filename: 生成的檔案名（成功時）或 None（失敗時）
        """
        try:
            # 清理文本
            cleaned_text = MarkdownCleaner.clean_markdown(text)
            
            if not cleaned_text:
                logger.error(f"TTS 文本清理後為空")
                return "error", None
            
            # 對文本進行分段（參考 base.py）
            segments = TTSTextSplitter.split_text(cleaned_text)
            
            if not segments:
                logger.error(f"TTS 文本分段失敗")
                return "error", None
            
            logger.info(f"文本分為 {len(segments)} 段")
            
            # 生成 TTS（取第一段作為主要輸出，實際應用可合併所有段落）
            # 這裡簡化處理：只對完整文本生成 TTS
            status, filename = await self.tts_provider.generate_audio(
                cleaned_text,
                record_id
            )
            
            return status, filename
            
        except Exception as e:
            logger.error(f"TTS 處理異常: {str(e)}", exc_info=True)
            return "error", None
    
    async def process_text_streaming(
        self,
        text: str,
        record_id: str,
        chunk_callback: Optional[Callable[[bytes], None]] = None
    ) -> Tuple[str, Optional[str]]:
        """
        流式處理文本（支持實時推送音頻塊）
        
        參考 base.py 的 tts_text_priority_thread 實現
        支持將音頻分塊推送給客戶端
        
        Args:
            text: 要轉換的文本
            record_id: 錄音 ID
            chunk_callback: 音頻塊回調函數
            
        Returns:
            (status, filename)
        """
        try:
            # 清理和分段
            cleaned_text = MarkdownCleaner.clean_markdown(text)
            segments = TTSTextSplitter.split_text(cleaned_text)
            
            if not segments:
                return "error", None
            
            # 逐段生成 TTS 並通過回調推送
            # 實際應用中可以實現邊生成邊推送
            # 這裡簡化為直接生成單個檔案
            status, filename = await self.tts_provider.generate_audio(
                cleaned_text,
                record_id
            )
            
            return status, filename
            
        except Exception as e:
            logger.error(f"流式 TTS 處理異常: {str(e)}", exc_info=True)
            return "error", None
