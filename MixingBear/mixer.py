import os
import argparse
import random
import librosa
import numpy as np
from pydub import AudioSegment
from pydub.silence import detect_nonsilent
from pydub.effects import high_pass_filter

# ===  1. АНАЛИЗАТОРЫ ===
MAJ_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MIN_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
NOTES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

CAMELOT_MINOR = {'G#': '1A', 'D#': '2A', 'A#': '3A', 'F': '4A', 'C': '5A', 'G': '6A', 'D': '7A', 'A': '8A', 'E': '9A', 'B': '10A', 'F#': '11A', 'C#': '12A'}
CAMELOT_MAJOR = {'B': '1B', 'F#': '2B', 'C#': '3B', 'G#': '4B', 'D#': '5B', 'A#': '6B', 'F': '7B', 'C': '8B', 'G': '9B', 'D': '10B', 'A': '11B', 'E': '12B'}

def get_track_key(file_path):
    try:
        y, sr = librosa.load(file_path, duration=45, offset=15) 
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        chroma_sum = np.sum(chroma, axis=1)
        maj_corrs = [np.corrcoef(chroma_sum, np.roll(MAJ_PROFILE, i))[0, 1] for i in range(12)]
        min_corrs = [np.corrcoef(chroma_sum, np.roll(MIN_PROFILE, i))[0, 1] for i in range(12)]
        if max(maj_corrs) > max(min_corrs): return CAMELOT_MAJOR[NOTES[maj_corrs.index(max(maj_corrs))]]
        else: return CAMELOT_MINOR[NOTES[min_corrs.index(max(min_corrs))]]
    except: return "0X"

def is_harmonically_compatible(cam1, cam2):
    if cam1 == "0X" or cam2 == "0X": return False
    n1, l1 = int(cam1[:-1]), cam1[-1]
    n2, l2 = int(cam2[:-1]), cam2[-1]
    if cam1 == cam2: return True
    if n1 == n2 and l1 != l2: return True
    if l1 == l2 and (abs(n1 - n2) == 1 or abs(n1 - n2) == 11): return True
    return False

def get_track_bpm(file_path):
    try:
        y, sr = librosa.load(file_path, duration=60)
        bpm, _ = librosa.beat.beat_track(y=y, sr=sr)
        return float(bpm[0]) if isinstance(bpm, np.ndarray) else float(bpm)
    except: return 120.0

# ===  2. БАЗОВАЯ ОБРАБОТКА И УДАЛЕНИЕ ТИШИНЫ ===
def trim_beatless_tail(audio, file_path, max_tail_ms=6000):
    """
    Ищет последнюю бочку. Если после нее эмбиент/тишина длится 
    дольше 6 секунд, плавно срезает этот кусок для плотного сведения.
    """
    try:
        duration = audio.duration_seconds
        if duration < 30:
            return audio # Трек слишком короткий, не трогаем
            
        # Чтобы не грузить память, анализируем только последние 45 секунд
        offset = max(0, duration - 45)
        y, sr = librosa.load(file_path, offset=offset, duration=45)
        
        # МАГИЯ: Отделяем ударные (percussive) от гармонии/вокала
        _, y_percussive = librosa.effects.hpss(y)
        
        # Ищем резкие всплески (onset) именно на ударной партии
        onsets = librosa.onset.onset_detect(y=y_percussive, sr=sr)
        onset_times = librosa.frames_to_time(onsets, sr=sr)
        
        if len(onset_times) > 0:
            # Время последнего удара относительно 45-секундного отрезка
            last_beat_in_snippet = onset_times[-1] 
            
            # Переводим в миллисекунды для Pydub
            snippet_start_ms = int(offset * 1000)
            last_beat_ms = snippet_start_ms + int(last_beat_in_snippet * 1000)
            
            tail_length = len(audio) - last_beat_ms
            
            # Если хвост без бита длиннее 6 секунд — режем!
            if tail_length > max_tail_ms:
                print(f"   ✂️ Найден длинный финал без ритма ({tail_length/1000:.1f} сек). Срезаю...")
                # Оставляем 2 секунды после удара, чтобы не обрубать резко, и делаем фейдаут
                keep_until = last_beat_ms + 2000 
                return audio[:keep_until].fade_out(2000)
                
    except Exception as e:
        print(f"   ⚠️ Ошибка анализа хвоста: {e}")
        
    return audio

def standardize_audio(audio):
    return audio.set_frame_rate(44100).set_channels(2).set_sample_width(2)

def match_loudness(audio, target_dBFS=-10.0):
    return audio.apply_gain(target_dBFS - audio.dBFS)

def strip_silence(audio):
    """Агрессивно отрезает мертвую тишину (вступления клипов YT)"""
    nonsilent = detect_nonsilent(audio, min_silence_len=200, silence_thresh=-45)
    if nonsilent:
        start_trim = max(0, nonsilent[0][0] - 100) # Оставляем 100мс для мягкости
        end_trim = min(len(audio), nonsilent[-1][1] + 100)
        return audio[start_trim:end_trim]
    return audio

def vinyl_sync(audio, original_bpm, target_bpm):
    if original_bpm == 0 or target_bpm == 0 or original_bpm == target_bpm: return audio
    ratio = target_bpm / original_bpm
    if 0.85 <= ratio <= 1.15: 
        synced = audio._spawn(audio.raw_data, overrides={'frame_rate': int(audio.frame_rate * ratio)})
        return standardize_audio(synced) 
    return audio

# ===  3. НЕПРЕРЫВНЫЕ ПЕРЕХОДЫ (NO GAPS) ===

def smooth_bass_kill(audio_chunk, duration_ms):
    """
     Идеально плавный срез баса (без 'лесенки').
    Имитирует диджейский плавный поворот ручки Low EQ.
    """
    if len(audio_chunk) < duration_ms:
        duration_ms = len(audio_chunk)
        
    # 1. Делаем копию куска, где бас УЖЕ срезан наглухо
    no_bass_chunk = high_pass_filter(audio_chunk, 400)
    
    # 2. Оригинал (с басом) плавно затухает...
    fading_original = audio_chunk.fade_out(duration_ms)
    
    # 3. ...а версия без баса синхронно нарастает
    fading_no_bass = no_bass_chunk.fade_in(duration_ms)
    
    # 4. Склеиваем их бутербродом! 
    # Частоты перетекают одна в другую без единой ступеньки.
    return fading_original.overlay(fading_no_bass)

def true_eq_overlap(track_a, track_b, overlap_ms):
    """
    Глубокое наложение (True Overlap). Никакой тишины.
    Трек А плавно растворяется, Трек Б бьет мощным басом.
    """
    overlap_ms = min(int(overlap_ms), 12000, len(track_a)//3, len(track_b)//3)
    if overlap_ms < 2000: return track_a.append(track_b, crossfade=1000)

    part_a = track_a[:-overlap_ms]
    tail_a = track_a[-overlap_ms:]
    head_b = track_b[:overlap_ms]
    part_b = track_b[overlap_ms:]

    #  ИСПОЛЬЗУЕМ НОВЫЙ ПЛАВНЫЙ ЭКВАЛАЙЗЕР
    # Бас уходит гладко, а затем сам трек плавно затухает по громкости
    tail_a_eq = smooth_bass_kill(tail_a, overlap_ms).fade_out(overlap_ms)
    
    # Входящий трек нарастает чуть быстрее, чтобы удержать энергию
    head_b_eq = head_b.fade_in(int(overlap_ms * 0.3))

    # Слияние
    overlap_region = tail_a_eq.overlay(head_b_eq)

    return part_a + overlap_region + part_b
    """
    Глубокое наложение (True Overlap). Никакой тишины.
    Трек А плавно растворяется, Трек Б бьет мощным басом.
    """
    overlap_ms = min(int(overlap_ms), 12000, len(track_a)//3, len(track_b)//3)
    if overlap_ms < 2000: return track_a.append(track_b, crossfade=1000)

    part_a = track_a[:-overlap_ms]
    tail_a = track_a[-overlap_ms:]
    head_b = track_b[:overlap_ms]
    part_b = track_b[overlap_ms:]

    # Срезаем бас на выходящем треке, чтобы не конфликтовал с бочкой нового
    tail_a_eq = high_pass_filter(tail_a, 400).fade_out(overlap_ms)
    
    # Входящий трек нарастает чуть быстрее (за 30% времени наложения), чтобы удержать энергию
    head_b_eq = head_b.fade_in(int(overlap_ms * 0.3))

    # Слияние
    overlap_region = tail_a_eq.overlay(head_b_eq)

    return part_a + overlap_region + part_b


def brake_and_drop(track_a, track_b, brake_ms):
    """Остановка винила, которая СРАЗУ переходит в следующий трек (Overlap)"""
    brake_ms = min(brake_ms, len(track_a)//4)
    if brake_ms < 1000: return track_a.append(track_b, crossfade=500)
    
    part_a = track_a[:-brake_ms]
    tail_a = track_a[-brake_ms:]
    
    chunk_size = 50
    chunks = []
    for i in range(0, len(tail_a), chunk_size):
        chunk = tail_a[i:i+chunk_size]
        ratio = max(0.05, 1.0 - ((i / len(tail_a)) ** 1.2))
        slow_chunk = chunk._spawn(chunk.raw_data, overrides={'frame_rate': int(chunk.frame_rate * ratio)})
        chunks.append(standardize_audio(slow_chunk))
        
    brake_tail = chunks[0]
    for c in chunks[1:]: brake_tail = brake_tail.append(c, crossfade=10)
    
    brake_tail = brake_tail.fade_out(300)
    
    #  Накладываем начало нового трека ПРЯМО НА ХВОСТ эффекта остановки (последние 400мс)
    overlap_time = min(400, len(brake_tail), len(track_b))
    head_b = track_b[:overlap_time].fade_in(50)
    
    end_of_brake = brake_tail[-overlap_time:].overlay(head_b)
    final_brake = brake_tail[:-overlap_time] + end_of_brake
    
    return part_a + final_brake + track_b[overlap_time:]


def roll_and_drop(track_a, track_b, beat_ms):
    """Разгон (Пулемет), мгновенно вливающийся в новый трек"""
    duration_ms = int(beat_ms * 4)
    if len(track_a) < duration_ms: return track_a.append(track_b, crossfade=500)
    
    part_a = track_a[:-duration_ms]
    sample_beat = track_a[-duration_ms : -duration_ms + int(beat_ms)]
    
    roll = AudioSegment.empty()
    roll += sample_beat * 2
    roll += sample_beat[:int(beat_ms / 2)] * 4
    roll += sample_beat[:int(beat_ms / 4)] * 8
    
    roll = high_pass_filter(roll, 800)
    
    #  Новый трек начинается в ту же секунду, где обрывается ролл
    return part_a + roll + track_b

# ===  4. ЯДРО МИКСЕРА ===

def create_dj_mix(files, output_path):
    if len(files) < 2:
        print("❌ Ошибка: Минимум 2 файла!")
        exit(1)
        
    print("\n Запуск AI DJ Mixer v6.0 (Continuous Engine - Zero Silence)")
    
    # Загрузка и очистка первого трека
    mix = AudioSegment.from_file(files[0])
    mix = standardize_audio(mix)
    mix = strip_silence(mix)
    mix = match_loudness(mix)
    mix = trim_beatless_tail(mix, files[0])
    
    current_bpm = get_track_bpm(files[0])
    current_cam = get_track_key(files[0])
    print(f"💽 ТРЕК 1: {os.path.basename(files[0])} | {current_bpm:.1f} BPM")
    
    for i in range(1, len(files)):
        next_file = files[i]
        
        # Загрузка и очистка следующего трека
        next_track = AudioSegment.from_file(next_file)
        next_track = standardize_audio(next_track)
        next_track = strip_silence(next_track)
        next_track = match_loudness(next_track)
        next_track = AudioSegment.from_file(next_file)
        next_track = standardize_audio(next_track)
        next_track = strip_silence(next_track)
        next_track = match_loudness(next_track)
        next_track = trim_beatless_tail(next_track, next_file)
        
        next_bpm = get_track_bpm(next_file)
        next_cam = get_track_key(next_file)
        print(f"\n СВОЖУ С: {os.path.basename(next_file)} | {next_bpm:.1f} BPM")
        
        bpm_diff = abs(current_bpm - next_bpm)
        is_harmonic = is_harmonically_compatible(current_cam, next_cam)
        beat_ms = (60000.0 / current_bpm) if current_bpm > 0 else 500
        
        # --- СЦЕНАРИЙ 1: ТЕМП БЛИЗОК ---
        if bpm_diff <= (current_bpm * 0.15):
            print("   🔄 Синхронизирую сетку (Beat-sync)...")
            next_track = vinyl_sync(next_track, next_bpm, current_bpm)
            
            if is_harmonic:
                # Глубокое наложение на 15 секунд
                overlap_time = int(beat_ms * 32)
                print(f"   🌈 Идеальная гармония. Делаю длинный Overlap ({overlap_time} мс).")
                mix = true_eq_overlap(mix, next_track, overlap_time)
            else:
                # Короткое наложение на 7-8 секунд
                overlap_time = int(beat_ms * 16)
                print(f"   ⚡ Конфликт нот. Делаю плотный Overlap ({overlap_time} мс).")
                mix = true_eq_overlap(mix, next_track, overlap_time)
                
        # --- СЦЕНАРИЙ 2: ТЕМП ПАДАЕТ ---
        elif next_bpm < current_bpm:
            brake_ms = int(beat_ms * 3)
            print("   📉 Сброс энергии. Brake & Drop.")
            mix = brake_and_drop(mix, next_track, brake_ms)
            
        # --- СЦЕНАРИЙ 3: ТЕМП РАСТЕТ ---
        else:
            print("   🚀 Разгон танцпола. Roll & Drop.")
            mix = roll_and_drop(mix, next_track, beat_ms)
            
        current_bpm = next_bpm 
        current_cam = next_cam
        
    print("\n🔊 Мастеринг: Защита от перегруза (Soft Clipping)...")
    if mix.max_dBFS > -1.0:
        mix = mix.apply_gain(-1.0 - mix.max_dBFS)
        
    mix.export(output_path, format="mp3", bitrate="320k")
    print(f"✅ Микс сохранен: {output_path}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('files', nargs='+', help="Файлы")
    parser.add_argument('-o', '--output', required=True)
    args = parser.parse_args()
    create_dj_mix(args.files, args.output)