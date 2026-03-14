import os
import argparse
import librosa
import numpy as np
import soundfile as sf
import urllib.request
import urllib.parse
import re
import warnings
from pydub import AudioSegment
from pydub.silence import detect_nonsilent
from pydub.effects import high_pass_filter

#  Отключаем системный спам от librosa (audioread warning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# ==========================================
#  ГЛОБАЛЬНЫЙ КЭШ АНАЛИЗАТОРА
# ==========================================
BPM_KEY_CACHE = {} 

# ==========================================
#  1. WEB-ПАРСЕР И АНАЛИЗАТОРЫ (BPM + KEY)
# ==========================================
MAJ_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MIN_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

CAMELOT_MINOR = {'G#': '1A', 'D#': '2A', 'A#': '3A', 'F': '4A', 'C': '5A', 'G': '6A', 'D': '7A', 'A': '8A', 'E': '9A', 'B': '10A', 'F#': '11A', 'C#': '12A'}
CAMELOT_MAJOR = {'B': '1B', 'F#': '2B', 'C#': '3B', 'G#': '4B', 'D#': '5B', 'A#': '6B', 'F': '7B', 'C': '8B', 'G': '9B', 'D': '10B', 'A': '11B', 'E': '12B'}

KEY_TO_CAMELOT = {
    'C Major': '8B', 'C Minor': '5A', 'C# Major': '3B', 'C# Minor': '12A',
    'Db Major': '3B', 'Db Minor': '12A', 'D Major': '10B', 'D Minor': '7A',
    'D# Major': '5B', 'D# Minor': '2A', 'Eb Major': '5B', 'Eb Minor': '2A',
    'E Major': '12B', 'E Minor': '9A', 'F Major': '7B', 'F Minor': '4A',
    'F# Major': '2B', 'F# Minor': '11A', 'Gb Major': '2B', 'Gb Minor': '11A',
    'G Major': '9B', 'G Minor': '6A', 'G# Major': '4B', 'G# Minor': '1A',
    'Ab Major': '4B', 'Ab Minor': '1A', 'A Major': '11B', 'A Minor': '8A',
    'A# Major': '6B', 'A# Minor': '3A', 'Bb Major': '6B', 'Bb Minor': '3A',
    'B Major': '1B', 'B Minor': '10A'
}

def get_web_bpm_key(filename):
    try:
        query = re.sub(r'\(.*?\)|\[.*?\]|\.mp3|\.wav', '', os.path.basename(filename)).strip()
        url = f"https://musicstax.com/search?q={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        html_code = urllib.request.urlopen(req, timeout=2).read().decode('utf-8')
        
        bpm_match = re.search(r'BPM.*?(\d{2,3})', html_code, re.I)
        key_match = re.search(r'Key.*?([A-G][#b]?\s(?:Major|Minor))', html_code, re.I)
        
        web_bpm = float(bpm_match.group(1)) if bpm_match else None
        web_key = key_match.group(1) if key_match else None
        return web_bpm, KEY_TO_CAMELOT.get(web_key, "0X") if web_key else "0X"
    except: return None, "0X"

def get_bpm_and_key(file_path):
    if file_path in BPM_KEY_CACHE:
        bpm, key = BPM_KEY_CACHE[file_path]
        print(f"   ⚡ Взято из кэша: {bpm} BPM | {key}")
        return bpm, key

    print(f"   🌐 Ищу данные в сети: {os.path.basename(file_path)}...")
    web_bpm, web_key = get_web_bpm_key(file_path)
    
    if web_bpm:
        print(f"   ✅ Студийные данные: {web_bpm} BPM | {web_key}")
        BPM_KEY_CACHE[file_path] = (web_bpm, web_key)
        return web_bpm, web_key
        
    print("   🚀 В сети нет. Быстрый ИИ-анализ (Librosa)...")
    bpm, key = 120.0, "0X"
    try:
        y, sr = librosa.load(file_path, duration=30, offset=15, sr=11025)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo[0]) if isinstance(tempo, np.ndarray) else float(tempo)
        
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        chroma_sum = np.sum(chroma, axis=1)
        maj_corrs = [np.corrcoef(chroma_sum, np.roll(MAJ_PROFILE, i))[0, 1] for i in range(12)]
        min_corrs = [np.corrcoef(chroma_sum, np.roll(MIN_PROFILE, i))[0, 1] for i in range(12)]
        if max(maj_corrs) > max(min_corrs): key = CAMELOT_MAJOR[NOTES[maj_corrs.index(max(maj_corrs))]]
        else: key = CAMELOT_MINOR[NOTES[min_corrs.index(max(min_corrs))]]
    except: pass
    
    print(f"   ✅ ИИ рассчитал: {bpm:.1f} BPM | {key}")
    BPM_KEY_CACHE[file_path] = (bpm, key)
    return bpm, key

def is_harmonically_compatible(cam1, cam2):
    if cam1 == "0X" or cam2 == "0X": return False
    n1, l1 = int(cam1[:-1]), cam1[-1]
    n2, l2 = int(cam2[:-1]), cam2[-1]
    if cam1 == cam2: return True
    if n1 == n2 and l1 != l2: return True
    if l1 == l2 and (abs(n1 - n2) == 1 or abs(n1 - n2) == 11): return True
    return False

def get_first_beat_ms(file_path):
    try:
        y, sr = librosa.load(file_path, duration=15, sr=11025)
        _, beats = librosa.beat.beat_track(y=y, sr=sr)
        if len(beats) > 0: return float(librosa.frames_to_time(beats, sr=sr)[0] * 1000)
    except: pass
    return 0.0

def stretch_audio_preserve_pitch(file_path, target_bpm, original_bpm):
    if original_bpm == 0 or target_bpm == 0: return file_path, False
    
    if target_bpm / original_bpm >= 1.6: original_bpm *= 2
    elif original_bpm / target_bpm >= 1.6: original_bpm /= 2
        
    rate = target_bpm / original_bpm
    if rate < 0.85 or rate > 1.15: return file_path, False
    if 0.98 <= rate <= 1.02: return file_path, False 
    
    y, sr = librosa.load(file_path, sr=None) 
    y_stretched = librosa.effects.time_stretch(y, rate=rate)
    temp_path = file_path + "_temp_stretched.wav"
    sf.write(temp_path, y_stretched, sr)
    return temp_path, True

# ==========================================
#  2. УТИЛИТЫ И ОБРАБОТКА (PYDUB)
# ==========================================
def standardize_audio(audio): return audio.set_frame_rate(44100).set_channels(2).set_sample_width(2)
def match_loudness(audio, target_dBFS=-10.0): return audio.apply_gain(target_dBFS - audio.dBFS)

def strip_silence(audio):
    nonsilent = detect_nonsilent(audio, min_silence_len=200, silence_thresh=-45)
    if nonsilent: return audio[max(0, nonsilent[0][0] - 100):min(len(audio), nonsilent[-1][1] + 100)]
    return audio

def trim_beatless_tail(audio, file_path, max_tail_ms=6000):
    try:
        if audio.duration_seconds < 30: return audio
        offset = max(0, audio.duration_seconds - 30)
        y, sr = librosa.load(file_path, offset=offset, duration=30, sr=11025)
        onsets = librosa.onset.onset_detect(y=y, sr=sr)
        onset_times = librosa.frames_to_time(onsets, sr=sr)
        
        if len(onset_times) > 0:
            last_beat_ms = int(offset * 1000) + int(onset_times[-1] * 1000)
            if len(audio) - last_beat_ms > max_tail_ms: 
                return audio[:last_beat_ms + 2000].fade_out(2000)
    except: pass
    return audio

def vinyl_sync(audio, original_bpm, target_bpm):
    if original_bpm == 0 or target_bpm == 0 or original_bpm == target_bpm: return audio
    ratio = target_bpm / original_bpm
    if 0.85 <= ratio <= 1.15: return standardize_audio(audio._spawn(audio.raw_data, overrides={'frame_rate': int(audio.frame_rate * ratio)})) 
    return audio

# ==========================================
#  3. DJ ПЕРЕХОДЫ (ТРАНЗИЦИИ)
# ==========================================
def true_eq_overlap(track_a, track_b, overlap_ms):
    overlap_ms = min(int(overlap_ms), 12000, len(track_a)//3, len(track_b)//3)
    if overlap_ms < 2000: return track_a.append(track_b, crossfade=1000)
    part_a, tail_a = track_a[:-overlap_ms], track_a[-overlap_ms:]
    head_b, part_b = track_b[:overlap_ms], track_b[overlap_ms:]
    
    no_bass = high_pass_filter(tail_a, 400).fade_in(overlap_ms)
    tail_a_eq = tail_a.fade_out(overlap_ms).overlay(no_bass).fade_out(overlap_ms)
    head_b_eq = head_b.fade_in(int(overlap_ms * 0.3))
    return part_a + tail_a_eq.overlay(head_b_eq) + part_b

def filter_drop(track_a, track_b, drop_ms):
    drop_ms = min(drop_ms, 2000, len(track_a)//4)
    if drop_ms < 500: return track_a.append(track_b, crossfade=500)
    part_a, tail_a = track_a[:-drop_ms], track_a[-drop_ms:]
    tail_a_filtered = high_pass_filter(tail_a, 800).fade_out(drop_ms)
    overlap_time = min(drop_ms, len(track_b))
    head_b = track_b[:overlap_time]
    return part_a + tail_a_filtered.overlay(head_b) + track_b[overlap_time:]

# ==========================================
#  4. PRO-ДВИЖКИ
# ==========================================
def create_dj_mix(files, output_path):
    if len(files) < 2: return False, "Минимум 2 файла!"
    print(f"\n⚡ Запуск TURBO DJ Mixer (Количество треков: {len(files)})")
    
    mix = trim_beatless_tail(match_loudness(strip_silence(standardize_audio(AudioSegment.from_file(files[0])))), files[0])
    current_bpm, current_cam = get_bpm_and_key(files[0])
    
    for i in range(1, len(files)):
        next_file = files[i]
        next_track = trim_beatless_tail(match_loudness(strip_silence(standardize_audio(AudioSegment.from_file(next_file)))), next_file)
        next_bpm, next_cam = get_bpm_and_key(next_file)
        
        bpm_diff = abs(current_bpm - next_bpm)
        is_harmonic = is_harmonically_compatible(current_cam, next_cam)
        beat_ms = (60000.0 / current_bpm) if current_bpm > 0 else 500
        
        if bpm_diff <= (current_bpm * 0.15):
            print("   🔄 Темпы близки. Делаю Beat-sync...")
            next_track = vinyl_sync(next_track, next_bpm, current_bpm)
            overlap = int(beat_ms * 32) if is_harmonic else int(beat_ms * 16)
            mix = true_eq_overlap(mix, next_track, overlap)
        else:
            print(f"   🧨 Разный темп ({current_bpm:.0f} ➡️ {next_bpm:.0f}). Резкий Filter Drop!")
            mix = filter_drop(mix, next_track, int(beat_ms * 4))
            
        current_bpm, current_cam = next_bpm, next_cam
        
    if mix.max_dBFS > -1.0: mix = mix.apply_gain(-1.0 - mix.max_dBFS)
    mix.export(output_path, format="mp3", bitrate="320k")
    print(f"✅ Микс сохранен: {output_path}")
    return True, output_path

def create_continuous_mix(files, output_path):
    if len(files) < 2: return False, "Минимум 2 файла!"
    print(f"\n📻 Запуск Классического радио-миксера (Треков: {len(files)})...")
    mix = match_loudness(strip_silence(standardize_audio(AudioSegment.from_file(files[0]))))
    for i in range(1, len(files)):
        next_track = match_loudness(strip_silence(standardize_audio(AudioSegment.from_file(files[i]))))
        mix = mix.append(next_track, crossfade=5000)
    if mix.max_dBFS > -1.0: mix = mix.apply_gain(-1.0 - mix.max_dBFS)
    mix.export(output_path, format="mp3", bitrate="320k")
    return True, output_path

def create_mashup(track1_path, track2_path, output_path, user_dir):
    try: from src.stem_separator import extract_minus
    except ImportError: return False, "Модуль stem_separator не найден!"
    _, vocal1_path = extract_minus(track1_path, user_dir)
    minus2_path, _ = extract_minus(track2_path, user_dir)
    if not vocal1_path or not minus2_path: return False, "Ошибка Demucs."

    vocal_bpm, _ = get_bpm_and_key(vocal1_path)
    beat_bpm, _ = get_bpm_and_key(minus2_path)
    stretched_vocal, is_temp = stretch_audio_preserve_pitch(vocal1_path, beat_bpm, vocal_bpm)
    
    try:
        vocal = AudioSegment.from_file(stretched_vocal)
        beat = AudioSegment.from_file(minus2_path)
        vocal_offset = get_first_beat_ms(stretched_vocal)
        beat_offset = get_first_beat_ms(minus2_path)
        
        if vocal_offset > beat_offset: beat = AudioSegment.silent(duration=int(vocal_offset - beat_offset)) + beat
        elif beat_offset > vocal_offset: vocal = AudioSegment.silent(duration=int(beat_offset - vocal_offset)) + vocal

        vocal = high_pass_filter(vocal, 250) + 4.0 
        beat = beat - 3.0                           
        mashup = beat.overlay(vocal)
        if mashup.max_dBFS > -1.0: mashup = mashup.apply_gain(-1.0 - mashup.max_dBFS)
        mashup.export(output_path, format="mp3", bitrate="320k")
        return True, output_path
    except Exception as e: return False, str(e)
    finally:
        if is_temp and os.path.exists(stretched_vocal): os.remove(stretched_vocal)

def create_vocal_battle(track1_path, track2_path, output_path, user_dir):
    try: from src.stem_separator import extract_minus
    except ImportError: return False, "Модуль stem_separator не найден!"

    minus1, vocal1 = extract_minus(track1_path, user_dir)
    minus2, vocal2 = extract_minus(track2_path, user_dir)
    if not minus1 or not minus2: return False, "Ошибка разделения."

    bpm1, _ = get_bpm_and_key(minus1)
    bpm2, _ = get_bpm_and_key(minus2)
    stretched_vocal2, is_temp = stretch_audio_preserve_pitch(vocal2, bpm1, bpm2)
    
    try:
        v1 = high_pass_filter(AudioSegment.from_file(vocal1), 250) + 3.0
        v2 = high_pass_filter(AudioSegment.from_file(stretched_vocal2), 250) + 3.0
        m1 = AudioSegment.from_file(minus1) - 3.0
        
        off1 = get_first_beat_ms(minus1)
        off2 = get_first_beat_ms(stretched_vocal2)
        if off1 > off2: v2 = AudioSegment.silent(duration=int(off1 - off2)) + v2
        elif off2 > off1:
            v1 = AudioSegment.silent(duration=int(off2 - off1)) + v1
            m1 = AudioSegment.silent(duration=int(off2 - off1)) + m1

        max_len = max(len(m1), len(v1), len(v2))
        v1 += AudioSegment.silent(duration=max_len - len(v1))
        v2 += AudioSegment.silent(duration=max_len - len(v2))
        m1 += AudioSegment.silent(duration=max_len - len(m1))

        beat_ms = 60000.0 / bpm1 if bpm1 > 0 else 500
        phrase_ms = int(beat_ms * 16)
        
        final_vocal = AudioSegment.empty()
        turn = 1
        
        for i in range(0, max_len, phrase_ms):
            chunk_v1 = v1[i:i+phrase_ms]
            chunk_v2 = v2[i:i+phrase_ms]
            if len(chunk_v1) == 0: break
            
            fade = min(50, len(chunk_v1)//2)
            rms1, rms2 = chunk_v1.rms, chunk_v2.rms
            thr = 300
            
            if turn == 1:
                if rms1 > thr: final_vocal += chunk_v1.fade_in(fade).fade_out(fade)
                elif rms2 > thr: final_vocal += chunk_v2.fade_in(fade).fade_out(fade); turn = 2 
                else: final_vocal += AudioSegment.silent(duration=len(chunk_v1))
                turn = 2
            else:
                if rms2 > thr: final_vocal += chunk_v2.fade_in(fade).fade_out(fade)
                elif rms1 > thr: final_vocal += chunk_v1.fade_in(fade).fade_out(fade); turn = 1
                else: final_vocal += AudioSegment.silent(duration=len(chunk_v2))
                turn = 1
                
        final_mix = m1.overlay(final_vocal)
        if final_mix.max_dBFS > -1.0: final_mix = final_mix.apply_gain(-1.0 - final_mix.max_dBFS)
        final_mix.export(output_path, format="mp3", bitrate="320k")
        return True, output_path
    except Exception as e: return False, str(e)
    finally:
        if is_temp and os.path.exists(stretched_vocal2): os.remove(stretched_vocal2)

def create_smart_transition(track1_path, track2_path, output_path):
    bpm1, cam1 = get_bpm_and_key(track1_path)
    bpm2, cam2 = get_bpm_and_key(track2_path)
    t1 = standardize_audio(AudioSegment.from_file(track1_path))
    
    if abs(bpm1 - bpm2) > (bpm1 * 0.15):
        t2 = standardize_audio(AudioSegment.from_file(track2_path))
        final_mix = filter_drop(t1, t2, int(60000.0 / bpm1 * 4 if bpm1 > 0 else 2000))
        final_mix.export(output_path, format="mp3", bitrate="320k")
        return True, output_path

    beat_ms = 60000.0 / bpm1 if bpm1 > 0 else 500
    square_ms = int(beat_ms * 32)
    stretched_t2, is_temp = stretch_audio_preserve_pitch(track2_path, bpm1, bpm2)
    
    try:
        t2 = standardize_audio(AudioSegment.from_file(stretched_t2))
        if len(t1) < square_ms or len(t2) < square_ms:
            final_mix = t1.append(t2, crossfade=3000)
        else:
            part_a, tail_a = t1[:-square_ms], t1[-square_ms:]
            head_b, part_b = t2[:square_ms], t2[square_ms:]
            tail_a_eq = high_pass_filter(tail_a, 400).fade_out(square_ms)
            head_b_eq = head_b.fade_in(int(square_ms * 0.2))
            final_mix = part_a + tail_a_eq.overlay(head_b_eq) + part_b
            
        final_mix.export(output_path, format="mp3", bitrate="320k")
        return True, output_path
    except Exception as e: return False, str(e)
    finally:
        if is_temp and os.path.exists(stretched_t2): os.remove(stretched_t2)