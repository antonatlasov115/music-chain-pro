import pylast
import random
import pylast
import random
import urllib.request
import urllib.parse
import json

# ТВОИ КЛЮЧИ LAST.FM
API_KEY = "23f741bf04a01326297ceb7bf5ba37a9"
API_SECRET = "e6e584f72183e60629508d74602f55a3"

network = pylast.LastFMNetwork(api_key=API_KEY, api_secret=API_SECRET)

# Все жанры, которые понимает наш Мега-Марков
KNOWN_GENRES = {
    # База
    "electronic", "rock", "hip-hop", "pop", "metal", "jazz", "classical", "rnb", "latin", "reggae", "country", "folk", "blues", "world", "soul", "funk", "disco", "ambient",
    # Электроника
    "house", "techno", "dubstep", "trance", "psytrance", "synthwave", "drum_and_bass", "garage", "idm", "deep_house", "hardstyle", "chillout", "breakbeat", "eurodance", "downtempo", "trip_hop", "glitch", "jungle", "big_beat", "outrun",
    # Рок / Инди
    "indie_rock", "hard_rock", "alternative", "punk", "post_rock", "grunge", "psychedelic_rock", "math_rock", "prog_rock", "shoegaze", "emo", "pop_punk", "britpop", "garage_rock", "post_punk", "hardcore_punk", "post_hardcore", "noise_rock", "classic_rock", "stoner_rock",
    # Хип-Хоп
    "trap", "boom_bap", "phonk", "lo-fi", "drill", "uk_drill", "grime", "cloud_rap", "old_school_hip_hop", "g_funk", "jazz_rap", "chillhop", "instrumental_hip_hop", "memphis_rap", "emo_rap",
    # Поп
    "electropop", "dance_pop", "k_pop", "j_pop", "synthpop", "indie_pop", "hyperpop", "dreampop", "bedroom_pop", "art_pop", "afrobeat", "latin_pop", "anime", "glitchcore",
    # Метал
    "heavy_metal", "death_metal", "black_metal", "metalcore", "nu_metal", "doom_metal", "thrash_metal", "symphonic_metal", "power_metal", "sludge", "groove_metal", "deathcore",
    # Джаз / Классика / РнБ
    "bebop", "smooth_jazz", "acid_jazz", "jazz_fusion", "swing", "bossa_nova", "free_jazz", "dark_jazz", "baroque", "romantic", "contemporary_classical", "orchestral", "neoclassical", "minimalism", "neo_soul", "contemporary_rnb", "new_jack_swing", "motown",
    # Латина / Регги / Кантри / Фолк
    "reggaeton", "salsa", "bachata", "cumbia", "flamenco", "dancehall", "dub", "ska", "roots_reggae", "rocksteady", "bluegrass", "americana", "outlaw_country", "country_pop", "indie_folk", "traditional_folk", "acoustic", "celtic", "delta_blues", "chicago_blues", "electric_blues", "singer_songwriter"
}

# Маппинг частых синонимов и странных тегов Last.fm
GENRE_MAPPING = {
    "hip hop": "hip-hop",
    "hip-hop/rap": "hip-hop", # <--- Apple Music тег
    "rap": "hip-hop",
    "r&b": "rnb",
    "r&b/soul": "rnb",        # <--- Apple Music тег
    "dance": "electronic",    # <--- Apple Music тег
    "d&b": "drum_and_bass",
    "edm": "electronic",
    "lofi": "lo-fi",
    "kpop": "k_pop",
    "jpop": "j_pop",
    "synth-pop": "synthpop",
    "lush": "dreampop",
    "indie": "indie_rock",
    "witch house": "synthwave",    
    "darkwave": "synthwave",       
    "post-punk": "post_punk",      
    "ost": "orchestral"
}

# Фильтр мусорных тегов, которые люди часто пишут в Last.fm
TRASH_TAGS = {"seen live", "favorite", "love", "awesome", "tracks", "catchy", "beautiful", "amazing", "good", "best", "masterpiece", "loved", "favorites", "favourite"}

def normalize_genre(tag_name: str) -> str:
    """Приводит тег к внутреннему стандарту и ДИНАМИЧЕСКИ изучает новые жанры"""
    raw_lower = tag_name.lower().strip()
    
    # 0. Отсеиваем мусор
    if raw_lower in TRASH_TAGS:
        return None
        
    # 1. Проверяем словарь синонимов
    if raw_lower in GENRE_MAPPING:
        return GENRE_MAPPING[raw_lower]
        
    # 2. Заменяем пробелы на нижние подчеркивания
    with_underscores = raw_lower.replace(" ", "_").replace("-", "_")
    if with_underscores in KNOWN_GENRES:
        return with_underscores
        
    # 3. Ищем частичные совпадения
    for known in KNOWN_GENRES:
        if known.replace("_", " ") in raw_lower:
            return known
            
    # 4.  ГИБКОСТЬ: ДОБАВЛЕНИЕ НОВЫХ ЖАНРОВ 
    # Если жанра нет в базе, но он адекватной длины и без странных символов - добавляем его!
    if len(with_underscores) <= 25 and not any(c in with_underscores for c in "0123456789()[]{}'\""):
        print(f"🌟 Открыт НОВЫЙ музыкальный жанр: {with_underscores.upper()}!")
        KNOWN_GENRES.add(with_underscores) # Записываем в оперативную память бота
        return with_underscores
        
    return None

def get_track_by_genre(genre_name: str) -> str:
    # Для Last.fm превращаем наши "heavy_metal" обратно в "heavy metal"
    search_genre = genre_name.replace("_", " ")
    
    print(f"🎵 Last.fm: Ищу треки для жанра '{search_genre}' (внутренний: {genre_name})...")
    try:
        tag = network.get_tag(search_genre)
        top_tracks = tag.get_top_tracks(limit=50)
        
        if top_tracks:
            random_item = random.choice(top_tracks)
            track = random_item.item
            result = f"{track.artist.name} - {track.title}"
            print(f"✅ Last.fm нашел: {result}")
            return result
        else:
            print(f"⚠️ Last.fm вернул пустой список для жанра '{search_genre}'")
            
    except Exception as e:
        print(f"❌ Ошибка Last.fm (get_track_by_genre): {e}")
        
    return "Rick Astley - Never Gonna Give You Up"

def get_itunes_genre(query: str) -> str:
    """Резервный способ: ищет жанр через бесплатный API Apple Music (Без лимитов)"""
    try:
        safe_query = urllib.parse.quote(query)
        url = f"https://itunes.apple.com/search?term={safe_query}&entity=song&limit=1"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            if data['resultCount'] > 0:
                genre = data['results'][0].get('primaryGenreName', '')
                normalized = normalize_genre(genre)
                return normalized
    except Exception as e:
        print(f"⚠️ Ошибка Apple Music API: {e}")
    return None

def get_track_genre(query: str) -> str:
    print(f"🎵 Определение жанра для '{query}'...")
    
    artist = ""
    track_name = query
    
    separators = [" - ", " ~ ", " | ", " // ", " / "]
    for sep in separators:
        if sep in query:
            parts = query.split(sep, 1)
            artist = parts[0].strip()
            track_name = parts[1].strip()
            break
            
    try:
        # 1. ПЫТАЕМСЯ ЧЕРЕЗ LAST.FM
        if artist:
            search_results = network.search_for_track(artist, track_name)
        else:
            search_results = network.search_for_track("", query)
            
        tracks = search_results.get_next_page()
        
        if tracks:
            best_match = tracks[0]
            top_tags = best_match.get_top_tags(limit=10)
            for tag in top_tags:
                tag_name = tag.item.get_name()
                normalized = normalize_genre(tag_name)
                if normalized:
                    print(f"✅ Last.fm нашел трек: {normalized}")
                    return normalized
                    
        search_artist = artist if artist else query
        artist_results = network.search_for_artist(search_artist)
        artists = artist_results.get_next_page()
        
        if artists:
            best_artist = artists[0]
            top_tags = best_artist.get_top_tags(limit=10)
            for tag in top_tags:
                tag_name = tag.item.get_name()
                normalized = normalize_genre(tag_name)
                if normalized:
                    print(f"✅ Last.fm нашел артиста: {normalized}")
                    return normalized

    except Exception as e:
        print(f"❌ Last.fm недоступен или выдал ошибку: {e}")
        
    # 2. ЕСЛИ LAST.FM УПАЛ ИЛИ НИЧЕГО НЕ НАШЕЛ -> ВКЛЮЧАЕМ РЕЗЕРВ (APPLE MUSIC)
    print("🔄 Включаю резервный радар (Apple Music)...")
    fallback_genre = get_itunes_genre(query)
    
    if fallback_genre:
        print(f"✅ Apple Music спас ситуацию: {fallback_genre}")
        return fallback_genre

    print(f"⚠️ Знакомый жанр вообще не найден. Игнорирую мусор.")
    return None