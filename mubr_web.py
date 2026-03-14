import os
import sys
import time
import json
import shutil
import threading
import subprocess
import random
import copy
import datetime

# ==========================================
# АВТО-УСТАНОВЩИК ЗАВИСИМОСТЕЙ В ПАПКУ
# ==========================================
LIBS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "_libs"))

if LIBS_DIR not in sys.path:
    sys.path.insert(0, LIBS_DIR)
    import site
    site.addsitedir(LIBS_DIR)

def check_and_install_deps():
    try:
        import flask
        import librosa
        import pydub
        import demucs
        import sklearn
        import webview
    except ImportError as e:
        print(f"\n Не хватает библиотеки: {e.name}")
        print(f" Создаю портативную среду... Скачиваю зависимости в папку '_libs'...")
        print(" Это займет пару минут. Не закрывай окно!\n")
        
        os.makedirs(LIBS_DIR, exist_ok=True)
        packages = [
            "Flask==3.0.2", "pywebview==4.4.1", "pydub==0.25.1", 
            "librosa>=0.10.1", "soundfile==0.12.1", "numpy>=1.24.0", 
            "scipy>=1.11.0", "scikit-learn==1.3.2", "demucs==4.0.1", 
            "pylast==5.2.0", "yt-dlp==2023.12.30"
        ]
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--target", LIBS_DIR] + packages)
        print("\n✅ Зависимости установлены!")
        os.execv(sys.executable, [sys.executable] + sys.argv)

check_and_install_deps()

# ==========================================
# ИМПОРТЫ ИИ-МОДУЛЕЙ
# ==========================================
from flask import Flask, request, jsonify, render_template_string, send_from_directory
import src.youtube_parser
import src.lastfm_api
import src.markov_db
import src.user_db
import src.playlist_generator
import src.audio_processor
import src.library_manager

from src.youtube_parser import search_tracks_on_youtube, download_track_by_url, download_multiple_tracks
from src.lastfm_api import get_track_genre, get_track_by_genre, normalize_genre, KNOWN_GENRES, GENRE_MAPPING
from src.markov_db import DEFAULT_MATRIX, MAX_WEIGHT
from src.user_db import add_user_preference, get_user_preferences, get_recent_history, extract_artist, get_user_history_file
from dj_mixer import create_continuous_mix

try: from src.stem_separator import extract_minus
except ImportError: pass

try: from MixingBear.smart_mixer import create_smart_transition, create_mashup, create_vocal_battle
except ImportError: pass

app = Flask(__name__)

BASE_MUSIC_DIR = "music_library"
os.makedirs(BASE_MUSIC_DIR, exist_ok=True)
src.markov_db.BASE_MUSIC_DIR = BASE_MUSIC_DIR
src.user_db.BASE_MUSIC_DIR = BASE_MUSIC_DIR
CURRENT_USER_ID = None
discovery_sessions = {}

# ==========================================
#  ЛОГИКА МАРКОВА (С ROOT-УЗЛАМИ)
# ==========================================
def get_user_markov_file(user_id: int | str, is_artist=False) -> str:
    user_dir = os.path.join(BASE_MUSIC_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    filename = "artist_markov.json" if is_artist else "markov.json"
    return os.path.join(user_dir, filename)

def load_user_markov(user_id: int | str, is_artist=False):
    file_path = get_user_markov_file(user_id, is_artist)
    base_dict = {"ROOT": {}} if is_artist else {"start": {}}
    if not os.path.exists(file_path): return base_dict
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not is_artist and "start" not in data: data["start"] = {}
            if is_artist and "ROOT" not in data: data["ROOT"] = {}
            return data
    except: return base_dict

def save_user_markov(user_id: int | str, db_data: dict, is_artist=False):
    file_path = get_user_markov_file(user_id, is_artist)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(db_data, f, ensure_ascii=False, indent=4)

def record_artist_transition(user_id: int, prev_artist: str, current_artist: str):
    if not current_artist or current_artist == "Unknown Artist": return
    if not prev_artist: prev_artist = "ROOT" # Исправление: первый артист идет от корня
    
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
    db = load_user_markov(user_id)
    deep_key = f"{prev_genre}|{current_genre}" if prev_genre else None
    transitions = {}
    if deep_key and deep_key in db and db[deep_key]: transitions = db[deep_key]
    elif current_genre in db and db[current_genre]: transitions = db[current_genre]
    if not transitions:
        if current_genre in DEFAULT_MATRIX: transitions = DEFAULT_MATRIX[current_genre]
        else: return random.choice(list(DEFAULT_MATRIX["start"].keys()))

    possible_genres = list(transitions.keys())
    weights = list(transitions.values())
    for i, genre in enumerate(possible_genres):
        if genre in recent_genres: weights[i] = max(0.1, weights[i] * 0.1)
        
    hour = datetime.datetime.now().hour
    MORNING_GENRES = ["rock", "pop", "metal", "dance_pop", "drum_and_bass", "house", "techno", "hard_rock", "trap", "synthwave"]
    NIGHT_GENRES = ["ambient", "lo-fi", "chillout", "jazz", "dark_jazz", "doom_metal", "dreampop", "shoegaze", "post_rock", "downtempo", "indie_folk"]
    for i, g in enumerate(possible_genres):
        if 6 <= hour <= 12 and any(mg in g for mg in MORNING_GENRES): weights[i] *= 2.0 
        elif (hour >= 23 or hour <= 5) and any(ng in g for ng in NIGHT_GENRES): weights[i] *= 2.5 
    return random.choices(possible_genres, weights=weights, k=1)[0]

def get_user_dir():
    if not CURRENT_USER_ID: return BASE_MUSIC_DIR
    path = os.path.join(BASE_MUSIC_DIR, str(CURRENT_USER_ID))
    os.makedirs(path, exist_ok=True)
    return path

def apply_audio_effect(file_path, effect):
    if effect == "normal": return
    try:
        from pydub import AudioSegment
        sound = AudioSegment.from_file(file_path)
        new_sr = int(sound.frame_rate * 1.25) if effect == "nightcore" else int(sound.frame_rate * 0.85)
        sound = sound._spawn(sound.raw_data, overrides={'frame_rate': new_sr})
        sound = sound.set_frame_rate(44100).set_channels(2).set_sample_width(2) 
        sound.export(file_path, format="mp3", bitrate="320k")
    except: pass


# ==========================================
#  HTML ИНТЕРФЕЙС
# ==========================================
HTML_PAGE = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>MixingBear | PRO ENVIRONMENT</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script type="text/javascript" src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <script src="https://unpkg.com/wavesurfer.js@7"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    
    <style>
        body { background-color: #0f0f0f; color: #d4d4d4; font-family: 'Inter', sans-serif; overflow: hidden; user-select: none; }
        .font-mono { font-family: 'JetBrains Mono', monospace; }
        ::-webkit-scrollbar { width: 8px; height: 8px; }
        ::-webkit-scrollbar-track { background: #141414; border-left: 1px solid #222; border-bottom: 1px solid #222; }
        ::-webkit-scrollbar-thumb { background: #333; }
        ::-webkit-scrollbar-thumb:hover { background: #00E5FF; }

        .no-scrollbar::-webkit-scrollbar { display: none; }
        .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }

        .daw-panel { background: #181818; border: 1px solid #282828; }
        .daw-btn { background: #222; border: 1px solid #333; color: #aaa; transition: all 0.1s ease; cursor: pointer; text-transform: uppercase; letter-spacing: 1px; font-size: 11px; font-weight: 600; }
        .daw-btn:hover:not(:disabled) { background: #333; color: #fff; border-color: #555; }
        .daw-btn-primary { background: transparent; border: 1px solid #00E5FF; color: #00E5FF; }
        .daw-btn-primary:hover:not(:disabled) { background: #00E5FF; color: #000; box-shadow: 0 0 10px rgba(0, 229, 255, 0.3); }
        .daw-btn-danger { border-color: #ff3366; color: #ff3366; }
        .daw-btn-danger:hover:not(:disabled) { background: #ff3366; color: #fff; box-shadow: 0 0 10px rgba(255, 51, 102, 0.3); }

        .daw-input { background: #111; border: 1px solid #333; color: #00E5FF; font-family: 'JetBrains Mono', monospace; padding: 8px 12px; outline: none; transition: border 0.1s; font-size: 13px; }
        .daw-input:focus { border-color: #00E5FF; }
        @keyframes fadeSlide { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
        .animate-fade { animation: fadeSlide 0.2s ease forwards; }
        
        /*  ИСПРАВЛЕННАЯ СЕТКА  */
        .track-row { display: grid; grid-template-columns: 40px 40px 150px 1fr 60px; gap: 10px; align-items: center; border-bottom: 1px solid #1e1e1e; transition: background 0.1s; }
        .track-row:hover { background: #1e1e1e; }
        .track-row.selected { background: rgba(0, 229, 255, 0.05); border-left: 2px solid #00E5FF; }
        
        .daw-checkbox { appearance: none; width: 14px; height: 14px; border: 1px solid #555; background: #111; cursor: pointer; position: relative; }
        .daw-checkbox:checked { background: #00E5FF; border-color: #00E5FF; }
        .daw-checkbox:checked::after { content: ''; position: absolute; left: 4px; top: 1px; width: 4px; height: 8px; border: solid #000; border-width: 0 2px 2px 0; transform: rotate(45deg); }
        
        #toast { position: fixed; bottom: 100px; right: -300px; transition: right 0.3s cubic-bezier(0.2, 0.8, 0.2, 1); z-index: 1000; }
        .toast-visible { right: 20px !important; }
        #mynetwork { width: 100%; height: 65vh; border: 1px solid #333; background: #0a0a0a; outline: none; }
        
        .dna-tag { border: 1px solid #444; background: #111; color: #888; cursor: pointer; transition: all 0.2s; }
        .dna-tag:hover { border-color: #00E5FF; color: #fff; }
        .dna-tag.selected { background: rgba(0, 229, 255, 0.1); border-color: #00E5FF; color: #00E5FF; box-shadow: inset 0 0 10px rgba(0, 229, 255, 0.2); }
    </style>
</head>
<body class="flex flex-col h-screen overflow-hidden text-sm">

    <div id="profile-selector" class="fixed inset-0 bg-[#0a0a0a] z-[200] flex flex-col items-center justify-center">
        <div class="daw-panel p-10 max-w-2xl w-full border-t-2 border-t-[#00E5FF] shadow-[0_0_50px_rgba(0,0,0,0.8)]">
            <div class="flex items-center gap-4 mb-8 border-b border-[#333] pb-4">
                <i class="fa-solid fa-layer-group text-3xl text-[#00E5FF]"></i>
                <div>
                    <h1 class="text-2xl font-bold text-white tracking-widest uppercase">MIXINGBEAR OS <span class="text-xs text-[#00E5FF] font-mono">v6.0</span></h1>
                    <p class="text-xs text-gray-500 font-mono">WORKSPACE INITIALIZATION</p>
                </div>
            </div>
            <div class="grid grid-cols-2 gap-8">
                <div>
                    <h3 class="text-xs text-gray-400 font-bold uppercase mb-4 tracking-wider">Select Workspace</h3>
                    <div id="profiles-list" class="flex flex-col gap-2 max-h-[200px] overflow-y-auto pr-2"></div>
                </div>
                <div class="border-l border-[#222] pl-8">
                    <h3 class="text-xs text-gray-400 font-bold uppercase mb-4 tracking-wider">Create Workspace</h3>
                    <div class="flex flex-col gap-3">
                        <input type="text" id="new-user-id" placeholder="e.g. Act_1_Scene" class="daw-input w-full" onkeypress="if(event.key === 'Enter') createNewProfile()">
                        <button onclick="createNewProfile()" class="daw-btn daw-btn-primary py-3 w-full"><i class="fa-solid fa-plus mr-2"></i>Initialize</button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <div id="dna-calibration-modal" class="fixed inset-0 bg-black/95 z-[250] flex flex-col items-center justify-center hidden opacity-0 transition-opacity">
        <div class="daw-panel p-8 max-w-3xl w-full border-t-2 border-t-[#00E5FF]">
            <div class="text-center mb-8">
                <i class="fa-solid fa-network-wired text-4xl text-[#00E5FF] mb-4"></i>
                <h2 class="text-xl font-bold text-white tracking-widest uppercase">NEURAL DNA CALIBRATION</h2>
                <p class="text-xs text-gray-500 font-mono mt-2">Select 3-5 base genres to initialize the Markov Chain weights.</p>
            </div>
            <div class="flex flex-wrap gap-3 justify-center mb-8" id="dna-tags-container">
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'electronic')">Electronic</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'rock')">Rock</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'hip-hop')">Hip-Hop</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'pop')">Pop</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'metal')">Metal</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'jazz')">Jazz</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'classical')">Classical</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'rnb')">R&B</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'ambient')">Ambient</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'latin')">Latin</div>
                <div class="dna-tag px-4 py-2 text-xs font-mono font-bold uppercase" onclick="toggleDnaTag(this, 'lo-fi')">Lo-Fi</div>
            </div>
            <button onclick="submitDnaCalibration()" class="daw-btn daw-btn-primary w-full py-4 text-sm tracking-widest font-bold">INJECT WEIGHTS & START</button>
        </div>
    </div>

    <div id="toast" class="daw-panel border-l-4 border-l-[#00E5FF] p-4 shadow-2xl flex items-center gap-4 min-w-[300px]">
        <i id="toast-icon" class="fa-solid fa-info-circle text-[#00E5FF] text-lg"></i>
        <div id="toast-msg" class="font-mono text-xs text-gray-300">System ready.</div>
    </div>

    <div id="transition-editor" class="fixed inset-0 bg-black/90 z-[150] flex items-center justify-center hidden opacity-0 transition-opacity">
        <div class="daw-panel w-[550px] border-t-2 border-t-[#00E5FF]">
            <div class="p-5 border-b border-[#222] flex justify-between items-center bg-[#111]">
                <h2 class="text-sm font-bold text-white tracking-widest uppercase"><i class="fa-solid fa-sliders text-[#00E5FF] mr-2"></i> MASTERING CHAIN</h2>
                <button onclick="closeTransitionEditor()" class="text-gray-500 hover:text-white"><i class="fa-solid fa-xmark"></i></button>
            </div>
            <div class="p-6 flex flex-col gap-5">
                <select id="editor-mix-type" class="daw-input w-full">
                    <option value="classic">CROSSFADE (STANDARD)</option><option value="smart">SMART BEAT-SYNC (32 BEATS)</option>
                    <option value="mashup">AI MASHUP (VOCAL A + BEAT B)</option><option value="battle">AI BATTLE (PING-PONG)</option>
                </select>
                <div>
                    <div class="flex justify-between text-[10px] text-gray-500 font-bold uppercase mb-2 tracking-widest"><label>Overlap</label><span id="crossfade-val" class="text-[#00E5FF] font-mono">8 SEC</span></div>
                    <input type="range" id="editor-crossfade" min="0" max="30" value="8" class="w-full" oninput="document.getElementById('crossfade-val').innerText = this.value + ' SEC'">
                </div>
                <div class="bg-[#111] p-4 border border-[#222] flex flex-col gap-3">
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" id="editor-sync-bpm" checked class="daw-checkbox"><span class="text-xs text-gray-400 font-mono">TIME_STRETCH (BPM SYNC)</span></label>
                    <label class="flex items-center gap-3 cursor-pointer"><input type="checkbox" id="editor-cut-bass" checked class="daw-checkbox"><span class="text-xs text-gray-400 font-mono">LOW_CUT FILTER (400Hz)</span></label>
                </div>
                <button onclick="executeCustomMix()" class="daw-btn daw-btn-primary py-3 mt-2 font-bold tracking-widest">RENDER OUTPUT</button>
            </div>
        </div>
    </div>

    <div id="acoustic-modal" class="fixed inset-0 bg-black/90 z-[150] flex items-center justify-center hidden opacity-0 transition-opacity">
        <div class="daw-panel w-[900px] h-[600px] flex flex-col border-t-2 border-t-[#00E5FF]">
            <div class="p-4 border-b border-[#222] flex justify-between items-center bg-[#111] shrink-0">
                <div><h2 class="text-sm font-bold text-white tracking-widest uppercase"><i class="fa-solid fa-fingerprint text-[#00E5FF] mr-2"></i> MFCC SONIC VISUALIZER</h2></div>
                <button onclick="closeAcousticModal()" class="text-gray-500 hover:text-white"><i class="fa-solid fa-xmark text-lg"></i></button>
            </div>
            <div class="flex-1 flex overflow-hidden">
                <div class="w-1/2 bg-[#0a0a0a] border-r border-[#222] p-4 relative flex items-center justify-center">
                    <div class="w-full h-full p-2"><canvas id="mfccChart"></canvas></div>
                </div>
                <div id="acoustic-results" class="w-1/2 bg-[#111] overflow-y-auto flex flex-col"></div>
            </div>
        </div>
    </div>

    <div class="flex flex-1 overflow-hidden" id="app-container" style="display: none;">
        <div class="w-56 bg-[#111] border-r border-[#222] flex flex-col z-10">
            <div class="h-16 flex items-center px-6 border-b border-[#222] gap-3">
                <i class="fa-solid fa-wave-square text-[#00E5FF] text-xl"></i>
                <span class="font-black tracking-widest text-white">STUDIO</span>
            </div>
            <div class="flex-1 overflow-y-auto py-4">
                <div class="text-[10px] text-gray-500 font-bold px-6 mb-2 uppercase tracking-widest">Media & Assets</div>
                <nav class="flex flex-col gap-0.5 px-3 font-mono">
                    <button onclick="showTab('library', this)" class="nav-btn flex items-center gap-3 px-3 py-2 text-xs text-white bg-[#222] border-l-2 border-[#00E5FF]"><i class="fa-solid fa-folder-tree w-4 text-[#00E5FF]"></i> Media Pool</button>
                    <button onclick="showTab('search', this)" class="nav-btn flex items-center gap-3 px-3 py-2 text-xs text-gray-400 hover:text-white hover:bg-[#1a1a1a] border-l-2 border-transparent"><i class="fa-solid fa-cloud-arrow-down w-4"></i> Importer</button>
                </nav>
                <div class="text-[10px] text-gray-500 font-bold px-6 mt-8 mb-2 uppercase tracking-widest">Generative AI</div>
                <nav class="flex flex-col gap-0.5 px-3 font-mono">
                    <button onclick="showTab('discovery', this)" class="nav-btn flex items-center gap-3 px-3 py-2 text-xs text-gray-400 hover:text-white hover:bg-[#1a1a1a] border-l-2 border-transparent"><i class="fa-solid fa-compass w-4"></i> Discovery</button>
                    <button onclick="showTab('autodj', this)" class="nav-btn flex items-center gap-3 px-3 py-2 text-xs text-gray-400 hover:text-white hover:bg-[#1a1a1a] border-l-2 border-transparent"><i class="fa-solid fa-robot w-4"></i> Auto-Mixer</button>
                    <button onclick="showTab('concert', this)" class="nav-btn flex items-center gap-3 px-3 py-2 text-xs text-gray-400 hover:text-white hover:bg-[#1a1a1a] border-l-2 border-transparent"><i class="fa-solid fa-microphone-lines w-4"></i> Live Set</button>
                </nav>
                <div class="text-[10px] text-gray-500 font-bold px-6 mt-8 mb-2 uppercase tracking-widest">Analysis</div>
                <nav class="flex flex-col gap-0.5 px-3 font-mono">
                    <button onclick="showTab('dna', this)" class="nav-btn flex items-center gap-3 px-3 py-2 text-xs text-gray-400 hover:text-white hover:bg-[#1a1a1a] border-l-2 border-transparent"><i class="fa-solid fa-network-wired w-4"></i> Neural Graph</button>
                </nav>
            </div>
        </div>

        <div class="flex-1 flex flex-col bg-[#0f0f0f] relative">
            <div id="action-bar" class="h-12 bg-[#181818] border-b border-[#222] flex items-center px-6 gap-2 opacity-0 pointer-events-none transition-opacity absolute w-full top-[77px] z-30">
                <div class="text-xs font-bold text-[#00E5FF] font-mono mr-4"><span id="sel-count">0</span> SEL</div>
                <div class="w-px h-4 bg-[#333] mr-2"></div>
                <button onclick="startAcousticSearch()" id="btn-search" class="daw-btn px-3 py-1.5 hidden"><i class="fa-solid fa-fingerprint text-[#00E5FF] mr-1.5"></i> Find Similar</button>
                <button onclick="startAcousticMix()" id="btn-acoustic" class="daw-btn px-3 py-1.5 hidden"><i class="fa-solid fa-layer-group text-blue-400 mr-1.5"></i> Auto-Mix</button>
                <button onclick="startStem()" id="btn-stem" class="daw-btn px-3 py-1.5 hidden"><i class="fa-solid fa-scissors text-purple-400 mr-1.5"></i> Extract Stems</button>
                <button onclick="openTransitionEditor()" id="btn-mix" class="daw-btn daw-btn-primary px-3 py-1.5 hidden"><i class="fa-solid fa-sliders mr-1.5"></i> Open Studio</button>
                <div class="flex-1"></div>
                <button onclick="moveToFolder()" class="daw-btn px-3 py-1.5"><i class="fa-solid fa-folder-tree mr-1.5"></i> Move</button>
                <button onclick="deleteSelected()" class="daw-btn daw-btn-danger px-3 py-1.5 ml-2"><i class="fa-solid fa-trash"></i></button>
                <button onclick="clearSelection()" class="text-gray-500 hover:text-white ml-4 text-lg"><i class="fa-solid fa-xmark"></i></button>
            </div>

            <div id="tab-library" class="tab-content flex-1 overflow-y-auto bg-[#0f0f0f]">
                <div class="p-6 border-b border-[#1e1e1e] flex justify-between items-center bg-[#141414] sticky top-0 z-20">
                    <div><h2 class="text-lg font-bold text-white tracking-widest uppercase">Media Pool</h2></div>
                    <div class="flex gap-2"><button onclick="indexWholeLibrary()" class="daw-btn daw-btn-primary px-4 py-2"><i class="fa-solid fa-bolt mr-2"></i>Scan MFCC</button><button onclick="loadLibrary()" class="daw-btn px-4 py-2"><i class="fa-solid fa-rotate-right"></i></button></div>
                </div>
                
                <div class="grid grid-cols-[40px_40px_150px_1fr_60px] gap-10 items-center px-6 py-2 bg-[#111] border-b border-[#222] text-[10px] text-gray-500 font-bold uppercase tracking-widest sticky top-[77px] z-10">
                    <div class="pl-2">Sel</div>
                    <div>Play</div>
                    <div>Artist</div>
                    <div>Track Title</div>
                    <div class="text-right">Fmt</div>
                </div>
                
                <div id="library-list" class="flex flex-col pb-20 pt-2"></div>
            </div>

          <div id="tab-search" class="tab-content flex-1 hidden flex flex-col bg-[#0f0f0f]">
                <div class="p-6 border-b border-[#1e1e1e] bg-[#141414]">
                    <h2 class="text-lg font-bold text-white tracking-widest uppercase mb-4"><i class="fa-solid fa-cloud-arrow-down mr-2 text-[#00E5FF]"></i> Audio Importer (Browser)</h2>
                    <div class="flex gap-2 max-w-3xl">
                        <input type="text" id="search-input" placeholder="YouTube Keyword or URL..." class="daw-input w-full text-sm" onkeypress="if(event.key === 'Enter') searchYoutube()">
                        <button onclick="searchYoutube()" class="daw-btn daw-btn-primary px-6 py-2 font-bold" id="btn-search-yt">SCAN</button>
                    </div>
                </div>
                
                <div class="flex-1 p-8 flex flex-col justify-center relative">
                    <div id="search-loader" class="text-center text-[#00E5FF] font-mono text-sm hidden">
                        <i class="fa-solid fa-circle-notch fa-spin text-3xl mb-4"></i><br>PARSING REMOTE ASSETS...
                    </div>
                    
                    <div id="search-results-area" class="w-full hidden flex items-center justify-between gap-4">
                        
                        <button onclick="scrollSearch(-1)" class="shrink-0 w-12 h-12 bg-[#111] border border-[#333] text-[#00E5FF] hover:bg-[#00E5FF] hover:text-black z-20 shadow-[0_0_15px_rgba(0,229,255,0.2)] transition flex items-center justify-center rounded-full cursor-pointer">
                            <i class="fa-solid fa-chevron-left text-lg"></i>
                        </button>
                        
                        <div id="search-results-container" class="flex-1 flex overflow-x-auto gap-4 no-scrollbar scroll-smooth py-4" style="scroll-snap-type: x mandatory;">
                            </div>
                        
                        <button onclick="scrollSearch(1)" class="shrink-0 w-12 h-12 bg-[#111] border border-[#333] text-[#00E5FF] hover:bg-[#00E5FF] hover:text-black z-20 shadow-[0_0_15px_rgba(0,229,255,0.2)] transition flex items-center justify-center rounded-full cursor-pointer">
                            <i class="fa-solid fa-chevron-right text-lg"></i>
                        </button>
                        
                    </div>
                </div>
            </div>
            <div id="tab-dna" class="tab-content flex-1 hidden flex flex-col">
                <div class="p-6 border-b border-[#1e1e1e] flex justify-between items-center bg-[#141414]">
                    <div><h2 class="text-lg font-bold text-white tracking-widest uppercase">Neural Graph</h2></div>
                    <div class="flex items-center gap-2 bg-[#0a0a0a] p-1 rounded border border-[#222]">
                        <button onclick="switchGraph('genre')" id="btn-graph-genre" class="daw-btn text-white bg-[#222] border-[#00E5FF] px-4 py-1.5">GENRES</button>
                        <button onclick="switchGraph('artist')" id="btn-graph-artist" class="daw-btn border-transparent px-4 py-1.5">ARTISTS</button>
                    </div>
                </div>
                <div id="graph-toolbar" class="bg-[#111] border-b border-[#222] p-3 flex items-center gap-3 opacity-50 pointer-events-none transition-opacity">
                    <div class="text-xs font-mono text-gray-400 mr-4">Target: <span id="graph-selected-node" class="text-[#00E5FF] font-bold">NONE</span></div>
                    <div class="w-px h-4 bg-[#333]"></div>
                    <button onclick="graphAddLink()" class="daw-btn px-3 py-1">Link</button>
                    <button onclick="graphChangeWeight(5)" class="daw-btn px-3 py-1">Weight +</button>
                    <button onclick="graphChangeWeight(-5)" class="daw-btn px-3 py-1">Weight -</button>
                    <button onclick="graphDeleteNode()" class="daw-btn daw-btn-danger px-3 py-1 ml-auto">Delete</button>
                </div>
                <div id="mynetwork" class="flex-1 border-none rounded-none"></div>
            </div>

            <div id="tab-discovery" class="tab-content flex-1 hidden flex flex-col items-center justify-center relative">
                <div id="discovery-loader" class="text-[#00E5FF] font-mono text-sm hidden flex flex-col items-center gap-4"><i class="fa-solid fa-circle-notch fa-spin text-3xl"></i>CALCULATING...</div>
                <div id="discovery-ui" class="flex flex-col items-center hidden w-full max-w-md">
                    <div class="w-full daw-panel p-8 flex flex-col items-center justify-center relative overflow-hidden mb-6 border-t-2 border-t-[#00E5FF]">
                        <i class="fa-solid fa-compact-disc text-6xl text-[#333] mb-6 animate-[spin_4s_linear_infinite]"></i>
                        <h2 id="disc-title" class="text-base font-bold text-white text-center z-10 mb-4 line-clamp-2">Track Name</h2>
                        <span id="disc-genre" class="bg-[#222] border border-[#444] text-[#00E5FF] font-mono text-[10px] px-3 py-1 tracking-widest">GENRE</span>
                    </div>
                    <div class="flex gap-4 w-full">
                        <button onclick="discoveryAction('skip')" class="daw-btn flex-1 py-3 text-red-400 border-red-900/50 hover:bg-red-900/20"><i class="fa-solid fa-xmark mr-2"></i> REJECT</button>
                        <button onclick="discoveryAction('like')" class="daw-btn daw-btn-primary flex-1 py-3"><i class="fa-solid fa-heart mr-2"></i> ADD TO DNA</button>
                    </div>
                </div>
                <button onclick="startDiscovery()" id="start-disc-btn" class="daw-btn daw-btn-primary px-8 py-3 tracking-widest">INITIATE SEQUENCE</button>
            </div>

            <div id="tab-concert" class="tab-content flex-1 hidden p-8">
                <h2 class="text-lg font-bold text-white tracking-widest uppercase mb-6"><i class="fa-solid fa-microphone-lines mr-2 text-[#00E5FF]"></i> Live Set</h2>
                <textarea id="concert-inputs" rows="4" class="daw-input w-full mb-4"></textarea>
                <button onclick="startConcert()" class="daw-btn daw-btn-primary px-6 py-3 w-48 font-bold tracking-widest">GENERATE MIX</button>
            </div>

            <div id="tab-autodj" class="tab-content flex-1 hidden p-8">
                <h2 class="text-lg font-bold text-white tracking-widest uppercase mb-6"><i class="fa-solid fa-robot mr-2 text-[#00E5FF]"></i> Auto-Mixer</h2>
                <select id="dj-effect" class="daw-input w-full max-w-sm mb-4">
                    <option value="normal">NONE (STUDIO MASTER)</option><option value="nightcore">NIGHTCORE (1.25x PITCH)</option><option value="slowed">SLOWED & REVERB (0.85x)</option>
                </select><br>
                <button onclick="startAutoDj()" class="daw-btn daw-btn-primary px-6 py-3 font-bold tracking-widest">GENERATE SET</button>
            </div>
        </div>
    </div>

    <div class="h-20 daw-panel border-t border-[#222] flex items-center px-4 z-50 fixed bottom-0 w-full" id="transport-bar" style="display:none;">
        <div class="flex items-center gap-3 w-64 shrink-0 border-r border-[#222] pr-4">
            <div class="w-10 h-10 bg-[#0a0a0a] border border-[#333] flex items-center justify-center"><i class="fa-solid fa-waveform text-[#00E5FF] text-xs"></i></div>
            <div class="overflow-hidden">
                <div id="now-playing-title" class="font-mono text-xs font-bold text-white truncate">NO SIGNAL</div>
            </div>
        </div>
        <div class="flex-1 flex items-center px-6 gap-4">
            <button onclick="wavesurfer.playPause()" class="w-10 h-10 bg-[#222] border border-[#333] text-white hover:bg-[#00E5FF] hover:text-black transition flex items-center justify-center shrink-0"><i id="play-btn-icon" class="fa-solid fa-play"></i></button>
            <div class="flex-1 relative flex items-center">
                <div id="time-current" class="text-[10px] text-[#00E5FF] font-mono w-10 text-right mr-3">0:00</div>
                <div id="waveform" class="flex-1 h-12 bg-[#0a0a0a] border border-[#222]"></div>
                <div id="time-total" class="text-[10px] text-gray-500 font-mono w-10 ml-3">0:00</div>
            </div>
        </div>
        <div class="w-48 shrink-0 flex justify-end items-center gap-3 border-l border-[#222] pl-4">
            <i class="fa-solid fa-volume-high text-gray-500 text-xs"></i>
            <input type="range" id="volume-bar" min="0" max="1" step="0.01" value="0.8" class="w-24">
        </div>
    </div>

    <script>
        const wavesurfer = WaveSurfer.create({ container: '#waveform', waveColor: '#333333', progressColor: '#00E5FF', barWidth: 2, barGap: 1, height: 46, cursorWidth: 1, cursorColor: '#fff', normalize: true });
        wavesurfer.on('play', () => { document.getElementById('play-btn-icon').className = 'fa-solid fa-pause'; });
        wavesurfer.on('pause', () => { document.getElementById('play-btn-icon').className = 'fa-solid fa-play'; });
        wavesurfer.on('audioprocess', () => { document.getElementById('time-current').innerText = formatTime(wavesurfer.getCurrentTime()); });
        wavesurfer.on('ready', () => { document.getElementById('time-total').innerText = formatTime(wavesurfer.getDuration()); wavesurfer.play(); });
        document.getElementById('volume-bar').addEventListener('input', (e) => { wavesurfer.setVolume(Number(e.target.value)); });
        function formatTime(s) { const m = Math.floor(s/60); const sc = Math.floor(s%60); return `${m}:${sc < 10 ? '0':''}${sc}`; }
        function playTrack(path) { wavesurfer.load(`/audio/${path.split('/').map(encodeURIComponent).join('/')}`); document.getElementById('now-playing-title').innerText = path.split('/').pop(); }

        let currentProfileIsNew = false;
        let selectedDnaGenres = new Set();

        async function loadProfiles() {
            const res = await fetch('/api/profiles'); const profiles = await res.json();
            const list = document.getElementById('profiles-list');
            if (profiles.length === 0) { list.innerHTML = `<div class="text-xs font-mono text-gray-500">No workspaces found.</div>`; return; }
            list.innerHTML = profiles.map(p => `<div onclick="selectProfile('${p}')" class="daw-btn text-left px-4 py-2 font-mono flex items-center justify-between group"><span><i class="fa-solid fa-folder text-gray-500 mr-2 group-hover:text-[#00E5FF] transition"></i> ${p}</span><i class="fa-solid fa-chevron-right text-[10px] opacity-0 group-hover:opacity-100 text-[#00E5FF]"></i></div>`).join('');
        }
        
        async function createNewProfile() { const val = document.getElementById('new-user-id').value.trim().replace(/[^a-zA-Z0-9_-]/g, '_'); if(val) await selectProfile(val); }
        
        async function selectProfile(id) {
            const res = await fetch('/api/set_profile', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id}) });
            const data = await res.json();
            document.getElementById('profile-selector').style.display = 'none';
            if(data.is_new) {
                const modal = document.getElementById('dna-calibration-modal');
                modal.classList.remove('hidden'); setTimeout(() => modal.classList.remove('opacity-0'), 10);
            } else { openMainWorkspace(id); }
        }

        function openMainWorkspace(id) {
            document.getElementById('app-container').style.display = 'flex';
            document.getElementById('transport-bar').style.display = 'flex';
            document.getElementById('current-user-display').innerText = `WKSP: ${id}`;
            showToast(`Workspace active: ${id}`); loadLibrary();
        }

        function toggleDnaTag(element, genre) {
            if(selectedDnaGenres.has(genre)) { selectedDnaGenres.delete(genre); element.classList.remove('selected'); } 
            else { if(selectedDnaGenres.size >= 5) { showToast("Max 5 genres allowed", true); return; } selectedDnaGenres.add(genre); element.classList.add('selected'); }
        }

        async function submitDnaCalibration() {
            if(selectedDnaGenres.size === 0) return showToast("Select at least 1 genre", true);
            await fetch('/api/seed_dna', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({genres: Array.from(selectedDnaGenres)}) });
            document.getElementById('dna-calibration-modal').classList.add('hidden');
            openMainWorkspace(document.getElementById('new-user-id').value || "New Workspace");
        }

        function scrollSearch(dir) { 
            const container = document.getElementById('search-results-container');
            container.scrollBy({ left: dir * 300, behavior: 'smooth' }); 
        }

        async function searchYoutube() {
            const query = document.getElementById('search-input').value.trim(); if(!query) return;
            document.getElementById('search-results-area').classList.add('hidden');
            document.getElementById('search-loader').classList.remove('hidden');
            document.getElementById('btn-search-yt').disabled = true;

            const res = await fetch('/api/search_yt', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query}) });
            const data = await res.json();
            
            document.getElementById('search-loader').classList.add('hidden'); 
            document.getElementById('btn-search-yt').disabled = false;

            if(data.success && data.results.length > 0) {
                const container = document.getElementById('search-results-container');
                let html = '';
                data.results.forEach((item) => {
                    //  Защита от кавычек в названиях видео с YouTube!
                    const safeTitle = item.title.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
                    
                    html += `
                    <div class="daw-panel shrink-0 w-72 flex flex-col p-4 border-t-2 border-t-[#333] hover:border-t-[#00E5FF] transition group" style="scroll-snap-align: start;">
                        <div class="h-20 flex items-center justify-center bg-[#0a0a0a] border border-[#222] mb-4 text-[#333] group-hover:text-[#00E5FF] transition"><i class="fa-solid fa-music text-3xl"></i></div>
                        <div class="font-bold text-gray-300 text-xs mb-2 line-clamp-2 h-8" title="${safeTitle}">${safeTitle}</div>
                        <button onclick="downloadSpecificTrack('${item.url}')" class="daw-btn daw-btn-primary w-full py-2 mt-auto"><i class="fa-solid fa-download mr-2"></i>EXTRACT</button>
                    </div>`;
                });
                container.innerHTML = html; 
                document.getElementById('search-results-area').classList.remove('hidden');
            } else { 
                showToast("No results found.", true); 
            }
        }

        async function downloadSpecificTrack(url) {
            showToast("Extraction started...");
            const res = await fetch('/api/download', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query: url}) });
            const data = await res.json();
            if(data.success) { showToast(`Neural Tag: ${data.genre}`); playTrack(data.filename); loadLibrary(); } else showToast(data.error, true);
        }

        let currentGraphType = 'genre';
        function switchGraph(type) {
            currentGraphType = type;
            document.getElementById('btn-graph-genre').classList.remove('text-white', 'bg-[#222]', 'border-[#00E5FF]'); document.getElementById('btn-graph-genre').classList.add('border-transparent');
            document.getElementById('btn-graph-artist').classList.remove('text-white', 'bg-[#222]', 'border-[#00E5FF]'); document.getElementById('btn-graph-artist').classList.add('border-transparent');
            document.getElementById(`btn-graph-${type}`).classList.remove('border-transparent'); document.getElementById(`btn-graph-${type}`).classList.add('text-white', 'bg-[#222]', 'border-[#00E5FF]');
            loadGraph();
        }

        let network = null, selectedNodeId = null;
        async function loadGraph() {
            const res = await fetch(`/api/graph_data?type=${currentGraphType}`); 
            const data = await res.json();
            const graphData = { nodes: new vis.DataSet(data.nodes), edges: new vis.DataSet(data.edges) };
            const colorEdge = currentGraphType === 'artist' ? 'rgba(255, 51, 102, 0.3)' : 'rgba(0, 229, 255, 0.3)';
            if (network) network.destroy();
            network = new vis.Network(document.getElementById('mynetwork'), graphData, { nodes: { shape: 'box', font: { color: '#fff', face: 'JetBrains Mono' }, color: { background: '#111', border: '#444' }, margin: 10 }, edges: { color: colorEdge, arrows: { to: { enabled: true, scaleFactor: 0.5 } }, smooth: { type: 'continuous' } }, physics: { solver: 'forceAtlas2Based', forceAtlas2Based: { gravitationalConstant: -100, centralGravity: 0.01, springLength: 200 } }});
            network.on("click", function (params) {
                const tb = document.getElementById('graph-toolbar');
                if (params.nodes.length > 0) { selectedNodeId = params.nodes[0]; document.getElementById('graph-selected-node').innerText = selectedNodeId; tb.classList.remove('opacity-50', 'pointer-events-none'); } 
                else { selectedNodeId = null; document.getElementById('graph-selected-node').innerText = "NONE"; tb.classList.add('opacity-50', 'pointer-events-none'); }
            });
        }
        
        async function graphAddLink() { if(!selectedNodeId) return; const t = prompt(`Target for [${selectedNodeId}]:`); if(!t) return; await fetch('/api/edit_graph', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action: 'add_link', from: selectedNodeId, to: t, weight: 3 }) }); loadGraph(); }
        async function graphChangeWeight(c) { if(!selectedNodeId) return; await fetch('/api/edit_graph', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action: 'change_weight', node: selectedNodeId, change: c }) }); loadGraph(); }
        async function graphDeleteNode() { if(!selectedNodeId || !confirm(`Delete [${selectedNodeId}]?`)) return; await fetch('/api/edit_graph', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ action: 'delete_node', node: selectedNodeId }) }); loadGraph(); }

        let selectedTracks = new Set(), currentDiscoveryFile = "";
        function showToast(msg, isErr = false) {
            const t = document.getElementById('toast'), ic = document.getElementById('toast-icon');
            document.getElementById('toast-msg').innerText = msg;
            t.style.borderLeftColor = isErr ? "#ff3366" : "#00E5FF";
            ic.className = `fa-solid ${isErr ? 'fa-triangle-exclamation text-[#ff3366]' : 'fa-circle-check text-[#00E5FF]'} text-lg`;
            t.classList.add('toast-visible'); setTimeout(() => { t.classList.remove('toast-visible'); }, 3000);
        }
        function showTab(tabId, btn) {
            document.querySelectorAll('.tab-content').forEach(el => { el.classList.add('hidden'); el.classList.remove('animate-fade'); });
            const tab = document.getElementById('tab-' + tabId); tab.classList.remove('hidden'); void tab.offsetWidth; tab.classList.add('animate-fade');
            if (btn) { document.querySelectorAll('.nav-btn').forEach(b => { b.classList.remove('text-white', 'bg-[#222]', 'border-[#00E5FF]'); b.classList.add('text-gray-400', 'border-transparent'); }); btn.classList.add('text-white', 'bg-[#222]', 'border-[#00E5FF]'); }
            if(tabId === 'library') loadLibrary(); if(tabId === 'dna') loadGraph();
        }
        function toggleSelection(path, e) {
            e.stopPropagation(); const row = document.getElementById(`row-${path.replace(/[^a-zA-Z0-9]/g, '')}`);
            if(selectedTracks.has(path)) { selectedTracks.delete(path); if(row) row.classList.remove('selected'); } else { selectedTracks.add(path); if(row) row.classList.add('selected'); }
            updateActionBar();
        }
        function clearSelection() { selectedTracks.clear(); document.querySelectorAll('.daw-checkbox').forEach(c => c.checked = false); document.querySelectorAll('.track-row').forEach(r => r.classList.remove('selected')); updateActionBar(); }
        function updateActionBar() {
            const bar = document.getElementById('action-bar'); document.getElementById('sel-count').innerText = selectedTracks.size;
            document.getElementById('btn-acoustic').classList.toggle('hidden', selectedTracks.size !== 1); document.getElementById('btn-search').classList.toggle('hidden', selectedTracks.size !== 1);
            document.getElementById('btn-stem').classList.toggle('hidden', selectedTracks.size === 0); document.getElementById('btn-mix').classList.toggle('hidden', selectedTracks.size < 2);
            if(selectedTracks.size > 0) bar.classList.remove('opacity-0', 'pointer-events-none'); else bar.classList.add('opacity-0', 'pointer-events-none');
        }

        async function indexWholeLibrary() { showToast("MFCC extraction running..."); const res = await fetch('/api/index_all', { method: 'POST' }); const data = await res.json(); if(data.success) showToast(`DB Updated. Processed: ${data.stats.processed_songs}`); }
        
        //  ИСПРАВЛЕННЫЙ LOAD LIBRARY С АРТИСТАМИ И ГАЛОЧКАМИ 
        async function loadLibrary() {
            const res = await fetch('/api/library'); const files = await res.json();
            const list = document.getElementById('library-list');
            if (!files.length) { list.innerHTML = "<div class='text-xs text-gray-600 font-mono p-6'>// DIRECTORY IS EMPTY</div>"; return; }
            const grouped = files.reduce((a, f) => { if(!a[f.folder]) a[f.folder] = []; a[f.folder].push(f); return a; }, {});
            let html = '';
            for(const [folder, tracks] of Object.entries(grouped)) {
                if(folder !== "Главная") html += `<div class="text-[10px] font-bold mt-4 mb-1 px-6 text-[#00E5FF] uppercase tracking-widest bg-[#111] py-1 border-y border-[#222]">/ ${folder}</div>`;
                html += tracks.map(f => {
                    const rowId = `row-${f.rel_path.replace(/[^a-zA-Z0-9]/g, '')}`, isSel = selectedTracks.has(f.rel_path), ext = f.filename.split('.').pop().toUpperCase();
                    
                    let artist = "Unknown Artist";
                    let title = f.filename;
                    const sepIdx = f.filename.indexOf(' - ');
                    if(sepIdx > -1) {
                        artist = f.filename.substring(0, sepIdx);
                        title = f.filename.substring(sepIdx + 3).replace('.' + f.filename.split('.').pop(), '');
                    } else {
                        title = title.replace('.' + f.filename.split('.').pop(), '');
                    }

                    return `<div id="${rowId}" class="track-row px-6 py-2 cursor-pointer ${isSel ? 'selected' : ''}" onclick="playTrack('${f.rel_path}')">
                        <div class="pl-2" onclick="event.stopPropagation()"><input type="checkbox" class="daw-checkbox" onclick="toggleSelection('${f.rel_path}', event)" ${isSel ? 'checked' : ''}></div>
                        <div class="text-gray-500 hover:text-[#00E5FF]"><i class="fa-solid fa-play"></i></div>
                        <div class="truncate text-[#00E5FF] font-bold text-xs">${artist}</div>
                        <div class="truncate text-gray-300 text-xs font-mono">${title}</div>
                        <div class="text-right text-[10px] text-gray-600 font-mono">${ext}</div>
                    </div>`;
                }).join('');
            }
            list.innerHTML = html; updateActionBar();
        }

        async function deleteSelected() { if(!confirm(`Delete items?`)) return; await fetch('/api/delete', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths: Array.from(selectedTracks)}) }); clearSelection(); loadLibrary(); }
        async function moveToFolder() { const f = prompt("Target directory:"); if(f) { await fetch('/api/move', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths: Array.from(selectedTracks), folder: f}) }); clearSelection(); loadLibrary(); } }
        async function startStem() { showToast("Demucs processing..."); const res = await fetch('/api/stem', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths: Array.from(selectedTracks)}) }); const data = await res.json(); if(data.success) { showToast("Done."); clearSelection(); loadLibrary(); playTrack(data.filename.split('/').pop()); } }

        function openTransitionEditor() { document.getElementById('transition-editor').classList.remove('hidden'); setTimeout(() => document.getElementById('transition-editor').classList.remove('opacity-0'), 10); }
        function closeTransitionEditor() { document.getElementById('transition-editor').classList.add('opacity-0'); setTimeout(() => document.getElementById('transition-editor').classList.add('hidden'), 200); }
        async function executeCustomMix() {
            closeTransitionEditor(); showToast("Rendering audio...");
            const res = await fetch('/api/custom_mix', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ paths: Array.from(selectedTracks), mix_type: document.getElementById('editor-mix-type').value, params: { crossfade_sec: parseInt(document.getElementById('editor-crossfade').value), sync_bpm: document.getElementById('editor-sync-bpm').checked, cut_bass: document.getElementById('editor-cut-bass').checked } }) });
            const data = await res.json(); if(data.success) { showToast("Render complete."); clearSelection(); loadLibrary(); playTrack(data.filename.split('/').pop()); } else showToast(data.error, true);
        }

        let mfccChartInstance = null; let currentSeedFp = []; let currentTracksData = [];
        function initOrUpdateChart(seedFp, targetFp, targetName) {
            const ctx = document.getElementById('mfccChart').getContext('2d');
            const labels = Array.from({length: 24}, (_, i) => i < 12 ? `T${i+1}` : `D${i-11}`);
            const datasets = [{ label: 'Seed Track', data: seedFp, backgroundColor: 'rgba(0, 229, 255, 0.2)', borderColor: '#00E5FF', pointBackgroundColor: '#00E5FF', borderWidth: 2, pointRadius: 1 }];
            if (targetFp) { datasets.push({ label: targetName || 'Match', data: targetFp, backgroundColor: 'rgba(255, 51, 102, 0.2)', borderColor: '#ff3366', pointBackgroundColor: '#ff3366', borderWidth: 2, pointRadius: 1 }); }
            if (mfccChartInstance) { mfccChartInstance.data.datasets = datasets; mfccChartInstance.update('none'); } 
            else { mfccChartInstance = new Chart(ctx, { type: 'radar', data: { labels: labels, datasets: datasets }, options: { responsive: true, maintainAspectRatio: false, scales: { r: { angleLines: { color: '#222' }, grid: { color: '#222' }, pointLabels: { color: '#555', font: {family: 'JetBrains Mono', size: 9} }, ticks: { display: false } } }, plugins: { legend: { labels: { color: '#aaa', font: {family: 'JetBrains Mono', size: 10} }, position: 'bottom' } } } }); }
        }
        function previewChart(index) { if(!currentTracksData[index]) return; const track = currentTracksData[index]; const shortName = track.filename.length > 20 ? track.filename.substring(0,20) + '...' : track.filename; initOrUpdateChart(currentSeedFp, track.fingerprint, shortName); }
        function closeAcousticModal() { document.getElementById('acoustic-modal').classList.add('opacity-0'); setTimeout(() => document.getElementById('acoustic-modal').classList.add('hidden'), 200); }

        async function startAcousticSearch() {
            if (selectedTracks.size !== 1) return;
            document.getElementById('acoustic-modal').classList.remove('hidden'); setTimeout(() => document.getElementById('acoustic-modal').classList.remove('opacity-0'), 10);
            const resultsContainer = document.getElementById('acoustic-results');
            resultsContainer.innerHTML = '<div class="p-10 text-center text-[#00E5FF] font-mono text-xs"><i class="fa-solid fa-circle-notch fa-spin text-2xl mb-3"></i><br>SCANNING VECTORS...</div>';
            if(mfccChartInstance) { mfccChartInstance.destroy(); mfccChartInstance = null; }

            const res = await fetch('/api/acoustic_search', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ paths: Array.from(selectedTracks), top_n: 12 }) });
            const data = await res.json();
            
            if(data.success) {
                if(data.results.length === 0) { resultsContainer.innerHTML = '<div class="p-10 text-center text-gray-500 font-mono text-xs">NO CORRELATION.</div>'; return; }
                currentSeedFp = data.seed_fingerprint; currentTracksData = data.results;
                initOrUpdateChart(currentSeedFp, null, null);
                
                let html = '';
                data.results.forEach((track, index) => {
                    html += `<div class="flex items-center justify-between px-6 py-4 border-b border-[#222] hover:bg-[#1e1e1e] cursor-pointer transition" onmouseenter="previewChart(${index})" onclick="playTrack('${track.path}')"><div class="flex items-center gap-4 truncate"><div class="text-gray-600 hover:text-[#00E5FF]"><i class="fa-solid fa-play"></i></div><div class="text-gray-300 font-mono text-[11px] truncate">${track.filename}</div></div><div class="text-[#00E5FF] font-mono text-[10px] font-bold shrink-0 ml-4">${track.score}%</div></div>`;
                });
                resultsContainer.innerHTML = html;
            } else { resultsContainer.innerHTML = `<div class="text-[#ff3366] font-mono text-xs p-6">${data.error}</div>`; showToast(data.error, true); }
        }

        async function startAcousticMix() { showToast("Analyzing sonic profile..."); const res = await fetch('/api/acoustic_mix', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({paths: Array.from(selectedTracks)}) }); const data = await res.json(); if(data.success) { showToast("Sequence generated."); clearSelection(); loadLibrary(); playTrack(data.filename.split('/').pop()); } else showToast(data.error, true); }
        async function startConcert() { const tracks = document.getElementById('concert-inputs').value; if(!tracks) return; showToast("Processing live data..."); const res = await fetch('/api/concert', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({tracks}) }); const data = await res.json(); if(data.success) { showToast("Live Set Generated."); playTrack(data.filename.split('/').pop()); loadLibrary(); } else showToast(`Error: ${data.error}`, true); }
        
        async function startDiscovery() { document.getElementById('start-disc-btn').classList.add('hidden'); document.getElementById('discovery-loader').classList.remove('hidden'); document.getElementById('discovery-ui').classList.add('hidden'); const res = await fetch('/api/discovery/next'); const data = await res.json(); document.getElementById('discovery-loader').classList.add('hidden'); if(data.success) { document.getElementById('discovery-ui').classList.remove('hidden'); document.getElementById('disc-title').innerText = data.title; document.getElementById('disc-genre').innerText = data.genre; currentDiscoveryFile = data.filename; playTrack(data.filename); } else { showToast(data.error, true); document.getElementById('start-disc-btn').classList.remove('hidden'); } }
        async function discoveryAction(action) { document.getElementById('discovery-ui').classList.add('hidden'); document.getElementById('discovery-loader').classList.remove('hidden'); wavesurfer.pause(); await fetch('/api/discovery/action', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({action, filename: currentDiscoveryFile}) }); if(action === 'like') showToast("Node updated."); startDiscovery(); }
        async function startAutoDj() { showToast("Executing auto-mix algorithm..."); const res = await fetch('/api/autodj', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({effect: document.getElementById('dj-effect').value}) }); const data = await res.json(); if(data.success) { showToast("Mix ready."); playTrack(data.filename.split('/').pop()); loadLibrary(); } else showToast(data.error, true); }

        loadProfiles();
    </script>
</body>
</html>
"""

# ==========================================
#  АПИ МАРШРУТЫ FLASK
# ==========================================
@app.route("/")
def index(): return render_template_string(HTML_PAGE)

@app.route("/api/profiles")
def api_profiles():
    profiles = [d for d in os.listdir(BASE_MUSIC_DIR) if os.path.isdir(os.path.join(BASE_MUSIC_DIR, d))]
    return jsonify(profiles)

@app.route("/api/set_profile", methods=["POST"])
def api_set_profile():
    global CURRENT_USER_ID
    CURRENT_USER_ID = request.json.get('id')
    udir = get_user_dir()
    mix_folder = os.path.join(udir, "Mixes")
    os.makedirs(mix_folder, exist_ok=True)
    history_file = get_user_history_file(CURRENT_USER_ID)
    is_new = not os.path.exists(history_file)
    return jsonify({"success": True, "is_new": is_new})

@app.route("/api/seed_dna", methods=["POST"])
def api_seed_dna():
    if not CURRENT_USER_ID: return jsonify({"success": False})
    genres = request.json.get('genres', [])
    for g in genres:
        add_user_preference(CURRENT_USER_ID, f"DNA SEED - {g.upper()}", genre=g)
        record_user_transition(CURRENT_USER_ID, "start", "start", g)
    return jsonify({"success": True})

@app.route("/audio/<path:filename>")
def serve_audio(filename): 
    return send_from_directory(get_user_dir(), filename)

@app.route("/api/library")
def api_library():
    udir = get_user_dir()
    files_data = []
    for root, dirs, files in os.walk(udir):
        for f in files:
            if f.endswith(('.mp3', '.wav')):
                rel_path = os.path.relpath(os.path.join(root, f), udir)
                folder = os.path.dirname(rel_path) or "Главная"
                files_data.append({"filename": f, "rel_path": rel_path.replace("\\", "/"), "folder": folder, "mtime": os.path.getmtime(os.path.join(root, f))})
    files_data.sort(key=lambda x: x['mtime'], reverse=True)
    return jsonify(files_data)

@app.route("/api/index_all", methods=["POST"])
def api_index_all():
    if not CURRENT_USER_ID: return jsonify({"success": False})
    try:
        audio_processor = src.audio_processor.AudioProcessor()
        library_manager = src.library_manager.LibraryManager(audio_processor)
        stats = library_manager.build_library(get_user_dir(), recursive=True, force_rebuild=False)
        return jsonify({"success": True, "stats": stats})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route("/api/search_yt", methods=["POST"])
def api_search_yt():
    query = request.json.get('query', '')
    if not query: return jsonify({"success": False})
    results = search_tracks_on_youtube(query, limit=10)
    return jsonify({"success": True, "results": results})

@app.route("/api/download", methods=["POST"])
def api_download():
    if not CURRENT_USER_ID: return jsonify({"success": False})
    udir, query = get_user_dir(), request.json.get('query', '')
    results = search_tracks_on_youtube(query, 1)
    if not results: return jsonify({"success": False, "error": "Not found"})
    
    urls = [r['url'] for r in results]
    if len(urls) == 1:
        success, path = download_track_by_url(urls[0], udir)
        if success:
            clean = results[0]['title'].replace("(Official Video)", "").strip()
            genre = get_track_genre(clean) or "pop"
            artist = extract_artist(results[0]['title'])
            
            record_user_transition(CURRENT_USER_ID, "start", "start", genre) 
            record_artist_transition(CURRENT_USER_ID, "ROOT", artist) # ИСПРАВЛЕНИЕ ДЛЯ АРТИСТА
            
            add_user_preference(CURRENT_USER_ID, results[0]['title'], genre=genre)
            return jsonify({"success": True, "filename": os.path.basename(path), "genre": genre})
        return jsonify({"success": False, "error": str(path)})
    else:
        dl_res = download_multiple_tracks(urls, udir, max_workers=5)
        success_dls = [r for r in dl_res if r['success']]
        if success_dls: return jsonify({"success": True, "filename": f"Playlist ({len(success_dls)} items)", "genre": "MIXED"})
        else: return jsonify({"success": False, "error": "Failed to extract."})

@app.route("/api/delete", methods=["POST"])
def api_delete():
    for p in request.json.get('paths', []):
        try: path = os.path.join(get_user_dir(), p.replace("..", "")); os.remove(path)
        except: pass
    return jsonify({"success": True})

@app.route("/api/move", methods=["POST"])
def api_move():
    udir, folder = get_user_dir(), request.json.get('folder', 'Главная').replace("..", "").replace("/", "")
    target_dir = udir if folder == "Главная" else os.path.join(udir, folder)
    os.makedirs(target_dir, exist_ok=True)
    for p in request.json.get('paths', []):
        try: src_file = os.path.join(udir, p.replace("..", "")); shutil.move(src_file, os.path.join(target_dir, os.path.basename(src_file)))
        except: pass
    return jsonify({"success": True})

@app.route("/api/stem", methods=["POST"])
def api_stem():
    udir = get_user_dir()
    paths = request.json.get('paths', [])
    if not paths: return jsonify({"success": False})
    last_minus = None
    for p in paths:
        try:
            minus, voc = extract_minus(os.path.normpath(os.path.join(udir, p.replace("..", "").replace("/", os.sep))), udir)
            if minus: last_minus = os.path.relpath(minus, udir).replace("\\", "/")
        except: pass
    return jsonify({"success": True, "filename": last_minus, "message": "Готово"}) if last_minus else jsonify({"success": False, "error": "Ошибка"})

@app.route("/api/custom_mix", methods=["POST"])
def api_custom_mix():
    udir = get_user_dir()
    paths, mix_type = request.json.get('paths', []), request.json.get('mix_type', 'classic')
    if len(paths) < 2: return jsonify({"success": False})
    out_path = os.path.join(udir, "Mixes", f"{mix_type}_mix_{int(time.time())}.mp3")
    abs_paths = [os.path.join(udir, p.replace("..", "")) for p in paths]
    try:
        if mix_type == "smart": succ, res = create_smart_transition(abs_paths[0], abs_paths[1], out_path)
        elif mix_type == "mashup": succ, res = create_mashup(abs_paths[0], abs_paths[1], out_path, udir)
        elif mix_type == "battle": succ, res = create_vocal_battle(abs_paths[0], abs_paths[1], out_path, udir)
        else: succ, res = create_continuous_mix(abs_paths, out_path)
        return jsonify({"success": True, "filename": f"Mixes/{os.path.basename(out_path)}"}) if succ else jsonify({"success": False, "error": str(res)})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route("/api/acoustic_search", methods=["POST"])
def api_acoustic_search():
    udir = get_user_dir()
    paths = request.json.get('paths', [])
    top_n = request.json.get('top_n', 10)
    if len(paths) != 1: return jsonify({"success": False, "error": "Select 1 track."})
    seed_abs = os.path.normpath(os.path.join(udir, paths[0].replace("..", "").replace("/", os.sep)))
    try:
        audio_processor = src.audio_processor.AudioProcessor()
        library_manager = src.library_manager.LibraryManager(audio_processor)
        if os.path.exists(library_manager.database_path): library_manager.load_library()
        library_manager.add_song_to_library(seed_abs)
        seed_fp = library_manager.library_data[seed_abs]['fingerprint'].tolist()
        recommendations = library_manager.find_similar_songs(seed_abs, top_n=top_n)
        results = []
        for file_path, score, meta in recommendations:
            rel_path = os.path.relpath(file_path, udir).replace("\\", "/")
            track_fp = library_manager.library_data[file_path]['fingerprint'].tolist()
            results.append({"filename": meta['filename'], "path": rel_path, "score": round(score * 100, 1), "fingerprint": track_fp})
        return jsonify({"success": True, "results": results, "seed_fingerprint": seed_fp})
    except Exception as e: return jsonify({"success": False, "error": str(e)})

@app.route("/api/acoustic_mix", methods=["POST"])
def api_acoustic():
    mix_path, err = generate_acoustic_mix(os.path.normpath(os.path.join(get_user_dir(), request.json.get('paths', [])[0].replace("..", "").replace("/", os.sep))), get_user_dir())
    return jsonify({"success": True, "filename": f"Mixes/{os.path.basename(mix_path)}"}) if mix_path else jsonify({"success": False, "error": err})

@app.route("/api/graph_data")
def api_graph_data():
    if not CURRENT_USER_ID: return jsonify({"nodes": [], "edges": []})
    g_type = request.args.get('type', 'genre')
    db = load_user_markov(CURRENT_USER_ID, is_artist=(g_type == 'artist'))
    node_color = "#ff3366" if g_type == 'artist' else "#00E5FF"
    
    nodes, edges = set(), []
    for src_node, targets in db.items():
        #  ИСПРАВЛЕНИЕ ДЛЯ ОТОБРАЖЕНИЯ СТАРТОВОГО УЗЛА 
        clean_src = "ROOT" if src_node in ("start", "ROOT") else (src_node.split("|")[-1] if "|" in src_node else src_node)
        nodes.add(clean_src)
        
        for tgt, w in targets.items():
            if tgt in ("start", "ROOT"): continue
            nodes.add(tgt)
            if w >= 1: edges.append({"from": clean_src, "to": tgt, "value": w, "title": f"Weight: {w}"})
            
    node_data = []
    for n in nodes:
        total_w = sum([e['value'] for e in edges if e['from'] == n or e['to'] == n])
        node_data.append({"id": n, "label": n.replace("_", "\n").upper(), "value": 15 + (total_w * 2.5), "color": {"background": node_color if total_w > 5 or n == "ROOT" else "#222"}})
    return jsonify({"nodes": node_data, "edges": edges})

@app.route("/api/edit_graph", methods=["POST"])
def api_edit_graph():
    if not CURRENT_USER_ID: return jsonify({"success": False})
    action, db = request.json.get('action'), load_user_markov(CURRENT_USER_ID)
    if action == 'add_link':
        tgt = normalize_genre(request.json.get('to'))
        if not tgt: return jsonify({"success": False, "error": "❌ Жанра не существует"})
        src_genre = request.json.get('from')
        if src_genre == "ROOT": src_genre = "start"
        if src_genre not in db: db[src_genre] = {}
        db[src_genre][tgt] = db[src_genre].get(tgt, 0) + int(request.json.get('weight', 3))
    elif action == 'change_weight':
        node, change = request.json.get('node'), int(request.json.get('change', 0))
        for s in list(db.keys()):
            if node in db[s]: db[s][node] = max(1, db[s][node] + change)
        if node in db:
            for t in list(db[node].keys()): db[node][t] = max(1, db[node][t] + change)
    elif action == 'delete_node':
        node = request.json.get('node')
        if node in db: del db[node]
        for s in list(db.keys()):
            if node in db[s]: del db[s][node]
    elif action == 'add_root':
        node = normalize_genre(request.json.get('node'))
        if not node: return jsonify({"success": False, "error": "❌ Жанра не существует"})
        if node not in db: db[node] = {}
        if "start" not in db: db["start"] = {}
        db["start"][node] = 1
    save_user_markov(CURRENT_USER_ID, db)
    return jsonify({"success": True})

@app.route("/api/discovery/next")
def api_disc_next():
    if not CURRENT_USER_ID: return jsonify({"success": False})
    recent_genres, recent_artists = get_recent_history(CURRENT_USER_ID, limit=3)
    prev_genre = recent_genres[-2] if len(recent_genres) > 1 else "start"
    current_genre = recent_genres[-1] if recent_genres else "pop"
    last_artist = recent_artists[-1] if recent_artists else None
    
    query_target, pred_mode = None, "GENRE"
    if last_artist and random.random() < 0.3:
        next_artist = get_next_artist(CURRENT_USER_ID, last_artist)
        if next_artist:
            query_target, pred_mode, next_genre = f"{next_artist} best audio", f"ARTIST MATRIX", "mixed"

    if not query_target:
        next_genre = get_next_user_genre(CURRENT_USER_ID, prev_genre, current_genre, recent_genres=recent_genres)
        query_target = get_track_by_genre(next_genre)
        pred_mode = "GENRE MATRIX"
    
    results = search_tracks_on_youtube(query_target, 1)
    if not results: return jsonify({"success": False})
    
    success, path = download_track_by_url(results[0]['url'], get_user_dir())
    if not success: return jsonify({"success": False})
    
    discovery_sessions[CURRENT_USER_ID] = {
        'filepath': path, 'genre': next_genre, 'query': query_target, 
        'last_genre': current_genre, 'prev_genre': prev_genre,
        'last_artist': last_artist, 'new_artist': extract_artist(results[0]['title'])
    }
    return jsonify({"success": True, "filename": os.path.basename(path), "title": results[0]['title'], "genre": f"{next_genre.upper()} | {pred_mode}"})

@app.route("/api/discovery/action", methods=["POST"])
def api_disc_action():
    action, filename = request.json.get('action'), request.json.get('filename')
    sess = discovery_sessions.get(CURRENT_USER_ID)
    if action == 'like' and sess:
        record_user_transition(CURRENT_USER_ID, sess['prev_genre'], sess['last_genre'], sess['genre'])
        record_artist_transition(CURRENT_USER_ID, sess['last_artist'], sess['new_artist'])
        add_user_preference(CURRENT_USER_ID, sess['query'], genre=sess['genre'])
    elif action == 'skip':
        p = os.path.join(get_user_dir(), filename)
        if os.path.exists(p): os.remove(p)
    return jsonify({"success": True})

@app.route("/api/concert", methods=["POST"])
def api_concert():
    udir = get_user_dir()
    tracks = [t.strip() for t in request.json.get('tracks', '').split(",") if t.strip()][:3]
    if len(tracks) < 2: return jsonify({"success": False, "error": "Минимум 2 трека"})
    downloaded = []
    for t in tracks:
        results = search_tracks_on_youtube(f"{t} live performance", 1)
        if results:
            succ, path = download_track_by_url(results[0]['url'], udir)
            if succ: downloaded.append(path)
    if len(downloaded) < 2: return jsonify({"success": False, "error": "Не удалось найти живые записи"})
    out_name = f"concert_mix_{int(time.time())}.mp3"
    out_path = os.path.join(udir, "Mixes", out_name)
    succ, result = create_continuous_mix(downloaded, out_path)
    if succ: return jsonify({"success": True, "filename": f"Mixes/{out_name}"})
    return jsonify({"success": False, "error": str(result)})

@app.route("/api/autodj", methods=["POST"])
def api_autodj():
    if not CURRENT_USER_ID: return jsonify({"success": False})
    udir, effect = get_user_dir(), request.json.get('effect', 'normal')
    last_query = get_user_preferences(CURRENT_USER_ID)
    if not last_query: return jsonify({"success": False, "error": "Библиотека пуста"})
    prev_genre, current_genre = "start", get_track_genre(last_query) or "pop"
    downloaded = []
    for _ in range(3):
        next_genre = get_next_user_genre(CURRENT_USER_ID, prev_genre, current_genre)
        results = search_tracks_on_youtube(get_track_by_genre(next_genre), 1)
        if results:
            succ, path = download_track_by_url(results[0]['url'], udir)
            if succ: downloaded.append(path)
        prev_genre, current_genre = current_genre, next_genre
    if len(downloaded) < 2: return jsonify({"success": False, "error": "Ошибка скачивания"})
    out_name = f"auto_mix_{int(time.time())}.mp3"
    out_path = os.path.join(udir, "Mixes", out_name)
    succ, result = create_continuous_mix(downloaded, out_path)
    if succ:
        apply_audio_effect(result, effect)
        return jsonify({"success": True, "filename": f"Mixes/{out_name}"})
    return jsonify({"success": False, "error": str(result)})

if __name__ == "__main__":
    try: import webview
    except ImportError: pass
    
    print("==================================================")
    print("🚀 PRO STUDIO BOOTED")
    print("==================================================")
    
    t = threading.Thread(target=lambda: app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False))
    t.daemon = True
    t.start()
    
    if 'webview' in sys.modules: 
        webview.create_window("MixingBear Studio PRO", "http://127.0.0.1:5000", width=1280, height=800, background_color='#0a0a0a')
        webview.start()
    else: 
        print("Откройте браузер по адресу: http://127.0.0.1:5000")
        while True: time.sleep(1)