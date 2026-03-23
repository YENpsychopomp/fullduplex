import io
import os
import torch
import numpy as np
from pydub import AudioSegment
import transformers
from transformers import WhisperProcessor, WhisperForConditionalGeneration
import librosa
import logging

transformers.logging.set_verbosity_error()
logger = logging.getLogger("uvicorn.error")

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# 1. 載入本地模型 (放在全域，確保伺服器啟動時只載入一次到 VRAM)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
local_model_path = os.path.join(BASE_DIR, "local_breeze_model")
print(f"🚀 準備從本地載入模型，絕對路徑為: {local_model_path}")
try:
    processor = WhisperProcessor.from_pretrained(local_model_path)
    model = WhisperForConditionalGeneration.from_pretrained(local_model_path)
    model.to(device)
    logger.info("Local Breeze-ASR model loaded successfully.")
except Exception as e:
    logger.error(f"Failed to load local ASR model: {e}")

def generate_transcription(audio_bytes: bytes) -> str:
    """將記憶體中的純 PCM 音訊二進位資料轉錄為文字"""
    try:
        logger.info(f"Received audio for transcription: {len(audio_bytes)} bytes (Pure PCM)")
        
        # 1. 直接讀取前端傳來的 24000Hz, 16-bit, 單聲道 PCM
        audio = AudioSegment(
            data=audio_bytes, 
            sample_width=2, 
            frame_rate=24000, 
            channels=1
        )
        
        # 2. 模型通常需要 16000Hz，讓 PyDub 幫忙降頻
        audio = audio.set_frame_rate(16000)
        
        # 3. 轉成模型需要的 float32 numpy array (範圍 -1.0 到 1.0)
        samples = np.array(audio.get_array_of_samples(), dtype=np.float32) / 32768.0
        
        # 4. 特徵提取
        inputs = processor(
            samples, 
            sampling_rate=16000, 
            return_tensors="pt",
            return_attention_mask=True
        )
        input_features = inputs.input_features.to(device)
        attention_mask = inputs.attention_mask.to(device)
        
        # 5. 模型推論
        with torch.no_grad():
            predicted_ids = model.generate(
                input_features,
                attention_mask=attention_mask,
                language="zh",
                task="transcribe"
            )
            
        # 6. 解碼回文字
        transcription = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0]
        return transcription.strip()
        
    except Exception as e:
        logger.error(f"Local ASR transcription failed: {e}", exc_info=True)
        return ""

if __name__ == "__main__":
    test_path = "backend\\streams\\test1.mp3"
    
    # 2. 讀取音檔 (使用 librosa)
    waveform, sample_rate = librosa.load(test_path, sr=16000)
    inputs = processor(
        waveform, 
        sampling_rate=16000, 
        return_tensors="pt",
        return_attention_mask=True
    )
    
    input_features = inputs.input_features.to(device)
    attention_mask = inputs.attention_mask.to(device) # 現在這裡就不會報錯了
    
    with torch.no_grad():
        # 新增參數：明確傳入 attention_mask, language, 和 task
        predicted_ids = model.generate(
            input_features,
            attention_mask=attention_mask,
            language="zh",
            task="transcribe"
        )
        
    # 5. 將 IDs 解碼回人類可讀的文字
    transcription = processor.batch_decode(predicted_ids, skip_special_tokens=True)[0]
    
    print("Result:", transcription)