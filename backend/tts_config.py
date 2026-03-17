"""
TTS 配置文件
集中管理所有 TTS 相關的配置參數
參考 xiaozhi-server 的 config 結構
"""

import os
from typing import Dict, List, Optional

# ==================== TTS 基礎配置 ====================

# Edge TTS 服務配置
EDGE_TTS_CONFIG = {
    # TTS 音色選項（支持多種語言和性別）
    "voice": os.getenv("EDGE_TTS_VOICE", "zh-TW-YunJheNeural"), 
    
    # 備用音色列表（若主音色失敗）
    "voice_fallback": [
        "zh-TW-HsiaoChenNeural",
    ],
    
    # 輸出格式優先級（按優先級排序，優先使用 WebM/Opus）
    "output_formats": [
        {
            "name": "webm_opus_24khz_16kb",
            "edge_format": "webm-24khz-16kbitrate-mono-opus",
            "file_extension": "webm",
            "codec": "opus",
            "sample_rate": 24000,
            "channels": 1,
            "bitrate": "16k"
        },
        {
            "name": "webm_opus_24khz_24kb",
            "edge_format": "webm-24khz-24kbitrate-mono-opus",
            "file_extension": "webm",
            "codec": "opus",
            "sample_rate": 24000,
            "channels": 1,
            "bitrate": "24k"
        },
        {
            "name": "mp3_24khz_48kb",
            "edge_format": "audio-24khz-48kbitrate-mono-mp3",
            "file_extension": "mp3",
            "codec": "mp3",
            "sample_rate": 24000,
            "channels": 1,
            "bitrate": "48k"
        },
        {
            "name": "mp3_16khz_32kb",
            "edge_format": "audio-16khz-32kbitrate-mono-mp3",
            "file_extension": "mp3",
            "codec": "mp3",
            "sample_rate": 16000,
            "channels": 1,
            "bitrate": "32k"
        }
    ]
}

# ==================== 文本處理配置 ====================

TEXT_PROCESSING_CONFIG = {
    # 文本分段配置（參考 base.py）
    "max_segment_length": 500,  # 單段最大字符數
    
    # 標點符號分段規則
    "punctuations": {
        "all": ("。", "？", "?", "！", "!", "；", ";", "：", ":", "\n", "）", ")", "，", ","),
        "first_sentence": ("，", "~", "、", ",", "。", "？", "?", "！", "!", "；", ";", "：", ":", "）", ")"),
    },
    
    # Markdown 清理規則
    "markdown_cleanup": {
        "remove_code_blocks": True,      # 移除代碼塊
        "remove_inline_code": True,      # 移除行內代碼
        "remove_html_tags": True,        # 移除 HTML 標籤
        "remove_bold_markers": True,     # 移除粗體標記
        "remove_italic_markers": True,   # 移除斜體標記
        "remove_links": True,            # 移除連結
        "remove_headings": True,         # 移除標題標記
        "remove_list_markers": True,     # 移除列表標記
        "clean_whitespace": True,        # 清理多餘空白
    }
}

# ==================== 音頻配置 ====================

AUDIO_CONFIG = {
    # 輸出目錄配置
    "output_dir": os.getenv("TTS_OUTPUT_DIR", "response"),
    "temp_dir": os.getenv("TTS_TEMP_DIR", "temp_audio"),
    
    # 音頻品質檢查
    "quality_check": {
        "enabled": True,                 # 是否啟用品質檢查
        "min_file_size": 5000,          # 最小檔案大小（字節）
        "min_duration": 0.5,            # 最小音頻時長（秒）
        "min_sample_rate": 16000,       # 最小樣本率（Hz）
    },
    
    # 音頻轉換配置
    "conversion": {
        "auto_convert_to_pcm": False,    # 自動轉換為 PCM
        "auto_convert_to_wav": False,    # 自動轉換為 WAV
        "ffmpeg_timeout": 30,            # FFmpeg 超時時間（秒）
    }
}

# ==================== 隊列配置 ====================

QUEUE_CONFIG = {
    # 文本隊列
    "text_queue": {
        "maxsize": 100,                  # 最大隊列容量
        "timeout": 30,                   # 取數據超時時間（秒）
    },
    
    # 音頻隊列
    "audio_queue": {
        "maxsize": 50,
        "timeout": 60,
    },
    
    # 線程池配置
    "thread_pool": {
        "max_workers": 3,               # 最大工作線程數
        "timeout": 120,                 # 線程超時時間（秒）
    }
}

# ==================== 日誌配置 ====================

LOGGING_CONFIG = {
    "level": os.getenv("LOG_LEVEL", "INFO"),
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "log_file": os.getenv("LOG_FILE", "tts.log"),
    "max_bytes": 10485760,              # 10 MB
    "backup_count": 5,
}

# ==================== 性能優化配置 ====================

PERFORMANCE_CONFIG = {
    # 緩存配置
    "cache": {
        "enabled": True,                 # 是否啟用緩存
        "ttl": 3600,                     # 緩存有效期（秒）
        "max_size": 100,                 # 最大緩存條數
    },
    
    # 重試配置
    "retry": {
        "max_attempts": 3,               # 最大重試次數
        "retry_delay": 2,                # 重試延遲（秒）
        "exponential_backoff": True,     # 指數退避
    },
    
    # 超時配置
    "timeout": {
        "tts_generation": 60,            # TTS 生成超時（秒）
        "http_request": 30,              # HTTP 請求超時（秒）
    }
}

# ==================== 功能開關 ====================

FEATURE_FLAGS = {
    "enable_streaming": True,           # 啟用流式輸出
    "enable_chunking": True,            # 啟用音頻分塊
    "enable_quality_check": True,       # 啟用品質檢查
    "enable_caching": False,            # 啟用結果緩存
    "enable_logging": True,             # 啟用日誌記錄
    "enable_metrics": True,             # 啟用性能指標
}

# ==================== 預設值 ====================

DEFAULTS = {
    "voice": EDGE_TTS_CONFIG["voice"],
    "output_dir": AUDIO_CONFIG["output_dir"],
    "max_segment_length": TEXT_PROCESSING_CONFIG["max_segment_length"],
    "temp_dir": AUDIO_CONFIG["temp_dir"],
}


# ==================== 配置驗證和初始化 ====================

def validate_config() -> bool:
    """
    驗證所有配置是否有效
    
    Returns:
        配置是否有效
    """
    errors = []
    
    # 驗證輸出目錄
    output_dir = AUDIO_CONFIG.get("output_dir")
    if not output_dir:
        errors.append("output_dir 未配置")
    else:
        try:
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            errors.append(f"無法建立 output_dir: {str(e)}")
    
    # 驗證臨時目錄
    temp_dir = AUDIO_CONFIG.get("temp_dir")
    if not temp_dir:
        errors.append("temp_dir 未配置")
    else:
        try:
            os.makedirs(temp_dir, exist_ok=True)
        except Exception as e:
            errors.append(f"無法建立 temp_dir: {str(e)}")
    
    # 驗證 TTS 音色
    voice = EDGE_TTS_CONFIG.get("voice")
    if not voice:
        errors.append("TTS voice 未配置")
    
    if errors:
        for error in errors:
            print(f"[ERROR] {error}")
        return False
    
    return True


def get_config(key: str, default=None):
    """
    獲取配置值（支持點符號路徑）
    
    Args:
        key: 配置鍵（例如 "EDGE_TTS_CONFIG.voice"）
        default: 默認值
        
    Returns:
        配置值
    """
    if "." in key:
        parts = key.split(".")
        obj = globals().get(parts[0])
        if obj is None:
            return default
        
        for part in parts[1:]:
            if isinstance(obj, dict):
                obj = obj.get(part)
                if obj is None:
                    return default
            else:
                return default
        
        return obj
    else:
        return globals().get(key, default)


# ==================== 初始化 ====================

if __name__ == "__main__":
    # 驗證配置
    if validate_config():
        print("[SUCCESS] 所有配置驗證通過")
    else:
        print("[FAILED] 配置驗證失敗")
