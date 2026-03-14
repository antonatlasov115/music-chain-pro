import os
import json
import time

BASE_MUSIC_DIR = "./music_library"

def get_user_history_file(user_id):
    user_dir = os.path.join(BASE_MUSIC_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, "history.json")

def load_user_db(user_id):
    file_path = get_user_history_file(user_id)
    if not os.path.exists(file_path):
        return {"play_history": [], "top_tracks": {}}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
            #  САМОЛЕЧЕНИЕ: Если файл старого формата, обновляем его структуру на лету
            if "play_history" not in data:
                data["play_history"] = []
            if "top_tracks" not in data:
                data["top_tracks"] = {}
                
            return data
    except Exception:
        # Если файл вообще сломан, отдаем чистую базу
        return {"play_history": [], "top_tracks": {}}

def save_user_db(user_id, data):
    file_path = get_user_history_file(user_id)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def extract_artist(track_name):
    """Вытаскивает имя артиста из стандартных названий 'Artist - Track'"""
    separators = [" - ", " ~ ", " | ", " // ", " / "]
    for sep in separators:
        if sep in track_name:
            return track_name.split(sep)[0].strip()
    return "Unknown Artist"

def add_user_preference(user_id, query, genre=None):
    """Записывает трек, жанр и артиста в историю"""
    db = load_user_db(user_id)
    artist = extract_artist(query)
    
    db["play_history"].append({
        "track": query,
        "artist": artist,
        "genre": genre or "unknown",
        "timestamp": time.time()
    })
    
    # Храним последние 50 треков
    if len(db["play_history"]) > 50:
        db["play_history"].pop(0)
        
    if query not in db["top_tracks"]:
        db["top_tracks"][query] = 0
    db["top_tracks"][query] += 1
    
    save_user_db(user_id, db)

def get_user_preferences(user_id):
    db = load_user_db(user_id)
    if db["play_history"]: return db["play_history"][-1]["track"]
    return None

def get_recent_history(user_id, limit=3):
    """Достает последние N жанров и артистов для Cooldown-системы"""
    db = load_user_db(user_id)
    history = db["play_history"][-limit:]
    genres = [item.get("genre") for item in history if item.get("genre")]
    artists = [item.get("artist") for item in history if item.get("artist")]
    return genres, artists