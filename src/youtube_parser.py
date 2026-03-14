import os
import yt_dlp
import concurrent.futures

def search_tracks_on_youtube(query: str, limit: int = 5):
    """Ищет треки по тексту ИЛИ извлекает информацию по прямой ссылке"""
    print(f"🔎 yt-dlp анализирует: {query}...")
    ydl_opts = {
        'extract_flat': True, 
        'quiet': True,
        'no_warnings': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 🚀 МАГИЯ: Если это ссылка, парсим её напрямую без поиска!
            if query.startswith("http://") or query.startswith("https://"):
                info = ydl.extract_info(query, download=False)
                # Если это плейлист, берем все треки. Если одно видео — заворачиваем в список.
                entries = info.get('entries', [info]) if info and 'entries' in info else [info]
            else:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
                entries = info.get('entries', [info]) if info else []
            
            valid_entries = []
            for entry in entries:
                if not entry: continue
                
                video_url = entry.get('url') or entry.get('webpage_url')
                if not video_url and entry.get('id'):
                    video_url = f"https://www.youtube.com/watch?v={entry.get('id')}"
                    
                if video_url:
                    entry['url'] = video_url
                    valid_entries.append(entry)
                    
            return valid_entries
            
    except Exception as e:
        print(f"❌ Ошибка поиска: {e}")
    return []

def download_track_by_url(url: str, save_dir: str):
    """Качает аудио в MP3 по прямой ссылке"""
    print(f"⬇️ yt-dlp качает: {url}...")
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': os.path.join(save_dir, '%(title)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            original_filename = ydl.prepare_filename(info)
            base_name, _ = os.path.splitext(original_filename)
            mp3_filename = f"{base_name}.mp3"
            return True, mp3_filename
    except Exception as e:
        return False, f"Ошибка загрузки: {str(e)}"

#  НОВАЯ ФУНКЦИЯ ДЛЯ МНОГОПОТОЧНОСТИ 
def download_multiple_tracks(urls: list, save_dir: str, max_workers: int = 5):
    """Многопоточное скачивание списка ссылок (идеально для плейлистов)"""
    print(f"🚀 Запускаю скачивание {len(urls)} треков в {max_workers} потоков...")
    results = []
    
    # Создаем пул потоков
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Отдаем все ссылки рабочим потокам
        future_to_url = {executor.submit(download_track_by_url, url, save_dir): url for url in urls}
        
        # Собираем результаты по мере их готовности
        for i, future in enumerate(concurrent.futures.as_completed(future_to_url), 1):
            url = future_to_url[future]
            try:
                success, result = future.result()
                if success:
                    print(f"   ✅ [{i}/{len(urls)}] Успех: {os.path.basename(result)}")
                else:
                    print(f"   ❌ [{i}/{len(urls)}] Ошибка: {result}")
                
                results.append({"url": url, "success": success, "result": result})
            except Exception as e:
                print(f"   ❌ [{i}/{len(urls)}] Критическая ошибка потока: {e}")
                results.append({"url": url, "success": False, "result": str(e)})
                
    return results