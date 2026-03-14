"""
Library Manager Module for Auto-DJ Phase 2 ( PRO MFCC ALGORITHM )

- Multithreaded music library scanning
- Vectorized Cosine Similarity calculation with strict penalty scaling
- Smart Differential Indexing (Fix)
"""

import os
import json
import numpy as np
from typing import List, Dict, Tuple, Optional
from pathlib import Path
import logging
import concurrent.futures
from sklearn.metrics.pairwise import cosine_similarity

from .audio_processor import AudioProcessor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LibraryManager:
    SUPPORTED_EXTENSIONS = {'.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma'}
    
    def __init__(self, 
                 audio_processor: Optional[AudioProcessor] = None,
                 database_path: str = "music_library.db"):
        self.audio_processor = audio_processor or AudioProcessor()
        self.database_path = database_path
        self.library_data = {}  
        
    def scan_directory(self, directory_path: str, recursive: bool = True) -> List[str]:
        try:
            if not os.path.exists(directory_path):
                raise FileNotFoundError(f"Directory not found: {directory_path}")
            
            music_files = []
            path_obj = Path(directory_path)
            
            IGNORED_MIXES = {"markov_auto_mix.mp3", "custom_manual_mix.mp3", "acoustic_mix.mp3", "dj_mix.wav"}
            iterator = path_obj.rglob('*') if recursive else path_obj.iterdir()
            
            for file_path in iterator:
                if file_path.is_file() and file_path.suffix.lower() in self.SUPPORTED_EXTENSIONS:
                    if file_path.name not in IGNORED_MIXES and not file_path.name.startswith("temp_mix") and "mix_" not in file_path.name:
                        music_files.append(str(file_path))
            return music_files
        except Exception as e:
            logger.error(f"Error scanning directory: {str(e)}")
            raise
    
    def process_song(self, file_path: str) -> Dict:
        try:
            audio_info = self.audio_processor.get_audio_info(file_path)
            audio_data, mfccs, fingerprint = self.audio_processor.process_audio_file(file_path)
            
            return {
                'fingerprint': fingerprint,
                'metadata': {
                    'file_path': file_path,
                    'filename': os.path.basename(file_path),
                    'duration_seconds': audio_info['duration_seconds'],
                    'duration_minutes': audio_info['duration_minutes'],
                    'sample_rate': audio_info['sample_rate'],
                    'num_samples': audio_info['num_samples']
                }
            }
        except Exception as e:
            raise Exception(f"Failed to process {os.path.basename(file_path)}: {str(e)}")
    
    def build_library(self, directory_path: str, recursive: bool = True, force_rebuild: bool = False) -> Dict:
        try:
            # 1. Загружаем базу, удаляя старые отпечатки из 13 параметров
            if os.path.exists(self.database_path) and not force_rebuild:
                self.load_library()
            else:
                self.library_data = {}
            
            logger.info("⚡ Сканирую директорию на наличие файлов...")
            all_music_files = self.scan_directory(directory_path, recursive)
            
            if not all_music_files:
                return {'total_songs': 0, 'processed_songs': 0, 'failed_songs': 0}
            
            # 2.  ИЩЕМ РАЗНИЦУ: Отбираем только те треки, которых НЕТ в оперативной памяти бота
            files_to_process = []
            for fp in all_music_files:
                if fp not in self.library_data:
                    files_to_process.append(fp)
                    
            if not files_to_process:
                logger.info("✅ Все 59 треков уже проиндексированы и находятся в базе!")
                return {'total_songs': len(self.library_data), 'processed_songs': 0, 'failed_songs': 0}
                
            logger.info(f"🚀 Найдено {len(files_to_process)} новых/обновленных треков. Запускаю MFCC извлечение в 8 потоков...")
            
            processed_count, failed_count = 0, 0
            max_threads = min(8, (os.cpu_count() or 4) + 2)
            
            # 3. Многопоточно прогоняем ТОЛЬКО недостающие треки
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as executor:
                future_to_file = {executor.submit(self.process_song, fp): fp for fp in files_to_process}
                
                for i, future in enumerate(concurrent.futures.as_completed(future_to_file), 1):
                    file_path = future_to_file[future]
                    try:
                        song_data = future.result()
                        self.library_data[file_path] = song_data
                        processed_count += 1
                        logger.info(f"[{i}/{len(files_to_process)}] ✅ Готово: {os.path.basename(file_path)}")
                    except Exception as e:
                        logger.error(f"[{i}/{len(files_to_process)}] ❌ Ошибка: {str(e)}")
                        failed_count += 1
            
            # 4. Сохраняем обновленную базу
            self.save_library()
            return {'total_songs': len(self.library_data), 'processed_songs': processed_count, 'failed_songs': failed_count}
        except Exception as e:
            logger.error(f"Критическая ошибка: {e}")
            raise
    
    def save_library(self) -> None:
        try:
            db_dir = os.path.dirname(self.database_path)
            if db_dir: os.makedirs(db_dir, exist_ok=True)
            
            serializable_data = {
                file_path: {'fingerprint': song_data['fingerprint'].tolist(), 'metadata': song_data['metadata']}
                for file_path, song_data in self.library_data.items()
            }
            with open(self.database_path, 'w', encoding='utf-8') as f:
                json.dump(serializable_data, f, ensure_ascii=False)
        except Exception as e:
            raise
    
    def load_library(self) -> None:
        try:
            with open(self.database_path, 'r', encoding='utf-8') as f:
                serializable_data = json.load(f)
            
            self.library_data = {}
            for file_path, song_data in serializable_data.items():
                fp = np.array(song_data['fingerprint'])
                
                #  УМНАЯ ОЧИСТКА: Удаляем старые неправильные отпечатки (где 13 значений вместо 24)
                if len(fp) != 24:
                    continue
                    
                self.library_data[file_path] = {
                    'fingerprint': fp,
                    'metadata': song_data['metadata']
                }
        except Exception as e:
            logger.warning(f"База данных пуста или повреждена, создаю новую...")
            self.library_data = {}
    
    def find_similar_songs(self, seed_file_path: str, top_n: int = 10, include_seed: bool = False) -> List[Tuple[str, float, Dict]]:
        try:
            if not self.library_data: self.load_library()
            
            target_path = os.path.abspath(seed_file_path)
            actual_key = next((k for k in self.library_data.keys() if os.path.abspath(k) == target_path), None)
            if not actual_key: raise ValueError(f"Seed song not found in library.")
            
            seed_fingerprint = self.library_data[actual_key]['fingerprint']
            
            keys = list(self.library_data.keys())
            matrix = np.array([self.library_data[k]['fingerprint'] for k in keys])
            seed_fp_2d = seed_fingerprint.reshape(1, -1)
            
            similarities = cosine_similarity(seed_fp_2d, matrix)[0]
            results = []
            
            for idx, similarity in enumerate(similarities):
                file_path = keys[idx]
                if file_path == actual_key and not include_seed: continue
                
                val = max(0.0, float(similarity))
                strict_score = val ** 5  
                
                results.append((file_path, strict_score, self.library_data[file_path]['metadata']))
            
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_n]
        except Exception as e:
            raise
            
    def get_library_stats(self) -> Dict:
        if not self.library_data: return {'total_songs': 0, 'total_duration_minutes': 0}
        total_duration = sum(song_data['metadata']['duration_minutes'] for song_data in self.library_data.values())
        return {'total_songs': len(self.library_data)}
    
    def get_song_list(self) -> List[Tuple[str, Dict]]:
        return [(file_path, song_data['metadata']) for file_path, song_data in self.library_data.items()] if self.library_data else []
    
    def add_song_to_library(self, file_path: str) -> bool:
        try:
            target_path = os.path.abspath(file_path)
            if any(os.path.abspath(k) == target_path for k in self.library_data.keys()): return True
            song_data = self.process_song(file_path)
            self.library_data[file_path] = song_data
            self.save_library()
            return True
        except Exception as e:
            return False