import librosa
import numpy as np
import soundfile as sf
from typing import Tuple, Optional, List
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class AudioProcessor:
   
    def __init__(self, 
                 sample_rate: int = 22050,
                 n_mfcc: int = 13,
                 hop_length: int = 512,
                 n_fft: int = 2048):
        self.sample_rate = sample_rate
        self.n_mfcc = n_mfcc
        self.hop_length = hop_length
        self.n_fft = n_fft
        
    def _get_duration_safe(self, file_path: str) -> float:
        """Безопасное получение длительности файла для любой версии librosa"""
        try:
            return librosa.get_duration(path=file_path)
        except TypeError:
            return librosa.get_duration(filename=file_path)

    def load_audio(self, file_path: str) -> Tuple[np.ndarray, int]:
        try:
            logger.info(f"Loading audio file snippet: {file_path}")
            
            total_duration = self._get_duration_safe(file_path)
            
            if total_duration <= 30.0:
                offset = 0.0
                duration = None
            else:
                offset = min(30.0, total_duration / 3)
                duration = 30.0
                
            audio_data, sr = librosa.load(file_path, sr=self.sample_rate, offset=offset, duration=duration)
            logger.info(f"Successfully loaded snippet: {len(audio_data)} samples at {sr} Hz")
            return audio_data, sr
            
        except FileNotFoundError:
            logger.error(f"Audio file not found: {file_path}")
            raise
        except Exception as e:
            logger.error(f"Error loading audio file {file_path}: {str(e)}")
            raise
    
    def extract_mfcc_features(self, audio_data: np.ndarray) -> np.ndarray:
        try:
            logger.info("Extracting MFCC features...")
            mfccs = librosa.feature.mfcc(
                y=audio_data,
                sr=self.sample_rate,
                n_mfcc=self.n_mfcc,
                hop_length=self.hop_length,
                n_fft=self.n_fft
            )
            logger.info(f"Extracted MFCC features: {mfccs.shape}")
            return mfccs
        except Exception as e:
            logger.error(f"Error extracting MFCC features: {str(e)}")
            raise
    
    def generate_acoustic_fingerprint(self, mfccs: np.ndarray) -> np.ndarray:
        try:
            logger.info("Generating acoustic fingerprint...")
            
            #  ИСПРАВЛЕНИЕ: Отбрасываем MFCC[0] (общую громкость трека)
            # Из-за него все треки казались боту похожими на 90%
            mfccs_timbre = mfccs[1:] 
            
            # Берем среднее значение (базовый тембр) и отклонение (ритм/динамика)
            mfcc_mean = np.mean(mfccs_timbre, axis=1)
            mfcc_std = np.std(mfccs_timbre, axis=1)
            
            # Склеиваем. Теперь наш отпечаток состоит из 24 уникальных параметров
            fingerprint = np.concatenate((mfcc_mean, mfcc_std))
            
            logger.info(f"Generated fingerprint with {len(fingerprint)} coefficients")
            return fingerprint
        except Exception as e:
            logger.error(f"Error generating acoustic fingerprint: {str(e)}")
            raise
    
    def process_audio_file(self, file_path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        try:
            logger.info(f"Processing audio file: {file_path}")
            audio_data, sample_rate = self.load_audio(file_path)
            mfccs = self.extract_mfcc_features(audio_data)
            fingerprint = self.generate_acoustic_fingerprint(mfccs)
            logger.info("Audio processing completed successfully")
            return audio_data, mfccs, fingerprint
        except Exception as e:
            logger.error(f"Error processing audio file {file_path}: {str(e)}")
            raise
    
    def get_audio_info(self, file_path: str) -> dict:
        try:
            duration = self._get_duration_safe(file_path)
            
            info = {
                'file_path': file_path,
                'sample_rate': self.sample_rate,
                'duration_seconds': duration,
                'duration_minutes': duration / 60,
                'num_samples': int(duration * self.sample_rate),
                'channels': 1
            }
            return info
        except Exception as e:
            logger.error(f"Error getting audio info for {file_path}: {str(e)}")
            raise