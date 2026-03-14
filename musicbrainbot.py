import asyncio
import os
import json
import html
import time
import shutil
import random
import urllib.request
import urllib.parse
import re
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ChatAction
from aiogram.client.session.aiohttp import AiohttpSession
from pydub import AudioSegment 

# --- НАШИ КАСТОМНЫЕ МОДУЛИ ---
from src.youtube_parser import search_tracks_on_youtube, download_track_by_url, download_multiple_tracks
from src.user_db import add_user_preference, get_user_preferences, get_recent_history, extract_artist, load_user_db
from src.lastfm_api import get_track_genre, get_track_by_genre
from src.markov_db import record_user_transition, get_next_user_genre, load_user_markov, save_user_markov, record_artist_transition, get_next_artist
from src.playlist_generator import PlaylistGenerator
from src.audio_processor import AudioProcessor
from src.library_manager import LibraryManager

#  ПОДКЛЮЧАЕМ НАШ НОВЫЙ ИЗОЛИРОВАННЫЙ ТЕЛЕГРАМ-МИКСЕР 
from mixer_tg import (
    create_continuous_mix,
    create_dj_mix,
    create_smart_transition,
    create_mashup,
    create_vocal_battle,
    get_bpm_and_key
)

# 🚨 ВСТАВЬ СВОЙ ТОКЕН СЮДА
BOT_TOKEN = ""
BASE_MUSIC_DIR = "music_library" 

session = AiohttpSession(timeout=300)
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()

# --- СИСТЕМА КЭШИРОВАНИЯ И АНТИ-СПАМА ---
custom_mixes_state = {}
discovery_sessions = {}
LIBRARY_CACHE = {}  
CACHE_TTL = 30      
ACTIVE_TASKS = set()

class Onboarding(StatesGroup):
    waiting_for_genre = State()
    waiting_for_song = State() 

class ConcertMode(StatesGroup):
    waiting_for_tracks = State() 

class DNAEdit(StatesGroup):
    waiting_for_track = State()

class CustomMixEdit(StatesGroup):
    waiting_for_yt_search = State()

# ==========================================
# 🌐 WEB-ПАРСЕР TUNEBAT (С УМНЫМ ОБХОДОМ И ЖАНРАМИ)
# ==========================================
async def search_tunebat_bpm_key(seed_name, target_bpm, target_key):
    clean_name = re.sub(r'\(.*?\)|\[.*?\]|\.mp3|\.wav', '', seed_name).strip()
    artist_hint = clean_name.split('-')[0].strip() if '-' in clean_name else clean_name
    
    found_tracks = []
    try:
        url = f"https://musicstax.com/search?q={urllib.parse.quote(clean_name)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})
        html_code = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=3).read().decode('utf-8'))
        
        tracks = re.findall(r'class="track-name".*?>(.*?)<', html_code, re.I | re.S)
        artists = re.findall(r'class="artist-name".*?>(.*?)<', html_code, re.I | re.S)
        
        for i in range(min(len(tracks), 3)):
            t_name = tracks[i].strip()
            a_name = artists[i].strip() if i < len(artists) else ""
            if t_name and t_name.lower() not in clean_name.lower():
                found_tracks.append(f"{a_name} - {t_name}")
    except Exception: pass 
        
    if len(found_tracks) < 2:
        genre = await asyncio.to_thread(get_track_genre, clean_name)
        genre_str = genre.replace('_', ' ') if genre else "pop"
        target_bpm_int = int(target_bpm)
        found_tracks = [
            f"{artist_hint} popular track audio",
            f"top {genre_str} hit song {target_bpm_int} bpm audio"
        ]
    return found_tracks[:2]

# ==========================================
#  ИНТЕРФЕЙС, ПРОГРЕСС-БАР И АНТИ-КРАШ 
# ==========================================
async def safe_answer(callback: CallbackQuery, text: str = None, show_alert: bool = False):
    """🛡 Защищает бота от краша 'query is too old' при долгих загрузках"""
    try:
        if text: await callback.answer(text, show_alert=show_alert)
        else: await callback.answer()
    except Exception: pass

async def safe_edit(msg: Message, text: str, markup=None) -> Message:
    try:
        if markup: await msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
        else: await msg.edit_text(text, parse_mode="HTML")
        return msg
    except Exception as e:
        if "message is not modified" in str(e).lower(): return msg
        try:
            try: await msg.delete()
            except: pass
            if markup: return await msg.answer(text, reply_markup=markup, parse_mode="HTML")
            else: return await msg.answer(text, parse_mode="HTML")
        except: return msg

async def upload_with_progress(chat_id, file_path, title, wait_msg, keyboard=None):
    uploading = True
    async def animate():
        frames = ["[⬜️⬜️⬜️⬜️⬜️]", "[🟩⬜️⬜️⬜️⬜️]", "[🟩🟩⬜️⬜️⬜️]", "[🟩🟩🟩⬜️⬜️]", "[🟩🟩🟩🟩⬜️]", "[🟩🟩🟩🟩🟩]"]
        i = 0
        while uploading:
            try: await wait_msg.edit_text(f"🚀 <b>Загрузка файла на сервера Telegram...</b>\n{frames[i % len(frames)]}", parse_mode="HTML")
            except: pass
            i += 1
            await asyncio.sleep(1.2)
            
    anim_task = asyncio.create_task(animate())
    try:
        await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        await bot.send_audio(chat_id=chat_id, audio=FSInputFile(file_path), title=title)
        if keyboard: await bot.send_message(chat_id, " Главное меню:", reply_markup=keyboard)
    finally:
        uploading = False
        anim_task.cancel()
        try: await wait_msg.delete()
        except: pass

def get_user_dir(user_id):
    user_dir = os.path.join(BASE_MUSIC_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def is_user_onboarded(user_id): return os.path.exists(os.path.join(get_user_dir(user_id), "profile.json"))

def save_user_profile(user_id, data):
    with open(os.path.join(get_user_dir(user_id), "profile.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def clean_up_mixes(user_dir):
    mix_folder = os.path.join(user_dir, "Mixes")
    os.makedirs(mix_folder, exist_ok=True)
    for f in os.listdir(user_dir):
        if f.endswith(('.mp3', '.wav')) and "mix" in f:
            try: shutil.move(os.path.join(user_dir, f), os.path.join(mix_folder, f))
            except: pass

def quick_add_to_library(user_dir, file_path):
    try:
        db_path = os.path.join(user_dir, 'auto-dj', 'music_library.db')
        lib_mgr = LibraryManager(AudioProcessor(), database_path=db_path)
        if os.path.exists(db_path): lib_mgr.load_library()
        lib_mgr.add_song_to_library(file_path)
    except: pass

async def get_cached_songs(user_id, user_dir):
    now = time.time()
    if user_id in LIBRARY_CACHE:
        cached_time, songs = LIBRARY_CACHE[user_id]
        if now - cached_time < CACHE_TTL: return songs
    def fetch():
        gen = PlaylistGenerator()
        gen.load_library_from_path(user_dir, force_rebuild=False)
        return gen.get_song_list_for_selection()
    songs = await asyncio.to_thread(fetch)
    LIBRARY_CACHE[user_id] = (now, songs)
    return songs

def invalidate_library_cache(user_id):
    if user_id in LIBRARY_CACHE: del LIBRARY_CACHE[user_id]

# ==========================================
# 2. КЛАВИАТУРЫ И ГЛАВНОЕ МЕНЮ
# ==========================================
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌊 Мой поток", callback_data="my_stream"),
         InlineKeyboardButton(text=" Дискавери", callback_data="discovery_menu")],
        [InlineKeyboardButton(text=" Свой микс", callback_data="cmpage_0"),
         InlineKeyboardButton(text="📻 Марков-Диджей", callback_data="auto_dj_settings")],
        [InlineKeyboardButton(text="🏃 Темпо-Микс (BPM Web)", callback_data="bpmpage_0"),
         InlineKeyboardButton(text=" Акустический микс", callback_data="seedpage_0")],
        [InlineKeyboardButton(text="📚 Библиотека", callback_data="libpage_0"),
         InlineKeyboardButton(text="📼 Мои миксы", callback_data="my_mixes_list")],
        [InlineKeyboardButton(text="🎸 Режим Концерта", callback_data="concert_mode_start"),
         InlineKeyboardButton(text="🕸 Нейро-Граф", callback_data="visualize_markov")]
    ])

def get_genre_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎸 Рок", callback_data="genre_rock"), InlineKeyboardButton(text="🎛 Электроника", callback_data="genre_electronic")],
        [InlineKeyboardButton(text="🎤 Хип-Хоп", callback_data="genre_hip-hop"), InlineKeyboardButton(text="🌟 Поп", callback_data="genre_pop")],
        [InlineKeyboardButton(text="🤘 Метал", callback_data="genre_metal"), InlineKeyboardButton(text="🎷 Джаз", callback_data="genre_jazz")]
    ])

def get_autodj_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📻 Обычный кроссфейд", callback_data="autodj_classic")],
        [InlineKeyboardButton(text=" PRO Умный переход (BPM)", callback_data="autodj_smart")],
        [InlineKeyboardButton(text="🐿 Nightcore (1.25x)", callback_data="autodj_nightcore"), InlineKeyboardButton(text="🐌 Slowed (0.85x)", callback_data="autodj_slowed")],
        [InlineKeyboardButton(text="🔙 Назад в меню", callback_data="cancel_action")]
    ])

@dp.callback_query(F.data == "cancel_action")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    await state.clear()
    await safe_edit(callback.message, " <b>Главное меню:</b>\nВыбери режим\n<i>(Или просто отправь мне название песни)</i>:", get_main_menu())

@dp.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛑 Отменено!\n Главное меню:", reply_markup=get_main_menu())

@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if not is_user_onboarded(user_id):
        await message.answer(" Привет! Выбери свой любимый стартовый жанр:", reply_markup=get_genre_keyboard())
        return await state.set_state(Onboarding.waiting_for_genre)
    await message.answer(" Жду команд!\n<i>Можешь написать мне название любой песни для скачивания!</i>", reply_markup=get_main_menu())

# --- ОНБОРДИНГ ---
@dp.callback_query(Onboarding.waiting_for_genre, F.data.startswith("genre_"))
async def process_genre(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    await state.update_data(genre=callback.data.split("_")[1])
    await safe_edit(callback.message, "✅ Стартовый жанр сохранен!\n\nСкинь мне <b>сразу несколько любимых треков или ссылок</b> (до 5 штук через запятую или с новой строки).")
    await state.set_state(Onboarding.waiting_for_song)

@dp.message(Onboarding.waiting_for_song)
async def process_song(message: Message, state: FSMContext):
    raw_tracks = [t.strip() for t in message.text.replace('\n', ',').split(',') if t.strip()][:5]
    if not raw_tracks: return await message.answer("Ничего не нашел.")
    
    user_data = await state.get_data()
    user_id = message.from_user.id
    wait_msg = await message.answer(f"⏳ Начинаю анализ...")
    
    chosen_macro = user_data.get('genre', 'pop')
    previous_genre, current_genre = "start", chosen_macro
    await asyncio.to_thread(record_user_transition, user_id, "start", "start", chosen_macro)
    
    for i, raw_query in enumerate(raw_tracks, 1):
        wait_msg = await safe_edit(wait_msg, f"⏳ Анализирую {i}/{len(raw_tracks)}: <b>{html.escape(raw_query)}</b>...")
        yt_results = await asyncio.to_thread(search_tracks_on_youtube, raw_query, 1)
        clean_query = yt_results[0].get('title', raw_query) if yt_results else raw_query
        clean_query = clean_query.replace("(Official Video)", "").replace("(Official Audio)", "").strip()
        
        next_genre = await asyncio.to_thread(get_track_genre, clean_query) or chosen_macro
        artist = extract_artist(clean_query)
        
        await asyncio.to_thread(record_user_transition, user_id, previous_genre, current_genre, next_genre)
        await asyncio.to_thread(record_artist_transition, user_id, "ROOT", artist)
        await asyncio.to_thread(add_user_preference, user_id, clean_query, genre=next_genre)
        previous_genre, current_genre = current_genre, next_genre
    
    await asyncio.to_thread(save_user_profile, user_id, user_data)
    try: await wait_msg.delete()
    except: pass
    await message.answer("✅ <b>Матрица вкусов заряжена!</b>", reply_markup=get_main_menu(), parse_mode="HTML")
    await state.clear()

# ==========================================
# 🔍 ГЛОБАЛЬНЫЙ ПОИСК И ВЫБОР ТРЕКОВ
# ==========================================
@dp.message(StateFilter(None), F.text & ~F.text.startswith('/'))
async def process_search_request(message: Message, state: FSMContext):
    query = message.text.strip()
    user_id = message.from_user.id
    
    if user_id in ACTIVE_TASKS:
        return await message.answer("⏳ Диджей занят, подожди окончания текущей операции!")
        
    await bot.send_chat_action(chat_id=message.chat.id, action=ChatAction.TYPING)
    
    if query.startswith("http://") or query.startswith("https://"):
        wait_msg = await message.answer("🔗 <b>Вижу ссылку!</b> Извлекаю аудио...", parse_mode="HTML")
        ACTIVE_TASKS.add(user_id)
        try:
            results = await asyncio.to_thread(search_tracks_on_youtube, query, 1)
            title = results[0]['title'] if results else "Unknown Track"
            user_dir = get_user_dir(user_id)
            success, result_path = await asyncio.to_thread(download_track_by_url, query, user_dir)
            
            if success:
                await asyncio.to_thread(quick_add_to_library, user_dir, result_path)
                invalidate_library_cache(user_id)
                await upload_with_progress(message.chat.id, result_path, title, wait_msg, get_main_menu())
            else:
                await safe_edit(wait_msg, f"❌ Ошибка скачивания: {result_path}")
        finally:
            ACTIVE_TASKS.discard(user_id)
        return

    wait_msg = await message.answer(f"🔎 Ищу: <b>{html.escape(query)}</b>...", parse_mode="HTML")
    results = await asyncio.to_thread(search_tracks_on_youtube, query, 5) 
    
    if not results:
        return await safe_edit(wait_msg, "❌ По твоему запросу ничего не найдено на YouTube.")

    await state.update_data(search_results=results, query=query)
    
    keyboard = []
    for i, res in enumerate(results):
        title = res.get('title', 'Неизвестно')
        duration = res.get('duration', 0)
        mins, secs = divmod(int(duration), 60)
        btn_text = f"🎵 {title[:30]}... ({mins}:{secs:02d})" if len(title) > 30 else f"🎵 {title} ({mins}:{secs:02d})"
        keyboard.append([InlineKeyboardButton(text=btn_text, callback_data=f"dl_{i}")])
        
    keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_action")])
    await safe_edit(wait_msg, f"🔎 Результаты по запросу: <b>{html.escape(query)}</b>\nВыбери нужный трек:", InlineKeyboardMarkup(inline_keyboard=keyboard))

@dp.callback_query(F.data.startswith("dl_"))
async def process_download_choice(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in ACTIVE_TASKS:
        return await safe_answer(callback, "⏳ Подожди, идет другая загрузка!", show_alert=True)
        
    index = int(callback.data.split("_")[1])
    data = await state.get_data()
    results = data.get("search_results", [])
    
    if not results or index >= len(results):
        await safe_answer(callback, "❌ Результаты устарели. Поищи заново.", show_alert=True)
        return await state.clear()
        
    chosen_track = results[index]
    url = chosen_track.get('url')
    title = chosen_track.get('title')
    user_dir = get_user_dir(user_id)
    
    ACTIVE_TASKS.add(user_id)
    await safe_answer(callback, "⏳ Начинаю загрузку...")
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except: pass
    wait_msg = await callback.message.answer(f"⬇️ Качаю: <b>{html.escape(title)}</b>...", parse_mode="HTML")
    
    try:
        success, result_path = await asyncio.to_thread(download_track_by_url, url, user_dir)
        
        if success:
            await asyncio.to_thread(quick_add_to_library, user_dir, result_path)
            invalidate_library_cache(user_id)
            
            last_query = await asyncio.to_thread(get_user_preferences, user_id)
            genre = await asyncio.to_thread(get_track_genre, title) or "pop"
            artist = extract_artist(title)
            
            if last_query:
                last_genre = await asyncio.to_thread(get_track_genre, last_query) or "pop"
                _, recent_artists = await asyncio.to_thread(get_recent_history, user_id, 2)
                last_artist = recent_artists[-1] if recent_artists else "ROOT"
                await asyncio.to_thread(record_user_transition, user_id, "start", last_genre, genre)
                await asyncio.to_thread(record_artist_transition, user_id, last_artist, artist)
                
            await asyncio.to_thread(add_user_preference, user_id, title, genre=genre)
            await upload_with_progress(callback.message.chat.id, result_path, title, wait_msg, get_main_menu())
        else:
            await safe_edit(wait_msg, f"❌ Ошибка скачивания.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    finally:
        ACTIVE_TASKS.discard(user_id)
        await state.clear()

# ==========================================
# 🕸 НЕЙРО-ГРАФ
# ==========================================
def get_text_dna(user_id, is_artist=False):
    user_dir = get_user_dir(user_id)
    filename = "artist_markov.json" if is_artist else "markov.json"
    markov_path = os.path.join(user_dir, filename)
    if not os.path.exists(markov_path): return None
    try:
        with open(markov_path, "r", encoding="utf-8") as f: data = json.load(f)
    except: return None

    weights = {}
    for source, transitions in data.items():
        source_clean = source.split("|")[-1] if "|" in source else source
        if source_clean not in ["start", "ROOT"]:
            weights[source_clean] = weights.get(source_clean, 0) + sum(transitions.values())
        for target, weight in transitions.items():
            if target not in ["start", "ROOT"]:
                weights[target] = weights.get(target, 0) + weight

    if not weights: return None
    total_weight = sum(weights.values())
    if total_weight == 0: return None

    sorted_items = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:10]
    title = "🎤 ТВОЙ ТОП ИСПОЛНИТЕЛЕЙ" if is_artist else "🕸 ТВОЯ МУЗЫКАЛЬНАЯ ДНК (ЖАНРЫ)"
    text = f"<b>{title}</b>\n\n"
    for item, weight in sorted_items:
        percent = int((weight / total_weight) * 100)
        bars = "▓" * (percent // 10) + "░" * (10 - (percent // 10))
        display_name = item if is_artist else item.replace('_', ' ').title()
        text += f"▪️ <b>{display_name}</b>\n   <code>{bars} {percent:02d}%</code>\n\n"
    return text, sorted_items

@dp.callback_query(F.data == "visualize_markov")
async def process_visualize_markov(callback: CallbackQuery):
    await render_dna_view(callback, view_type="genres")

@dp.callback_query(F.data.startswith("dna_view_"))
async def switch_dna_view(callback: CallbackQuery):
    view_type = callback.data.split("_")[2]
    await render_dna_view(callback, view_type)

async def render_dna_view(callback: CallbackQuery, view_type: str):
    await safe_answer(callback)
    user_id = callback.from_user.id
    is_artist = (view_type == "artists")
    result = await asyncio.to_thread(get_text_dna, user_id, is_artist=is_artist)
    
    prefix = "nerfart" if is_artist else "nerfgen"
    title = "🎤 Матрица Исполнителей" if is_artist else "🧬 Матрица Жанров"
    switch_btn = InlineKeyboardButton(text="🧬 Показать Жанры" if is_artist else "🎤 Показать Исполнителей", callback_data="dna_view_genres" if is_artist else "dna_view_artists")

    if not result:
        kb = InlineKeyboardMarkup(inline_keyboard=[[switch_btn], [InlineKeyboardButton(text="🎵 Добавить трек вручную", callback_data="dna_add_track")], [InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]])
        return await safe_edit(callback.message, f"❌ <b>{title} пуста.</b>\nДобавь пару треков, чтобы ИИ начал изучать твои вкусы!", kb)

    dna_text, sorted_items = result
    kb_list = [[switch_btn], [InlineKeyboardButton(text="🎵 Добавить трек вручную", callback_data="dna_add_track")]]
    for item, _ in sorted_items[:4]: 
        display_name = item if is_artist else item.replace('_', ' ').title()
        kb_list.append([InlineKeyboardButton(text=f"📉 Ослабить: {display_name[:20]}", callback_data=f"{prefix}_{item[:30]}")])
    kb_list.append([InlineKeyboardButton(text="🔙 В главное меню", callback_data="cancel_action")])
    await safe_edit(callback.message, dna_text, InlineKeyboardMarkup(inline_keyboard=kb_list))

@dp.callback_query(F.data.startswith("nerfgen_"))
async def perform_nerf_genre(callback: CallbackQuery):
    genre_to_nerf = callback.data.split("nerfgen_")[1]
    user_id = callback.from_user.id
    db = await asyncio.to_thread(load_user_markov, user_id, is_artist=False)
    for src in list(db.keys()):
        if genre_to_nerf in db[src]: db[src][genre_to_nerf] = max(1, db[src][genre_to_nerf] // 2)
    if genre_to_nerf in db:
        for tgt in db[genre_to_nerf]: db[genre_to_nerf][tgt] = max(1, db[genre_to_nerf][tgt] // 2)
    await asyncio.to_thread(save_user_markov, user_id, db, is_artist=False)
    await safe_answer(callback, f"📉 Влияние жанра уменьшено!", show_alert=True)
    await render_dna_view(callback, "genres")

@dp.callback_query(F.data.startswith("nerfart_"))
async def perform_nerf_artist(callback: CallbackQuery):
    artist_to_nerf = callback.data.split("nerfart_")[1]
    user_id = callback.from_user.id
    db = await asyncio.to_thread(load_user_markov, user_id, is_artist=True)
    for src in list(db.keys()):
        if artist_to_nerf in db[src]: db[src][artist_to_nerf] = max(1, db[src][artist_to_nerf] // 2)
    if artist_to_nerf in db:
        for tgt in db[artist_to_nerf]: db[artist_to_nerf][tgt] = max(1, db[artist_to_nerf][tgt] // 2)
    await asyncio.to_thread(save_user_markov, user_id, db, is_artist=True)
    await safe_answer(callback, f"📉 Влияние артиста уменьшено!", show_alert=True)
    await render_dna_view(callback, "artists")

@dp.callback_query(F.data == "dna_add_track")
async def dna_add_track_start(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]])
    await safe_edit(callback.message, "🎵 <b>Скинь мне трек или ссылку:</b>\n<i>(Я проанализирую его и волью этот жанр/артиста в твою базу)</i>", kb)
    await state.set_state(DNAEdit.waiting_for_track)

@dp.message(DNAEdit.waiting_for_track)
async def process_dna_add_track(message: Message, state: FSMContext):
    query = message.text.strip()
    user_id = message.from_user.id
    wait_msg = await message.answer("🧬 Анализирую ДНК трека...")
    
    results = await asyncio.to_thread(search_tracks_on_youtube, query, 1)
    if not results: return await safe_edit(wait_msg, "❌ Не найдено. Попробуй другой запрос.")
    
    title = results[0]['title']
    clean_title = title.replace("(Official Video)", "").replace("(Official Audio)", "").strip()
    genre = await asyncio.to_thread(get_track_genre, clean_title) or "pop"
    artist = extract_artist(clean_title)
    
    recent_genres, recent_artists = await asyncio.to_thread(get_recent_history, user_id, 2)
    prev_genre = recent_genres[-1] if recent_genres else "start"
    last_artist = recent_artists[-1] if recent_artists else "ROOT"
    
    await asyncio.to_thread(record_user_transition, user_id, "start", prev_genre, genre)
    await asyncio.to_thread(record_artist_transition, user_id, last_artist, artist)
    await asyncio.to_thread(add_user_preference, user_id, title, genre)
    
    await wait_msg.delete()
    await message.answer(f"✅ Успешно! Жанр <b>{genre.upper()}</b> и артист <b>{artist}</b> влиты в матрицу.\n Главное меню:", parse_mode="HTML", reply_markup=get_main_menu())
    await state.clear()


# ==========================================
# 🌊 МОЙ ПОТОК
# ==========================================
@dp.callback_query(F.data == "my_stream")
async def process_my_stream(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in ACTIVE_TASKS: return await safe_answer(callback, "⏳ Подожди окончания текущего процесса...", show_alert=True)
        
    ACTIVE_TASKS.add(user_id)
    await safe_answer(callback, "🌊 Ловлю волну...")
    
    try:
        user_dir = get_user_dir(user_id)
        last_query = await asyncio.to_thread(get_user_preferences, user_id)
        current_genre = await asyncio.to_thread(get_track_genre, last_query) if last_query else "pop"
        recent_genres, recent_artists = await asyncio.to_thread(get_recent_history, user_id, 3)
        
        last_artist = recent_artists[-1] if recent_artists else None
        query_target, pred_mode = None, "Жанры"
        
        if last_artist and random.random() < 0.3:
            next_artist = await asyncio.to_thread(get_next_artist, user_id, last_artist)
            if next_artist: query_target, next_genre, pred_mode = f"{next_artist} best audio", "mixed", f"Артисты ({last_artist} ➡️ {next_artist})"

        if not query_target:
            next_genre = await asyncio.to_thread(get_next_user_genre, user_id, "start", current_genre, recent_genres)
            query_target = await asyncio.to_thread(get_track_by_genre, next_genre)
        
        wait_msg = await callback.message.answer(f"🌊 Поток ({pred_mode}): <b>{html.escape(query_target)}</b>\n🔍 Ищу...", parse_mode="HTML")
        
        results = await asyncio.to_thread(search_tracks_on_youtube, query_target, 1)
        if results:
            wait_msg = await safe_edit(wait_msg, f"⬇️ Качаю трек...")
            success, path = await asyncio.to_thread(download_track_by_url, results[0]['url'], user_dir)
            if success:
                await asyncio.to_thread(quick_add_to_library, user_dir, path)
                invalidate_library_cache(user_id)
                await asyncio.to_thread(add_user_preference, user_id, query_target, genre=next_genre)
                if last_artist: await asyncio.to_thread(record_artist_transition, user_id, last_artist, extract_artist(results[0]['title']))
                
                await upload_with_progress(callback.message.chat.id, path, f"Твой поток: {next_genre.upper()}", wait_msg, get_main_menu())
                return
                
        await safe_edit(wait_msg, "❌ Ошибка загрузки потока.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    finally: ACTIVE_TASKS.discard(user_id)

# ==========================================
#  СВОЙ МИКС
# ==========================================
async def render_custom_mix_keyboard(user_id, user_dir, page=0):
    songs = await get_cached_songs(user_id, user_dir)
    if not songs: return None
    
    selected_indices = custom_mixes_state.get(user_id, [])
    ITEMS_PER_PAGE = 10
    total_pages = max(1, (len(songs) - 1) // ITEMS_PER_PAGE + 1)
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    
    keyboard = []
    for i, f, _ in songs[start:end]:
        mark = '✅ ' if i in selected_indices else '🎵 '
        keyboard.append([InlineKeyboardButton(text=f"{mark}{f[:30]}...", callback_data=f"toggle_{i}_{page}")])
    
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"cmpage_{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1: nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"cmpage_{page+1}"))
    if len(nav_row) > 1: keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(text="🔍 С YouTube", callback_data="cmix_yt_search"), InlineKeyboardButton(text="✨ ИИ-совет", callback_data="cmix_ai_rec")])
    if len(selected_indices) >= 2: keyboard.append([InlineKeyboardButton(text="▶️ СВЕСТИ ТРЕКИ", callback_data="choose_mix_type")])
    keyboard.append([InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@dp.callback_query(F.data.startswith("cmpage_"))
async def process_custom_mix_page(callback: CallbackQuery):
    await safe_answer(callback)
    page = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    if user_id not in custom_mixes_state: custom_mixes_state[user_id] = []
    kb = await render_custom_mix_keyboard(user_id, get_user_dir(user_id), page)
    if not kb: return await safe_edit(callback.message, "Библиотека пуста!", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    await safe_edit(callback.message, "Выбери треки для микса:", kb)

@dp.callback_query(F.data.startswith("toggle_"))
async def process_toggle_track(callback: CallbackQuery):
    await safe_answer(callback)
    user_id = callback.from_user.id
    parts = callback.data.split("_")
    track_index = int(parts[1])
    page = int(parts[2]) if len(parts) > 2 else 0
    
    if user_id not in custom_mixes_state: custom_mixes_state[user_id] = []
    if track_index in custom_mixes_state[user_id]: custom_mixes_state[user_id].remove(track_index)
    else: custom_mixes_state[user_id].append(track_index)
    
    await safe_edit(callback.message, "Выбери треки для микса:", await render_custom_mix_keyboard(user_id, get_user_dir(user_id), page))

@dp.callback_query(F.data == "cmix_yt_search")
async def cmix_yt_search_start(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Отмена", callback_data="cmpage_0")]])
    await safe_edit(callback.message, "🔍 <b>Напиши название трека ИЛИ кинь ссылку:</b>\n<i>(Можно кинуть плейлист, скачаю всё разом)</i>", kb)
    await state.set_state(CustomMixEdit.waiting_for_yt_search)

@dp.message(CustomMixEdit.waiting_for_yt_search)
async def process_cmix_yt_search_state(message: Message, state: FSMContext):
    query = message.text.strip()
    user_id = message.from_user.id
    user_dir = get_user_dir(user_id)
    wait_msg = await message.answer("🔍 Ищу на серверах...")
    
    results = await asyncio.to_thread(search_tracks_on_youtube, query, 1)
    if not results: return await safe_edit(wait_msg, "❌ Ничего не найдено.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="cmpage_0")]]))
    urls = [r['url'] for r in results]
    
    if len(urls) == 1:
        wait_msg = await safe_edit(wait_msg, f"⬇️ Качаю 1 трек...")
        success, path = await asyncio.to_thread(download_track_by_url, urls[0], user_dir)
        if success:
            await asyncio.to_thread(quick_add_to_library, user_dir, path)
            invalidate_library_cache(user_id)
            songs = await get_cached_songs(user_id, user_dir)
            for i, f, _ in songs:
                if f == os.path.basename(path):
                    if user_id not in custom_mixes_state: custom_mixes_state[user_id] = []
                    if i not in custom_mixes_state[user_id]: custom_mixes_state[user_id].append(i)
                    break
            try: await wait_msg.delete()
            except: pass
            await message.answer(f"✅ Трек добавлен!", reply_markup=await render_custom_mix_keyboard(user_id, user_dir, 0))
        else: await safe_edit(wait_msg, "❌ Ошибка загрузки.")
    else:
        wait_msg = await safe_edit(wait_msg, f"⬇️ Многопоточная загрузка плейлиста ({len(urls)} шт)...")
        dl_res = await asyncio.to_thread(download_multiple_tracks, urls, user_dir, 5)
        success_paths = [r['result'] for r in dl_res if r['success']]
        
        wait_msg = await safe_edit(wait_msg, f"⚡ Индексирую {len(success_paths)} треков в базу...")
        for p in success_paths: await asyncio.to_thread(quick_add_to_library, user_dir, p)
        invalidate_library_cache(user_id)
            
        try: await wait_msg.delete()
        except: pass
        await message.answer(f"✅ Плейлист скачан ({len(success_paths)} треков)!", reply_markup=await render_custom_mix_keyboard(user_id, user_dir, 0))
    await state.clear()

@dp.callback_query(F.data == "choose_mix_type")
async def choose_mix_type(callback: CallbackQuery):
    user_id = callback.from_user.id
    selected = custom_mixes_state.get(user_id, [])
    if len(selected) < 2: return await safe_answer(callback, "Нужно минимум 2 трека!", show_alert=True)
    
    kb = []
    if len(selected) == 2:
        kb.append([InlineKeyboardButton(text=" Умный бит-матч (Smart)", callback_data="domix_smart")])
        kb.append([InlineKeyboardButton(text="🎤 Мэшап (Вокал+Бит)", callback_data="domix_mashup")])
        kb.append([InlineKeyboardButton(text="⚔️ Вокальный Баттл", callback_data="domix_battle")])
    kb.append([InlineKeyboardButton(text="🎛 Классический кроссфейд", callback_data="domix_classic")])
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="cmpage_0")])
    
    await safe_edit(callback.message, "🎛 <b>Выбери технологию сведения:</b>", InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("domix_"))
async def perform_mixing(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in ACTIVE_TASKS: return await safe_answer(callback, "⏳ Я уже свожу треки! Подожди.", show_alert=True)
        
    ACTIVE_TASKS.add(user_id)
    mix_type = callback.data.split("_")[1]
    user_dir = get_user_dir(user_id)
    selected = custom_mixes_state.get(user_id, [])
    
    await safe_answer(callback, " Рендеринг запущен...")
    try: await callback.message.edit_reply_markup(reply_markup=None) 
    except: pass
    wait_msg = await callback.message.answer(f" Студия сводит ({mix_type.upper()}). Это займет время...")
    
    try:
        await asyncio.to_thread(clean_up_mixes, user_dir)
        songs = await get_cached_songs(user_id, user_dir)
        files = [os.path.normpath(os.path.join(user_dir, m['filename'])) for i, f, m in songs if i in selected]
        out_name = os.path.join(user_dir, "Mixes", f"{mix_type}_mix_{int(time.time())}.mp3")
        
        success = False
        if mix_type == "smart":
            try: success, mix_path = await asyncio.to_thread(create_smart_transition, files[0], files[1], out_name)
            except NameError: success, mix_path = await asyncio.to_thread(create_continuous_mix, files, out_name)
        elif mix_type == "mashup":
            try: success, mix_path = await asyncio.to_thread(create_mashup, files[0], files[1], out_name, user_dir)
            except NameError: success, mix_path = await asyncio.to_thread(create_continuous_mix, files, out_name)
        elif mix_type == "battle":
            try: success, mix_path = await asyncio.to_thread(create_vocal_battle, files[0], files[1], out_name, user_dir)
            except NameError: success, mix_path = await asyncio.to_thread(create_continuous_mix, files, out_name)
        else: 
            success, mix_path = await asyncio.to_thread(create_continuous_mix, files, out_name)
        
        if success:
            custom_mixes_state[user_id] = [] 
            await upload_with_progress(callback.message.chat.id, mix_path, f"{mix_type.title()} Studio Mix", wait_msg, get_main_menu())
        else: await safe_edit(wait_msg, f"❌ Ошибка рендера.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    except Exception as e: await safe_edit(wait_msg, f"❌ Ошибка: {e}", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    finally: ACTIVE_TASKS.discard(user_id)


# ==========================================
# 📻 МАРКОВ-ДИДЖЕЙ
# ==========================================
@dp.callback_query(F.data == "auto_dj_settings")
async def process_auto_dj_settings(callback: CallbackQuery):
    await safe_answer(callback)
    await safe_edit(callback.message, "🎛 <b>Выбери режим работы Авто-Диджея:</b>", get_autodj_keyboard())

@dp.callback_query(F.data.startswith("autodj_"))
async def process_auto_dj(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in ACTIVE_TASKS: return await safe_answer(callback, "⏳ Диджей уже работает над твоим сетом!", show_alert=True)
        
    ACTIVE_TASKS.add(user_id)
    await safe_answer(callback, "📻 Начинаю работу...")
    try: await callback.message.edit_reply_markup(reply_markup=None) 
    except: pass
    
    try:
        user_dir = get_user_dir(user_id)
        effect = callback.data.split("_")[1] 
        
        last_query = await asyncio.to_thread(get_user_preferences, user_id)
        if not last_query: 
            ACTIVE_TASKS.discard(user_id)
            return await callback.message.answer("❌ Библиотека пуста. Послушай пару треков!", reply_markup=get_main_menu())
            
        status_msg = await callback.message.answer("📻 <b>ИИ-Диджей:</b> Анализирую историю...", parse_mode="HTML")
        
        prev_genre = "start"
        current_genre = await asyncio.to_thread(get_track_genre, last_query)
        recent_genres, recent_artists = await asyncio.to_thread(get_recent_history, user_id, 3)
        last_artist = recent_artists[-1] if recent_artists else None
        
        queries = []
        for i in range(3):
            query_target = None
            if last_artist and random.random() < 0.3:
                next_artist = await asyncio.to_thread(get_next_artist, user_id, last_artist)
                if next_artist: query_target, next_genre = f"{next_artist} best audio", "mixed"
            if not query_target:
                next_genre = await asyncio.to_thread(get_next_user_genre, user_id, prev_genre, current_genre, recent_genres)
                query_target = await asyncio.to_thread(get_track_by_genre, next_genre)
            queries.append(query_target)
            prev_genre, current_genre = current_genre, next_genre
            recent_genres.append(next_genre)
            if len(recent_genres) > 3: recent_genres.pop(0)

        status_msg = await safe_edit(status_msg, f"📻 <b>ИИ-Диджей подобрал сет:</b>\n" + "\n".join([f"🔹 {q}" for q in queries]))
        
        async def search(q):
            res = await asyncio.to_thread(search_tracks_on_youtube, q, 1)
            return res[0]['url'] if res else None
            
        urls = await asyncio.gather(*(search(q) for q in queries))
        valid_urls = [u for u in urls if u]
        if len(valid_urls) < 2: return await safe_edit(status_msg, "❌ Не удалось найти треки.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))

        status_msg = await safe_edit(status_msg, f"⬇️ <b>ИИ-Диджей:</b> Многопоточная загрузка ({len(valid_urls)} шт)...")
        dl_results = await asyncio.to_thread(download_multiple_tracks, valid_urls, user_dir, 5)
        downloaded_files = [r['result'] for r in dl_results if r['success']]
        if len(downloaded_files) < 2: return await safe_edit(status_msg, "❌ Ошибка при скачивании.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
        
        status_msg = await safe_edit(status_msg, "⚡ <b>ИИ-Диджей:</b> Индексирую акустику...")
        for path in downloaded_files: await asyncio.to_thread(quick_add_to_library, user_dir, path)
        invalidate_library_cache(user_id)

        await asyncio.to_thread(clean_up_mixes, user_dir)
        out_name = os.path.join(user_dir, "Mixes", f"auto_mix_{int(time.time())}.mp3")
        
        if effect == "smart":
            status_msg = await safe_edit(status_msg, f" <b>PRO-Диджей:</b> Свожу с анализом BPM и тональности...")
            try: mix_success, mix_result = await asyncio.to_thread(create_dj_mix, downloaded_files, out_name)
            except NameError: mix_success, mix_result = await asyncio.to_thread(create_continuous_mix, downloaded_files, out_name)
        else:
            status_msg = await safe_edit(status_msg, f" <b>ИИ-Диджей:</b> Свожу микс (Режим: {effect.upper()})...")
            mix_success, mix_result = await asyncio.to_thread(create_continuous_mix, downloaded_files, out_name)
            if mix_success and effect in ["nightcore", "slowed"]:
                status_msg = await safe_edit(status_msg, f" <b>ИИ-Диджей:</b> Применяю аудио-эффект {effect.upper()}...")
                await asyncio.to_thread(apply_audio_effect, mix_result, effect)

        if mix_success:
            await upload_with_progress(callback.message.chat.id, mix_result, "Auto DJ Mix", status_msg, get_main_menu())
        else: await safe_edit(status_msg, f"❌ Ошибка сведения.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    except Exception as e: await safe_edit(status_msg, f"❌ Ошибка: {e}", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    finally: ACTIVE_TASKS.discard(user_id)

# ==========================================
# 🎸 РЕЖИМ КОНЦЕРТА 
# ==========================================
@dp.callback_query(F.data == "concert_mode_start")
async def concert_mode_start(callback: CallbackQuery, state: FSMContext):
    await safe_answer(callback)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🎲 Шафл по вкусу (ИИ)", callback_data="concert_shuffle")], [InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]])
    await safe_edit(callback.message, "🎸 <b>Режим Концерта</b>\nНапиши названия песен через запятую:", kb)
    await state.set_state(ConcertMode.waiting_for_tracks)

@dp.message(ConcertMode.waiting_for_tracks)
async def process_concert_tracks(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id in ACTIVE_TASKS: return await message.answer("⏳ Я уже свожу концерт!")
    
    tracks = [t.strip() for t in message.text.split(",") if t.strip()][:5]
    if len(tracks) < 2: return await message.answer("❌ Напиши минимум 2 трека!")
    await state.clear()
    
    ACTIVE_TASKS.add(user_id)
    try:
        status_msg = await message.answer(f"🎸 Ищу {len(tracks)} концертных записей на YouTube...")
        user_dir = get_user_dir(user_id)
        
        async def search(t):
            res = await asyncio.to_thread(search_tracks_on_youtube, f"{t} live performance", 1)
            return res[0]['url'] if res else None
            
        urls = await asyncio.gather(*(search(t) for t in tracks))
        valid_urls = [u for u in urls if u]
        if len(valid_urls) < 2: return await safe_edit(status_msg, "❌ Не удалось найти живые записи.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
            
        status_msg = await safe_edit(status_msg, f"⬇️ <b>Лайв-сет:</b> Многопоточная загрузка ({len(valid_urls)} шт)...")
        dl_results = await asyncio.to_thread(download_multiple_tracks, valid_urls, user_dir, 5)
        downloaded = [r['result'] for r in dl_results if r['success']]
        if len(downloaded) < 2: return await safe_edit(status_msg, "❌ Ошибка при скачивании.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
            
        status_msg = await safe_edit(status_msg, " <b>Лайв-сет:</b> Имитирую толпу и свожу треки...")
        await asyncio.to_thread(clean_up_mixes, user_dir)
        out_name = os.path.join(user_dir, "Mixes", f"concert_live_mix_{int(time.time())}.mp3")
        succ, mix_path = await asyncio.to_thread(create_continuous_mix, downloaded, out_name)
        
        if succ:
            await upload_with_progress(message.chat.id, mix_path, "Live Concert Mix", status_msg, get_main_menu())
        else: await safe_edit(status_msg, f"❌ Ошибка сведения.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    finally: ACTIVE_TASKS.discard(user_id)

@dp.callback_query(F.data == "concert_shuffle")
async def process_concert_shuffle(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id in ACTIVE_TASKS: return await safe_answer(callback, "⏳ Процесс уже идет...", show_alert=True)
    await safe_answer(callback, "🎲 ИИ собирает лайв-сет...")
    await state.clear()
    try: await callback.message.edit_reply_markup(reply_markup=None) 
    except: pass

    ACTIVE_TASKS.add(user_id)
    try:
        user_dir = get_user_dir(user_id)
        db_data = await asyncio.to_thread(load_user_db, user_id)
        history = db_data.get("play_history", [])
        if len(history) < 2: return await callback.message.answer("❌ В истории пока мало треков!", reply_markup=get_main_menu())
        
        tracks = random.sample([item["track"] for item in history[-20:]], min(3, len(history)))
        status_msg = await callback.message.answer(f"🎸 Нашел в истории: <b>{', '.join(tracks)}</b>\nИщу живые записи...", parse_mode="HTML")
        
        async def search(t):
            res = await asyncio.to_thread(search_tracks_on_youtube, f"{t} live performance", 1)
            return res[0]['url'] if res else None
            
        urls = await asyncio.gather(*(search(t) for t in tracks))
        valid_urls = [u for u in urls if u]
        if len(valid_urls) < 2: return await safe_edit(status_msg, "❌ Не удалось найти записи.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
            
        status_msg = await safe_edit(status_msg, f"⬇️ Многопоточная загрузка ({len(valid_urls)} шт)...")
        dl_results = await asyncio.to_thread(download_multiple_tracks, valid_urls, user_dir, 5)
        downloaded = [r['result'] for r in dl_results if r['success']]
        
        status_msg = await safe_edit(status_msg, " Свожу лайв-сет...")
        await asyncio.to_thread(clean_up_mixes, user_dir)
        out_name = os.path.join(user_dir, "Mixes", f"concert_live_mix_{int(time.time())}.mp3")
        succ, mix_path = await asyncio.to_thread(create_continuous_mix, downloaded, out_name)
        
        if succ:
            await upload_with_progress(callback.message.chat.id, mix_path, "AI Live Concert Mix", status_msg, get_main_menu())
        else: await safe_edit(status_msg, f"❌ Ошибка сведения.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    finally: ACTIVE_TASKS.discard(user_id)

# ==========================================
# 🏃 ТЕМПО-МИКС (ГИБРИДНЫЙ ПОИСК)
# ==========================================
async def render_bpm_seed_keyboard(user_id, user_dir, page=0):
    songs = await get_cached_songs(user_id, user_dir)
    if not songs: return None
    
    ITEMS_PER_PAGE = 10
    total_pages = max(1, (len(songs) - 1) // ITEMS_PER_PAGE + 1)
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    
    kb = [[InlineKeyboardButton(text=f"🎵 {f[:30]}...", callback_data=f"bpmseed_{i}")] for i, f, _ in songs[start:end]]
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"bpmpage_{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1: nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"bpmpage_{page+1}"))
    
    if len(nav_row) > 1: kb.append(nav_row)
    kb.append([InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.callback_query(F.data.startswith("bpmpage_"))
async def process_bpm_library(callback: CallbackQuery):
    await safe_answer(callback)
    user_id = callback.from_user.id
    user_dir = get_user_dir(user_id)
    page = int(callback.data.split("_")[1])
    kb = await render_bpm_seed_keyboard(user_id, user_dir, page)
    if not kb: return await safe_edit(callback.message, "Библиотека пуста!", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    await safe_edit(callback.message, "🏃 <b>Темпо-Микс</b>\nВыбери стартовый трек. Я найду в интернете похожие по BPM и сведу их:", kb)

@dp.callback_query(F.data.startswith("bpmseed_"))
async def process_bpm_mix(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in ACTIVE_TASKS: return await safe_answer(callback, "⏳ Диджей уже сводит сет!", show_alert=True)
        
    ACTIVE_TASKS.add(user_id)
    await safe_answer(callback, "🏃 Анализирую скорость (BPM)...")
    user_dir = get_user_dir(user_id)
    idx = int(callback.data.split("_")[1])
    
    try: await callback.message.edit_reply_markup(reply_markup=None) 
    except: pass
    wait_msg = await callback.message.answer("⏳ <b>Темпо-Радар:</b> Локальный анализ стартового трека...", parse_mode="HTML")
    
    try:
        songs = await get_cached_songs(user_id, user_dir)
        chosen_filename = next(f for i, f, _ in songs if i == idx)
        seed_path = os.path.join(user_dir, chosen_filename)
        
        seed_bpm, seed_key = await asyncio.to_thread(get_bpm_and_key, seed_path)
        wait_msg = await safe_edit(wait_msg, f"🔍 <b>Темпо-Радар:</b> Стартовый трек {seed_bpm:.1f} BPM.\n📚 Ищу совпадения в твоей библиотеке...")
        
        mix_files = [seed_path]
        
        random.shuffle(songs)
        for _, f, _ in songs:
            if f == chosen_filename: continue
            p = os.path.join(user_dir, f)
            b, _ = await asyncio.to_thread(get_bpm_and_key, p)
            if abs(b - seed_bpm) <= 6: 
                mix_files.append(p)
            if len(mix_files) >= 3: break
            
        if len(mix_files) < 3:
            wait_msg = await safe_edit(wait_msg, f"🌐 Локальных треков не хватило. Подбираю похожие по стилю в Web...")
            web_tracks = await search_tunebat_bpm_key(chosen_filename, seed_bpm, seed_key)
            
            async def search_and_dl(q):
                res = await asyncio.to_thread(search_tracks_on_youtube, q, 1)
                if res:
                    succ, p = await asyncio.to_thread(download_track_by_url, res[0]['url'], user_dir)
                    if succ: return p
                return None
                
            dl_paths = await asyncio.gather(*(search_and_dl(q) for q in web_tracks))
            valid_paths = [p for p in dl_paths if p]
            
            for p in valid_paths: 
                await asyncio.to_thread(quick_add_to_library, user_dir, p)
                mix_files.append(p)
            invalidate_library_cache(user_id)
            
        if len(mix_files) < 2:
            await safe_edit(wait_msg, "❌ Не удалось найти подходящие треки.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
            ACTIVE_TASKS.discard(user_id)
            return
            
        wait_msg = await safe_edit(wait_msg, f" <b>Свожу Темпо-Микс:</b> Идеальное совпадение темпа...")
        out_name = os.path.join(user_dir, "Mixes", f"tempo_mix_{int(time.time())}.mp3")
        
        try: success, final_path = await asyncio.to_thread(create_dj_mix, mix_files[:3], out_name)
        except NameError: success, final_path = await asyncio.to_thread(create_continuous_mix, mix_files[:3], out_name)
        
        if success:
            await upload_with_progress(callback.message.chat.id, final_path, "BPM Sync Mix", wait_msg, get_main_menu())
        else: await safe_edit(wait_msg, f"❌ Ошибка сведения", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    except Exception as e: await safe_edit(wait_msg, f"❌ Ошибка: {e}", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    finally: ACTIVE_TASKS.discard(user_id)


# ==========================================
# ПРОСТЫЕ МЕНЮ (БИБЛИОТЕКА КАК КНОПКИ)
# ==========================================
async def render_library_page(user_id, user_dir, page=0):
    songs = await get_cached_songs(user_id, user_dir)
    if not songs: return "Библиотека пуста.", None
    
    ITEMS_PER_PAGE = 10
    total_pages = max(1, (len(songs) - 1) // ITEMS_PER_PAGE + 1)
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    
    text = f"📚 <b>Твоя коллекция (Стр. {page+1}/{total_pages}):</b>\n<i>👇 Нажми на любой трек, чтобы скачать его</i>"
    
    kb = []
    for i, filename, _ in songs[start:end]: 
        display_name = filename[:35] + "..." if len(filename) > 35 else filename
        kb.append([InlineKeyboardButton(text=f"🎵 {display_name}", callback_data=f"getsong_{i}")])
        
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"libpage_{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1: nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"libpage_{page+1}"))
    
    if len(nav_row) > 1: kb.append(nav_row)
    kb.append([InlineKeyboardButton(text="🔙 Меню", callback_data="cancel_action")])
    return text, InlineKeyboardMarkup(inline_keyboard=kb)

@dp.callback_query(F.data.startswith("libpage_"))
async def process_library_page(callback: CallbackQuery):
    await safe_answer(callback)
    page = int(callback.data.split("_")[1])
    text, kb = await render_library_page(callback.from_user.id, get_user_dir(callback.from_user.id), page)
    await safe_edit(callback.message, text, kb)

@dp.callback_query(F.data.startswith("getsong_"))
async def process_get_song(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in ACTIVE_TASKS: return await safe_answer(callback, "⏳ Идет загрузка, подожди!", show_alert=True)
    
    ACTIVE_TASKS.add(user_id)
    await safe_answer(callback, "⏳ Отправляю трек...") 
    user_dir = get_user_dir(user_id)
    idx = int(callback.data.split("_")[1])
    
    songs = await get_cached_songs(user_id, user_dir)
    try:
        chosen_filename = next(f for i, f, _ in songs if i == idx)
        file_path = os.path.join(user_dir, chosen_filename)
        
        if os.path.exists(file_path):
            wait_msg = await callback.message.answer("⏳ Подготовка к отправке...")
            await upload_with_progress(callback.message.chat.id, file_path, chosen_filename, wait_msg)
        else:
            await callback.message.answer("❌ Файл не найден на диске.")
    except StopIteration: pass
    finally: ACTIVE_TASKS.discard(user_id)

@dp.callback_query(F.data == "my_mixes_list")
async def process_my_mixes_list(callback: CallbackQuery):
    await safe_answer(callback) 
    user_dir = get_user_dir(callback.from_user.id)
    mix_folder = os.path.join(user_dir, "Mixes")
    if not os.path.exists(mix_folder): return await safe_edit(callback.message, "У тебя еще нет миксов!", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    mix_files = [f for f in os.listdir(mix_folder) if f.endswith('.mp3')]
    if not mix_files: return await safe_edit(callback.message, "У тебя еще нет миксов!", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
        
    keyboard = [[InlineKeyboardButton(text=f"📼 {m[:30]}...", callback_data=f"getmix_{m}")] for m in mix_files[:10]]
    keyboard.append([InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")])
    await safe_edit(callback.message, "📼 <b>Твои готовые миксы:</b>", InlineKeyboardMarkup(inline_keyboard=keyboard))

@dp.callback_query(F.data.startswith("getmix_"))
async def process_get_mix(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in ACTIVE_TASKS: return await safe_answer(callback, "⏳ Идет загрузка, подожди!", show_alert=True)
    
    ACTIVE_TASKS.add(user_id)
    await safe_answer(callback, "⏳ Отправляю микс...") 
    mix_name = callback.data.split("getmix_")[1]
    mix_path = os.path.join(get_user_dir(user_id), "Mixes", mix_name)
    if os.path.exists(mix_path):
        wait_msg = await callback.message.answer("⏳ Подготовка к отправке...")
        await upload_with_progress(callback.message.chat.id, mix_path, mix_name, wait_msg)
    ACTIVE_TASKS.discard(user_id)

# ==========================================
#  АКУСТИКА С ПАГИНАЦИЕЙ
# ==========================================
async def render_acoustic_seed_keyboard(user_id, user_dir, page=0):
    songs = await get_cached_songs(user_id, user_dir)
    if not songs: return None
    
    ITEMS_PER_PAGE = 10
    total_pages = max(1, (len(songs) - 1) // ITEMS_PER_PAGE + 1)
    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    
    kb = [[InlineKeyboardButton(text=f"🎵 {f[:30]}...", callback_data=f"seedtr_{i}")] for i, f, _ in songs[start:end]]
    nav_row = []
    if page > 0: nav_row.append(InlineKeyboardButton(text="⬅️", callback_data=f"seedpage_{page-1}"))
    nav_row.append(InlineKeyboardButton(text=f"📄 {page+1}/{total_pages}", callback_data="ignore"))
    if page < total_pages - 1: nav_row.append(InlineKeyboardButton(text="➡️", callback_data=f"seedpage_{page+1}"))
    
    if len(nav_row) > 1: kb.append(nav_row)
    kb.append([InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.callback_query(F.data.startswith("seedpage_"))
async def process_show_library(callback: CallbackQuery):
    await safe_answer(callback) 
    user_id = callback.from_user.id
    user_dir = get_user_dir(user_id)
    page = int(callback.data.split("_")[1])
    kb = await render_acoustic_seed_keyboard(user_id, user_dir, page)
    if not kb: return await safe_edit(callback.message, "Библиотека пуста!", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    await safe_edit(callback.message, "Выбери базовый трек (ИИ найдет похожие по MFCC):", kb)

@dp.callback_query(F.data.startswith("seedtr_"))
async def process_acoustic_mix(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in ACTIVE_TASKS: return await safe_answer(callback, "⏳ Я уже анализирую акустику! Подожди.", show_alert=True)
        
    ACTIVE_TASKS.add(user_id)
    await safe_answer(callback, " MFCC-анализ...")
    user_dir = get_user_dir(user_id)
    idx = int(callback.data.split("_")[1])
    
    try: await callback.message.edit_reply_markup(reply_markup=None) 
    except: pass
    wait_msg = await callback.message.answer("⏳ <b>Акустический Радар:</b> Сканирую библиотеку...", parse_mode="HTML")
    
    try:
        songs = await get_cached_songs(user_id, user_dir)
        chosen_filename = next(f for i, f, _ in songs if i == idx)
        seed_path = os.path.join(user_dir, chosen_filename)
        
        mix_path, msg = await asyncio.to_thread(generate_acoustic_mix, seed_path, user_dir)
        if mix_path:
            await upload_with_progress(callback.message.chat.id, mix_path, "Acoustic AI Mix", wait_msg, get_main_menu())
        else: await safe_edit(wait_msg, "❌ Не удалось собрать акустический микс.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    except Exception as e: await safe_edit(wait_msg, f"❌ Ошибка: {e}", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))
    finally: ACTIVE_TASKS.discard(user_id)

# ==========================================
#  ДИСКАВЕРИ С ВЫБОРОМ ЖАНРА 
# ==========================================
@dp.callback_query(F.data == "discovery_menu")
async def discovery_menu(callback: CallbackQuery):
    await safe_answer(callback)
    result = await asyncio.to_thread(get_text_dna, callback.from_user.id)
    kb = []
    if result:
        _, sorted_genres = result
        for genre, _ in sorted_genres[:6]:
            clean_name = genre.replace('_', ' ').title()
            kb.append([InlineKeyboardButton(text=f"🧬 Начать с: {clean_name}", callback_data=f"disc_gen_{genre}")])
            
    kb.append([InlineKeyboardButton(text="🎲 Довериться ИИ (Случайный путь)", callback_data="disc_gen_auto")])
    kb.append([InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")])
    await safe_edit(callback.message, " <b>Режим Дискавери</b>\n\nС какого жанра начнем исследование твоей ДНК?", InlineKeyboardMarkup(inline_keyboard=kb))

@dp.callback_query(F.data.startswith("disc_gen_"))
async def start_discovery(callback: CallbackQuery):
    genre = callback.data.split("disc_gen_")[1]
    user_id = callback.from_user.id
    await safe_answer(callback, " Запускаю алгоритм...")
    if genre != "auto": discovery_sessions[user_id] = {'forced_genre': genre}
    else: discovery_sessions[user_id] = {}
    
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except: pass
    await send_next_discovery_track(callback.message, user_id)

async def send_next_discovery_track(message: Message, user_id: int, attempt: int = 1):
    if attempt > 3: return await safe_edit(message, "❌ Слишком много битых ссылок.", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 В меню", callback_data="cancel_action")]]))

    wait_msg = await message.answer(f" Ищу трек... (Попытка {attempt}/3)")
    user_dir = get_user_dir(user_id)
    session_data = discovery_sessions.get(user_id, {})
    forced_genre = session_data.get('forced_genre')
    recent_genres, recent_artists = await asyncio.to_thread(get_recent_history, user_id, limit=3)
    
    if forced_genre:
        current_genre = forced_genre
        prev_genre = "start"
        discovery_sessions[user_id]['forced_genre'] = None 
    else:
        prev_genre = session_data.get('last_genre', 'start')
        current_genre = session_data.get('genre')
        if not current_genre:
            last_query = await asyncio.to_thread(get_user_preferences, user_id)
            current_genre = await asyncio.to_thread(get_track_genre, last_query) if last_query else "pop"
            
    last_artist = recent_artists[-1] if recent_artists else None
    query_target, pred_mode = None, "Жанры"
    
    if not forced_genre and last_artist and random.random() < 0.3:
        next_artist = await asyncio.to_thread(get_next_artist, user_id, last_artist)
        if next_artist: query_target, pred_mode, next_genre = f"{next_artist} best audio", "Артисты", "mixed"

    if not query_target:
        next_genre = await asyncio.to_thread(get_next_user_genre, user_id, prev_genre, current_genre, recent_genres)
        query_target = await asyncio.to_thread(get_track_by_genre, next_genre)
    
    results = await asyncio.to_thread(search_tracks_on_youtube, query_target, 1)
    if not results:
         try: await wait_msg.delete()
         except: pass
         return await send_next_discovery_track(message, user_id, attempt + 1) 
         
    url = results[0]['url']
    wait_msg = await safe_edit(wait_msg, f"⬇️ Качаю трек...")
    success, result_path = await asyncio.to_thread(download_track_by_url, url, user_dir)
    
    if not success:
         try: await wait_msg.delete()
         except: pass
         return await send_next_discovery_track(message, user_id, attempt + 1)
    
    await asyncio.to_thread(quick_add_to_library, user_dir, result_path)
    invalidate_library_cache(user_id)
         
    discovery_sessions[user_id] = {
        'filepath': result_path, 'genre': next_genre, 'query': query_target, 
        'last_genre': current_genre, 'prev_genre': prev_genre,
        'last_artist': last_artist, 'new_artist': extract_artist(results[0]['title'])
    }
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👍 Забрать", callback_data="tinder_like"), InlineKeyboardButton(text="👎 Скип", callback_data="tinder_dislike")],
        [InlineKeyboardButton(text="🛑 Выйти в меню", callback_data="tinder_stop")]
    ])
    
    await upload_with_progress(message.chat.id, result_path, f"Discovery: {next_genre.upper()}", wait_msg, kb)

@dp.callback_query(F.data == "tinder_like")
async def tinder_like(callback: CallbackQuery):
    await safe_answer(callback, "❤️ Забрали!") 
    user_id = callback.from_user.id
    session = discovery_sessions.get(user_id)
    if not session: return
    prev_genre = session.get('prev_genre', 'start')
    
    await asyncio.to_thread(record_user_transition, user_id, prev_genre, session['last_genre'], session['genre'])
    if session.get('last_artist'): await asyncio.to_thread(record_artist_transition, user_id, session['last_artist'], session['new_artist'])
    await asyncio.to_thread(add_user_preference, user_id, session['query'], genre=session['genre'])
    
    try: await callback.message.edit_reply_markup(reply_markup=None)
    except: pass
    await send_next_discovery_track(callback.message, user_id)

@dp.callback_query(F.data == "tinder_dislike")
async def tinder_dislike(callback: CallbackQuery):
    await safe_answer(callback, "🗑 Скип!") 
    user_id = callback.from_user.id
    session = discovery_sessions.get(user_id)
    if session and os.path.exists(session['filepath']):
        try: os.remove(session['filepath'])
        except: pass
    try: await callback.message.delete()
    except: pass
    await send_next_discovery_track(callback.message, user_id)

@dp.callback_query(F.data == "tinder_stop")
async def tinder_stop(callback: CallbackQuery):
    await safe_answer(callback) 
    user_id = callback.from_user.id
    session = discovery_sessions.get(user_id)
    if session and os.path.exists(session['filepath']):
        try: os.remove(session['filepath'])
        except: pass
    try: await callback.message.delete()
    except: pass
    await callback.message.answer(" Вышли из Тиндера.\nГлавное меню:", reply_markup=get_main_menu())

async def main():
    os.makedirs(BASE_MUSIC_DIR, exist_ok=True)
    print("🤖 ИИ-Диджей запущен успешно! Ошибка 'query is too old' устранена.")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
