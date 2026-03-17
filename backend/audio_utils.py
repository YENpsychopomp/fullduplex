"""
音頻處理工具集
參考 xiaozhi-server 的 util.py 和 base.py 中的音頻轉換邏輯
提供音頻格式轉換、編碼、解碼等工具函數
"""

import os
import re
import wave
import struct
import logging
from typing import Optional, Callable, List
import subprocess
import shutil

# 日誌配置
logger = logging.getLogger("audio_utils")


def _get_ffprobe_cmd() -> Optional[str]:
    """回傳可用的 ffprobe 命令路徑，不存在則回傳 None。"""
    custom_path = os.getenv("FFPROBE_PATH")
    if custom_path and os.path.exists(custom_path):
        return custom_path

    detected = shutil.which("ffprobe")
    return detected


class AudioValidator:
    """
    音頻檔案驗證器
    檢查檔案格式、樣本率、聲道數等參數
    """
    
    # 支持的音頻格式
    SUPPORTED_FORMATS = {
        "webm": "audio/webm;codecs=opus",
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "opus": "audio/opus",
    }
    
    @staticmethod
    def get_audio_duration(filepath: str) -> Optional[float]:
        """
        獲取音頻檔案時長（秒）
        使用 ffprobe 工具
        
        Args:
            filepath: 音頻檔案路徑
            
        Returns:
            時長（秒），若失敗則返回 None
        """
        ffprobe_cmd = _get_ffprobe_cmd()
        if not ffprobe_cmd:
            logger.warning("ffprobe 不存在，略過音頻時長檢查（可安裝 ffmpeg 取得 ffprobe）")
            return None

        try:
            result = subprocess.run(
                [ffprobe_cmd, "-v", "error", "-show_entries", 
                 "format=duration", "-of", 
                 "default=noprint_wrappers=1:nokey=1:noprint_wrappers=1", 
                 filepath],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                duration = float(result.stdout.strip())
                return duration
            else:
                logger.warning(f"無法獲取音頻時長: {filepath}")
                return None
                
        except Exception as e:
            logger.error(f"獲取音頻時長出錯: {str(e)}")
            return None
    
    @staticmethod
    def get_audio_info(filepath: str) -> Optional[dict]:
        """
        獲取音頻檔案詳細信息
        包括: 樣本率、聲道數、位深、編碼格式等
        
        參考 xiaozhi-server 的音頻檢測邏輯
        
        Args:
            filepath: 音頻檔案路徑
            
        Returns:
            包含音頻信息的字典，格式如下：
            {
                "format": "mp3",
                "duration": 10.5,
                "sample_rate": 16000,
                "channels": 1,
                "codec": "libmp3lame",
                "bitrate": "128k"
            }
        """
        ffprobe_cmd = _get_ffprobe_cmd()
        if not ffprobe_cmd:
            logger.warning("ffprobe 不存在，略過音頻詳細信息檢查（可安裝 ffmpeg 取得 ffprobe）")
            return None

        try:
            result = subprocess.run(
                [ffprobe_cmd, "-v", "error", "-show_format", 
                 "-show_streams", "-of", "json", filepath],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                
                if data.get("streams"):
                    stream = data["streams"][0]
                    fmt = data.get("format", {})
                    
                    return {
                        "format": os.path.splitext(filepath)[1][1:],
                        "duration": float(fmt.get("duration", 0)),
                        "sample_rate": stream.get("sample_rate"),
                        "channels": stream.get("channels"),
                        "codec": stream.get("codec_name"),
                        "bitrate": fmt.get("bit_rate")
                    }
            
            return None
            
        except Exception as e:
            logger.error(f"獲取音頻信息出錯: {str(e)}")
            return None
    
    @staticmethod
    def validate_webm_opus(filepath: str) -> bool:
        """
        驗證 WebM/Opus 音頻檔案
        檢查檔案是否為有效的 WebM 容器且編碼為 Opus
        
        Args:
            filepath: 檔案路徑
            
        Returns:
            驗證結果 (True/False)
        """
        try:
            with open(filepath, "rb") as f:
                # WebM 文件簽名：EBML 元素 ID (0x1A 0x45 0xDF 0xA3)
                header = f.read(4)
                return header == b'\x1a\x45\xdf\xa3'
                
        except Exception as e:
            logger.error(f"WebM 驗證失敗: {str(e)}")
            return False


class AudioConverter:
    """
    音頻格式轉換器
    參考 base.py 的音頻轉換邏輯
    支持各種音頻格式之間的轉換
    """
    
    @staticmethod
    def convert_to_pcm(
        input_file: str,
        output_file: str,
        sample_rate: int = 16000,
        channels: int = 1
    ) -> bool:
        """
        將音頻轉換為 PCM 格式
        
        參考 base.py 的 audio_to_pcm_data_stream()
        
        Args:
            input_file: 輸入音頻檔案
            output_file: 輸出 PCM 檔案
            sample_rate: 目標樣本率（Hz）
            channels: 目標聲道數
            
        Returns:
            轉換成功標誌
        """
        try:
            cmd = [
                "ffmpeg", "-i", input_file,
                "-f", "s16le",
                "-acodec", "pcm_s16le",
                "-ar", str(sample_rate),
                "-ac", str(channels),
                "-y", output_file
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30
            )
            
            if result.returncode == 0:
                logger.info(f"PCM 轉換成功: {input_file} -> {output_file}")
                return True
            else:
                logger.error(f"PCM 轉換失敗: {result.stderr.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"PCM 轉換異常: {str(e)}")
            return False
    
    @staticmethod
    def convert_to_opus(
        input_file: str,
        output_file: str,
        bitrate: str = "128k"
    ) -> bool:
        """
        將音頻轉換為 Opus 格式
        
        Opus 編碼相比 MP3 具有更好的語音品質和更低的延遲
        
        Args:
            input_file: 輸入音頻檔案
            output_file: 輸出 Opus 檔案
            bitrate: 目標位元速率
            
        Returns:
            轉換成功標誌
        """
        try:
            cmd = [
                "ffmpeg", "-i", input_file,
                "-c:a", "libopus",
                "-b:a", bitrate,
                "-y", output_file
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30
            )
            
            if result.returncode == 0:
                logger.info(f"Opus 轉換成功: {input_file} -> {output_file}")
                return True
            else:
                logger.error(f"Opus 轉換失敗: {result.stderr.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"Opus 轉換異常: {str(e)}")
            return False
    
    @staticmethod
    def convert_to_wav(
        input_file: str,
        output_file: str,
        sample_rate: int = 16000
    ) -> bool:
        """
        將音頻轉換為 WAV 格式
        
        Args:
            input_file: 輸入音頻檔案
            output_file: 輸出 WAV 檔案
            sample_rate: 目標樣本率
            
        Returns:
            轉換成功標誌
        """
        try:
            cmd = [
                "ffmpeg", "-i", input_file,
                "-acodec", "pcm_s16le",
                "-ar", str(sample_rate),
                "-y", output_file
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=30
            )
            
            if result.returncode == 0:
                logger.info(f"WAV 轉換成功: {input_file} -> {output_file}")
                return True
            else:
                logger.error(f"WAV 轉換失敗: {result.stderr.decode()}")
                return False
                
        except Exception as e:
            logger.error(f"WAV 轉換異常: {str(e)}")
            return False


class AudioProcessor:
    """
    音頻處理器
    集成驗證、轉換、分析等功能
    """
    
    def __init__(self, temp_dir: str = "temp_audio"):
        """
        初始化音頻處理器
        
        Args:
            temp_dir: 暫存目錄
        """
        self.temp_dir = temp_dir
        os.makedirs(temp_dir, exist_ok=True)
        self.validator = AudioValidator()
        self.converter = AudioConverter()
    
    def process_audio_file(
        self,
        input_file: str,
        target_format: str = "wav",
        **kwargs
    ) -> Optional[str]:
        """
        處理音頻檔案
        包括驗證、轉換、優化等步驟
        
        Args:
            input_file: 輸入檔案路徑
            target_format: 目標格式 (wav, opus, pcm)
            **kwargs: 傳遞給轉換函數的其他參數
            
        Returns:
            處理後檔案路徑，或 None（失敗時）
        """
        try:
            if not os.path.exists(input_file):
                logger.error(f"輸入檔案不存在: {input_file}")
                return None
            
            # 驗證輸入檔案
            info = self.validator.get_audio_info(input_file)
            if not info:
                logger.warning(f"無法識別音頻檔案: {input_file}")
                return None
            
            logger.info(f"音頻信息: {info}")
            
            # 生成輸出檔案路徑
            output_file = os.path.join(
                self.temp_dir,
                f"{os.path.splitext(os.path.basename(input_file))[0]}.{target_format}"
            )
            
            # 根據目標格式進行轉換
            if target_format == "wav":
                success = self.converter.convert_to_wav(input_file, output_file, **kwargs)
            elif target_format == "opus":
                success = self.converter.convert_to_opus(input_file, output_file, **kwargs)
            elif target_format == "pcm":
                success = self.converter.convert_to_pcm(input_file, output_file, **kwargs)
            else:
                logger.error(f"不支持的目標格式: {target_format}")
                return None
            
            if success and os.path.exists(output_file):
                return output_file
            else:
                return None
                
        except Exception as e:
            logger.error(f"音頻處理異常: {str(e)}", exc_info=True)
            return None
    
    def cleanup_temp_files(self):
        """清理暫存檔案"""
        try:
            import shutil
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
                os.makedirs(self.temp_dir, exist_ok=True)
                logger.info("暫存檔案清理完成")
        except Exception as e:
            logger.error(f"清理暫存檔案失敗: {str(e)}")


class AudioQualityChecker:
    """
    音頻品質檢查器
    驗證音頻是否符合要求（避免雜音）
    """
    
    # 最小可接受檔案大小（字節）
    MIN_FILE_SIZE = 5000
    
    # 最小可接受持續時間（秒）
    MIN_DURATION = 0.5
    
    @staticmethod
    def check_audio_quality(filepath: str) -> dict:
        """
        檢查音頻品質
        返回品質檢查結果
        
        Args:
            filepath: 音頻檔案路徑
            
        Returns:
            品質檢查結果字典：
            {
                "is_valid": bool,
                "issues": [list of issues],
                "file_size": int,
                "duration": float,
                "recommendations": [list of recommendations]
            }
        """
        issues = []
        recommendations = []
        
        try:
            # 檢查檔案是否存在
            if not os.path.exists(filepath):
                issues.append("檔案不存在")
                return {
                    "is_valid": False,
                    "issues": issues,
                    "recommendations": recommendations
                }
            
            # 檢查檔案大小
            file_size = os.path.getsize(filepath)
            if file_size < AudioQualityChecker.MIN_FILE_SIZE:
                issues.append(f"檔案過小 ({file_size} bytes)")
                recommendations.append("檢查 TTS 是否正常生成音頻")
            
            ffprobe_available = _get_ffprobe_cmd() is not None

            # 獲取音頻持續時間
            duration = AudioValidator.get_audio_duration(filepath)
            if duration is None and ffprobe_available:
                issues.append("無法獲取音頻時長")
            elif duration < AudioQualityChecker.MIN_DURATION:
                issues.append(f"音頻過短 ({duration:.2f}s)")
                recommendations.append("增加 TTS 文本長度")
            
            # 獲取音頻信息
            info = AudioValidator.get_audio_info(filepath)
            if info:
                # 檢查樣本率 (確保轉為整數)
                sample_rate = info.get("sample_rate")
                if sample_rate:
                    try:
                        sample_rate_int = int(sample_rate) if isinstance(sample_rate, str) else sample_rate
                        if sample_rate_int < 16000:
                            recommendations.append(f"樣本率過低 ({sample_rate_int}Hz，建議 ≥16000Hz)")
                    except (ValueError, TypeError):
                        logger.warning(f"無法解析樣本率: {sample_rate}")
                
                # 檢查聲道數 (確保轉為整數)
                channels = info.get("channels")
                if channels:
                    try:
                        channels_int = int(channels) if isinstance(channels, str) else channels
                        if channels_int > 2:
                            recommendations.append(f"聲道過多 ({channels_int}，建議使用單聲道或立體聲)")
                    except (ValueError, TypeError):
                        logger.warning(f"無法解析聲道數: {channels}")
            
            is_valid = len(issues) == 0
            
            return {
                "is_valid": is_valid,
                "issues": issues,
                "file_size": file_size,
                "duration": duration,
                "recommendations": recommendations
            }
            
        except Exception as e:
            logger.error(f"音頻品質檢查異常: {str(e)}")
            return {
                "is_valid": False,
                "issues": [f"檢查異常: {str(e)}"],
                "recommendations": ["請檢查音頻檔案是否損壞"]
            }
