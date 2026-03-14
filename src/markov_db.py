import json
import os
import random
import copy
import datetime

BASE_MUSIC_DIR = "./music_library"
MAX_WEIGHT = 25  

_CACHE = {}

DEFAULT_MATRIX = {
    "start": {
        "electronic": 2, "rock": 2, "hip-hop": 2, "pop": 2, 
        "metal": 1, "jazz": 1, "classical": 1, "rnb": 1, 
        "latin": 1, "reggae": 1, "country": 1, "folk": 1, "blues": 1,
        "world": 1, "soul": 1, "funk": 1, "disco": 1, "ambient": 1
    },
    
    # === БАЗОВЫЕ МАКРО-ЖАНРЫ ===
    "electronic": {
        "house": 2, "techno": 2, "dubstep": 1, "trance": 2, "psytrance": 1,
        "synthwave": 1, "drum_and_bass": 2, "ambient": 1, "garage": 1, "idm": 1, 
        "pop": 1, "deep_house": 2, "hardstyle": 1, "chillout": 2, "breakbeat": 1
    },
    "rock": {
        "indie_rock": 2, "hard_rock": 2, "alternative": 2, "punk": 2, 
        "post_rock": 1, "grunge": 2, "psychedelic_rock": 1, "metal": 1, "blues": 1,
        "math_rock": 1, "prog_rock": 1, "shoegaze": 1, "emo": 1, "pop_punk": 2, "britpop": 1
    },
    "hip-hop": {
        "trap": 2, "boom_bap": 2, "phonk": 2, "lo-fi": 2, "drill": 2, "uk_drill": 1,
        "grime": 1, "cloud_rap": 1, "rnb": 1, "old_school_hip_hop": 2, "g_funk": 1, "jazz_rap": 2
    },
    "pop": {
        "electropop": 2, "dance_pop": 2, "k_pop": 2, "j_pop": 1, "synthpop": 2, 
        "indie_pop": 2, "hyperpop": 1, "dreampop": 1, "rnb": 1, "electronic": 1,
        "bedroom_pop": 2, "art_pop": 1, "afrobeat": 1, "latin_pop": 1
    },
    "metal": {
        "heavy_metal": 2, "death_metal": 1, "black_metal": 1, "metalcore": 2, 
        "nu_metal": 2, "doom_metal": 1, "rock": 1, "thrash_metal": 2, "symphonic_metal": 1,
        "power_metal": 1, "sludge": 1, "groove_metal": 1, "deathcore": 1
    },
    "jazz": {
        "bebop": 1, "smooth_jazz": 2, "acid_jazz": 2, "jazz_fusion": 1, 
        "swing": 1, "blues": 1, "lo-fi": 1, "bossa_nova": 1, "free_jazz": 1, "dark_jazz": 1
    },
    "classical": {
        "baroque": 1, "romantic": 1, "contemporary_classical": 1, 
        "orchestral": 2, "ambient": 1, "jazz": 1, "neoclassical": 2, "minimalism": 1
    },
    "rnb": {
        "neo_soul": 2, "contemporary_rnb": 2, "funk": 1, "disco": 1, 
        "hip-hop": 1, "pop": 1, "jazz": 1, "soul": 2, "new_jack_swing": 1
    },
    "latin": {
        "reggaeton": 2, "salsa": 1, "bossa_nova": 1, "bachata": 1, "pop": 1, "cumbia": 1, "flamenco": 1
    },
    "reggae": {
        "dancehall": 2, "dub": 2, "ska": 2, "roots_reggae": 2, "hip-hop": 1, "rocksteady": 1
    },
    "country": {
        "bluegrass": 1, "americana": 1, "outlaw_country": 1, "folk": 1, "rock": 1, "country_pop": 2
    },
    "folk": {
        "indie_folk": 2, "traditional_folk": 1, "acoustic": 2, "country": 1, "rock": 1, "celtic": 1
    },
    "blues": {
        "delta_blues": 1, "chicago_blues": 1, "electric_blues": 1, "rock": 2, "jazz": 1, "soul": 1
    },

    # === ЭЛЕКТРОННЫЕ САБЖАНРЫ ===
    "house": {"electronic": 2, "techno": 1, "dance_pop": 1, "deep_house": 2, "tech_house": 1},
    "deep_house": {"house": 2, "chillout": 1, "ambient": 1, "lounge": 1},
    "tech_house": {"house": 1, "techno": 2, "electronic": 1},
    "techno": {"electronic": 2, "tech_house": 1, "acid_techno": 1, "idm": 1},
    "trance": {"electronic": 1, "psytrance": 2, "eurodance": 1, "techno": 1},
    "psytrance": {"trance": 2, "electronic": 1, "hardstyle": 1},
    "dubstep": {"electronic": 1, "drum_and_bass": 1, "trap": 2, "metalcore": 1, "riddim": 1},
    "drum_and_bass": {"electronic": 1, "jungle": 2, "breakbeat": 1, "dubstep": 1},
    "breakbeat": {"electronic": 1, "drum_and_bass": 1, "big_beat": 1, "hip-hop": 1},
    "synthwave": {"electronic": 1, "retrowave": 2, "cyberpunk": 1, "synthpop": 1, "outrun": 1},
    "ambient": {"electronic": 1, "classical": 1, "lo-fi": 2, "post_rock": 1, "drone": 1, "chillout": 2},
    "chillout": {"ambient": 2, "lo-fi": 1, "downtempo": 2, "trip_hop": 1},
    "downtempo": {"chillout": 2, "trip_hop": 2, "ambient": 1, "lo-fi": 1},
    "idm": {"electronic": 2, "techno": 1, "ambient": 1, "glitch": 1},

    # === РОК / ИНДИ САБЖАНРЫ ===
    "indie_rock": {"rock": 2, "indie_pop": 2, "alternative": 2, "indie_folk": 1, "garage_rock": 1},
    "hard_rock": {"rock": 2, "heavy_metal": 1, "blues": 1, "grunge": 1, "classic_rock": 2},
    "alternative": {"rock": 2, "indie_rock": 1, "grunge": 2, "nu_metal": 1, "pop_punk": 1},
    "punk": {"rock": 1, "pop_punk": 2, "post_punk": 1, "ska": 1, "hardcore_punk": 1},
    "pop_punk": {"punk": 2, "alternative": 1, "emo": 2, "rock": 1},
    "emo": {"pop_punk": 2, "post_hardcore": 1, "indie_rock": 1, "alternative": 1},
    "post_rock": {"rock": 1, "ambient": 2, "indie_rock": 1, "shoegaze": 2, "math_rock": 1},
    "shoegaze": {"post_rock": 1, "dreampop": 2, "indie_rock": 1, "noise_rock": 1},
    "grunge": {"rock": 1, "alternative": 2, "punk": 1, "hard_rock": 1},
    "prog_rock": {"rock": 1, "classic_rock": 1, "math_rock": 1, "psychedelic_rock": 1},

    # === ХИП-ХОП САБЖАНРЫ ===
    "trap": {"hip-hop": 2, "drill": 1, "phonk": 1, "cloud_rap": 2, "electropop": 1},
    "boom_bap": {"hip-hop": 2, "jazz_rap": 2, "lo-fi": 1, "old_school_hip_hop": 2},
    "jazz_rap": {"boom_bap": 2, "jazz": 2, "lo-fi": 2, "neo_soul": 1},
    "phonk": {"hip-hop": 2, "trap": 1, "memphis_rap": 2, "synthwave": 1},
    "lo-fi": {"hip-hop": 1, "jazz": 2, "ambient": 2, "chillhop": 2, "downtempo": 1},
    "chillhop": {"lo-fi": 2, "jazz": 1, "instrumental_hip_hop": 2},
    "drill": {"hip-hop": 2, "trap": 2, "uk_drill": 2, "grime": 1},
    "uk_drill": {"drill": 2, "grime": 2, "hip-hop": 1},
    "cloud_rap": {"hip-hop": 1, "trap": 1, "ambient": 1, "dreampop": 1, "emo_rap": 2},
    "old_school_hip_hop": {"hip-hop": 2, "boom_bap": 2, "funk": 1, "g_funk": 1},

    # === ПОП САБЖАНРЫ ===
    "electropop": {"pop": 2, "synthpop": 2, "dance_pop": 1, "electronic": 1},
    "dance_pop": {"pop": 2, "house": 1, "electropop": 1, "reggaeton": 1},
    "k_pop": {"pop": 2, "dance_pop": 2, "hip-hop": 1, "rnb": 1},
    "j_pop": {"pop": 2, "anime": 2, "rock": 1, "electronic": 1},
    "indie_pop": {"pop": 1, "indie_rock": 2, "dreampop": 1, "bedroom_pop": 2},
    "bedroom_pop": {"indie_pop": 2, "lo-fi": 1, "dreampop": 1, "pop": 1},
    "hyperpop": {"pop": 1, "electronic": 2, "trap": 1, "glitchcore": 2},
    "dreampop": {"pop": 1, "indie_pop": 2, "ambient": 1, "shoegaze": 2},
    "afrobeat": {"world": 2, "dancehall": 1, "pop": 1, "rnb": 1},

    # === МЕТАЛ САБЖАНРЫ ===
    "heavy_metal": {"metal": 2, "hard_rock": 2, "thrash_metal": 2, "power_metal": 1},
    "thrash_metal": {"heavy_metal": 2, "death_metal": 1, "groove_metal": 2, "metal": 1},
    "metalcore": {"metal": 2, "post_hardcore": 2, "deathcore": 1, "alternative": 1},
    "nu_metal": {"metal": 1, "alternative": 2, "hip-hop": 1, "hard_rock": 1},
    "black_metal": {"metal": 2, "death_metal": 1, "ambient": 1, "doom_metal": 1},
    "death_metal": {"metal": 1, "thrash_metal": 1, "deathcore": 2, "black_metal": 1},
    "doom_metal": {"metal": 1, "stoner_rock": 2, "post_rock": 1, "sludge": 2},

    # === R&B, ФАНК, СОУЛ ===
    "soul": {"rnb": 2, "neo_soul": 2, "motown": 2, "blues": 1, "jazz": 1},
    "neo_soul": {"rnb": 2, "jazz": 1, "hip-hop": 1, "soul": 2},
    "funk": {"rnb": 1, "disco": 2, "jazz": 1, "rock": 1, "g_funk": 1},
    "disco": {"rnb": 1, "funk": 2, "dance_pop": 1, "house": 2},

    # === АКУСТИКА И ФОЛК ===
    "indie_folk": {"folk": 2, "indie_rock": 2, "acoustic": 1, "americana": 1},
    "acoustic": {"folk": 1, "indie_pop": 1, "pop": 1, "country": 1, "singer_songwriter": 2},
    
    # === ГЛУБОКИЕ ТУПИКИ ===
    "glitchcore": {"hyperpop": 2, "electronic": 1},
    "emo_rap": {"cloud_rap": 2, "trap": 1, "emo": 1},
    "anime": {"j_pop": 2, "electronic": 1},
    "singer_songwriter": {"acoustic": 2, "folk": 1, "indie_pop": 1},
    "stoner_rock": {"doom_metal": 2, "psychedelic_rock": 2, "hard_rock": 1}
}


def get_user_markov_file(user_id: int | str, is_artist=False) -> str:
    user_dir = os.path.join(BASE_MUSIC_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    filename = "artist_markov.json" if is_artist else "markov.json"
    return os.path.join(user_dir, filename)

def load_user_markov(user_id: int | str, is_artist=False):
    """Возвращает пустой граф для новых пользователей"""
    file_path = get_user_markov_file(user_id, is_artist)
    base_dict = {} if is_artist else {"start": {}}
    
    if not os.path.exists(file_path):
        return base_dict
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not is_artist and "start" not in data:
                data["start"] = {}
            return data
    except:
        return base_dict

def save_user_markov(user_id: int | str, db_data: dict, is_artist=False):
    file_path = get_user_markov_file(user_id, is_artist)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(db_data, f, ensure_ascii=False, indent=4)

def record_artist_transition(user_id: int, prev_artist: str, current_artist: str):
    if not prev_artist or not current_artist or prev_artist == "Unknown Artist": return
    db = load_user_markov(user_id, is_artist=True)
    if prev_artist not in db: db[prev_artist] = {}
    if current_artist not in db[prev_artist]: db[prev_artist][current_artist] = 0
    db[prev_artist][current_artist] += 1
    save_user_markov(user_id, db, is_artist=True)

def get_next_artist(user_id: int, current_artist: str):
    db = load_user_markov(user_id, is_artist=True)
    if current_artist in db and db[current_artist]:
        return random.choices(list(db[current_artist].keys()), weights=list(db[current_artist].values()), k=1)[0]
    return None

def record_user_transition(user_id: int, prev_genre: str, current_genre: str, next_genre: str):
    if not current_genre or not next_genre: return
    db = load_user_markov(user_id)
    
    if current_genre not in db: db[current_genre] = {}
    if next_genre not in db[current_genre]: db[current_genre][next_genre] = 0
    db[current_genre][next_genre] += 2
    
    if prev_genre and prev_genre != "start":
        deep_key = f"{prev_genre}|{current_genre}"
        if deep_key not in db: db[deep_key] = {}
        if next_genre not in db[deep_key]: db[deep_key][next_genre] = 0
        db[deep_key][next_genre] += 3 
    
    if db[current_genre][next_genre] > MAX_WEIGHT:
        for key in db[current_genre]: db[current_genre][key] = max(1, int(db[current_genre][key] * 0.6))
        
    save_user_markov(user_id, db)

def get_next_user_genre(user_id: int, prev_genre: str, current_genre: str, recent_genres: list = []):
    """Ищет в личном графе. Если там пусто - подглядывает в Глобальный словарь."""
    db = load_user_markov(user_id)
    deep_key = f"{prev_genre}|{current_genre}" if prev_genre else None
    
    transitions = {}
    
    # Пытаемся найти связи в личном графе пользователя
    if deep_key and deep_key in db and db[deep_key]:
        transitions = db[deep_key]
    elif current_genre in db and db[current_genre]:
        transitions = db[current_genre]
        
    # Если личных связей еще нет, ИИ смотрит в Глобальную матрицу
    if not transitions:
        if current_genre in DEFAULT_MATRIX:
            transitions = DEFAULT_MATRIX[current_genre]
            print(f"🌍 Использован ГЛОБАЛЬНЫЙ словарь для жанра: {current_genre}")
        else:
            return random.choice(list(DEFAULT_MATRIX["start"].keys()))

    possible_genres = list(transitions.keys())
    weights = list(transitions.values())
    
    # Cooldown
    for i, genre in enumerate(possible_genres):
        if genre in recent_genres: weights[i] = max(0.1, weights[i] * 0.1)
        
    # Биоритмы
    hour = datetime.datetime.now().hour
    MORNING_GENRES = ["rock", "pop", "metal", "dance_pop", "drum_and_bass", "house", "techno", "hard_rock", "trap", "synthwave"]
    NIGHT_GENRES = ["ambient", "lo-fi", "chillout", "jazz", "dark_jazz", "doom_metal", "dreampop", "shoegaze", "post_rock", "downtempo", "indie_folk"]

    for i, g in enumerate(possible_genres):
        if 6 <= hour <= 12 and any(mg in g for mg in MORNING_GENRES): weights[i] *= 2.0 
        elif (hour >= 23 or hour <= 5) and any(ng in g for ng in NIGHT_GENRES): weights[i] *= 2.5 
    
    return random.choices(possible_genres, weights=weights, k=1)[0]