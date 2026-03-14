import os
import librosa
import soundfile as sf
from pydub import AudioSegment
from pydub.effects import high_pass_filter

# ==========================================
#  УТИЛИТЫ И АНАЛИЗАТОРЫ
# ==========================================
def get_bpm(file_path):
    print(f"   🔍 Считаю BPM для: {os.path.basename(file_path)}...")
    y, sr = None, None
    
    # ПОПЫТКА 1: Читаем напрямую через Librosa
    try:
        y, sr = librosa.load(file_path, duration=60)
    except Exception as e:
        print(f"   ⚠️ Librosa не смогла прочитать файл: {e}")
        print("   ♻️ Пробую запасной вариант (конвертация через pydub)...")
        
    # ПОПЫТКА 2: Если Librosa капризничает, делаем WAV-копию через Pydub
    if y is None:
        try:
            temp_wav = file_path + "_temp_bpm.wav"
            audio = AudioSegment.from_file(file_path)
            # Берем только первую минуту, чтобы не тратить время
            audio[:60000].export(temp_wav, format="wav") 
            
            y, sr = librosa.load(temp_wav)
            os.remove(temp_wav) # Убираем за собой мусор
        except Exception as e2:
            print(f"   ❌ Полный провал при чтении аудио: {e2}")
            return 120.0

    # СЧИТАЕМ РИТМ
    try:
        bpm, _ = librosa.beat.beat_track(y=y, sr=sr)
        import numpy as np
        
        # Разные версии librosa возвращают данные по-разному (число или массив)
        final_bpm = float(bpm[0]) if isinstance(bpm, np.ndarray) else float(bpm)
        print(f"   ✅ BPM найден: {final_bpm:.1f}")
        return final_bpm
    except Exception as e3:
        print(f"   ❌ Ошибка при математическом расчете BPM: {e3}")
        return 120.0

def get_first_beat_ms(file_path):
    """ Ищет точное время первого мощного удара (бита) в миллисекундах"""
    try:
        y, sr = librosa.load(file_path, duration=30)
        _, beats = librosa.beat.beat_track(y=y, sr=sr)
        if len(beats) > 0:
            return float(librosa.frames_to_time(beats, sr=sr)[0] * 1000)
    except:
        pass
    return 0.0

def stretch_audio_preserve_pitch(file_path, target_bpm, original_bpm):
    y, sr = librosa.load(file_path)
    
    if original_bpm > 0 and target_bpm > 0:
        if target_bpm / original_bpm >= 1.7:
            original_bpm *= 2
        elif original_bpm / target_bpm >= 1.7:
            original_bpm /= 2
            
    rate = target_bpm / original_bpm
    
    if 0.97 <= rate <= 1.03:
        return file_path, False
        
    y_stretched = librosa.effects.time_stretch(y, rate=rate)
    temp_path = file_path + "_temp_stretched.wav"
    sf.write(temp_path, y_stretched, sr)
    return temp_path, True

# ==========================================
#  1. МЭШАП (ИДЕАЛЬНЫЙ: ВОКАЛ 1 + МИНУС 2)
# ==========================================
def create_mashup(track1_path, track2_path, output_path, user_dir):
    try:
        from src.stem_separator import extract_minus
    except ImportError:
        return False, "Модуль stem_separator не найден!"

    print(f" МЭШАП: Подготовка стемов (Demucs)...")
    
    # 1. Извлекаем чистый вокал из ПЕРВОГО трека
    print("   ⏳ Вырезаю вокал из Трека 1...")
    _, vocal1_path = extract_minus(track1_path, user_dir)
    
    # 2. Извлекаем чистый минус из ВТОРОГО трека (удаляем его родной вокал!)
    print("   ⏳ Вырезаю минус из Трека 2...")
    minus2_path, _ = extract_minus(track2_path, user_dir)
    
    if not vocal1_path or not minus2_path:
        return False, "Не удалось разделить треки. Возможно, не хватило памяти."

    # Высчитываем темп для чистого вокала и чистого минуса
    vocal_bpm = get_bpm(vocal1_path)
    beat_bpm = get_bpm(minus2_path)
    
    print(f"   BPM Вокала: {vocal_bpm:.1f} | BPM Бита: {beat_bpm:.1f}")
    
    # Тянем вокал под скорость минуса
    stretched_vocal_path, is_temp = stretch_audio_preserve_pitch(vocal1_path, beat_bpm, vocal_bpm)
    
    try:
        vocal = AudioSegment.from_file(stretched_vocal_path)
        beat = AudioSegment.from_file(minus2_path)
        
        #  СИНХРОНИЗАЦИЯ: Находим первые удары
        vocal_offset = get_first_beat_ms(stretched_vocal_path)
        beat_offset = get_first_beat_ms(minus2_path)
        
        print(f"   Сдвиг Вокала: {vocal_offset:.0f}мс | Сдвиг Бита: {beat_offset:.0f}мс")
        
        if vocal_offset > beat_offset:
            beat = AudioSegment.silent(duration=int(vocal_offset - beat_offset)) + beat
        elif beat_offset > vocal_offset:
            vocal = AudioSegment.silent(duration=int(beat_offset - vocal_offset)) + vocal

        # 🎛 ЭКВАЛАЙЗЕР И ГРОМКОСТЬ
        vocal = high_pass_filter(vocal, 250) + 4.0 
        beat = beat - 3.0                           
        
        # Слияние
        mashup = beat.overlay(vocal)
        
        if mashup.max_dBFS > -1.0:
            mashup = mashup.apply_gain(-1.0 - mashup.max_dBFS)
            
        mashup.export(output_path, format="mp3", bitrate="320k")
        print(f"✅ Идеальный мэшап готов: {output_path}")
        return True, output_path
    except Exception as e:
        return False, f"Ошибка мэшапа: {str(e)}"
    finally:
        if is_temp and os.path.exists(stretched_vocal_path):
            os.remove(stretched_vocal_path)

# ==========================================
#  2. УМНЫЙ МИКС (ПО КВАДРАТАМ)
# ==========================================
def create_smart_transition(track1_path, track2_path, output_path):
    print(f" Умный переход по квадратам")
    bpm1 = get_bpm(track1_path)
    bpm2 = get_bpm(track2_path)
    
    beat_ms = 60000.0 / bpm1
    square_ms = int(beat_ms * 32)
    
    t1 = AudioSegment.from_file(track1_path)
    stretched_t2_path, is_temp = stretch_audio_preserve_pitch(track2_path, bpm1, bpm2)
    
    try:
        t2 = AudioSegment.from_file(stretched_t2_path)
        
        if len(t1) < square_ms or len(t2) < square_ms:
            final_mix = t1.append(t2, crossfade=5000)
        else:
            part_a = t1[:-square_ms]
            tail_a = t1[-square_ms:]
            head_b = t2[:square_ms]
            part_b = t2[square_ms:]
            
            tail_a_eq = high_pass_filter(tail_a, 400).fade_out(square_ms)
            head_b_eq = head_b.fade_in(int(square_ms * 0.2))
            
            overlap_region = tail_a_eq.overlay(head_b_eq)
            final_mix = part_a + overlap_region + part_b
            
        final_mix.export(output_path, format="mp3", bitrate="320k")
        return True, output_path
    except Exception as e:
        return False, str(e)
    finally:
        if is_temp and os.path.exists(stretched_t2_path):
            os.remove(stretched_t2_path)

# ==========================================
# ⚔️ 3. ВОКАЛЬНЫЙ БАТТЛ (СТРОГО ПО ОЧЕРЕДИ)
# ==========================================
def create_vocal_battle(track1_path, track2_path, output_path, user_dir):
    try:
        from src.stem_separator import extract_minus
    except ImportError:
        return False, "Модуль stem_separator не найден!"

    print("⚔️ ВОКАЛЬНЫЙ БАТТЛ: Разделяю треки (Demucs)...")
    minus1, vocal1 = extract_minus(track1_path, user_dir)
    minus2, vocal2 = extract_minus(track2_path, user_dir)
    
    if not minus1 or not minus2:
        return False, "Не удалось вырезать вокал. Возможно, не хватило памяти."

    bpm1 = get_bpm(minus1)
    bpm2 = get_bpm(minus2)
    
    print("   ⏳ Синхронизирую темп и ритм...")
    stretched_vocal2, is_temp = stretch_audio_preserve_pitch(vocal2, bpm1, bpm2)
    
    try:
        v1 = AudioSegment.from_file(vocal1)
        v2 = AudioSegment.from_file(stretched_vocal2)
        m1 = AudioSegment.from_file(minus1)
        
        offset1 = get_first_beat_ms(minus1)
        offset2 = get_first_beat_ms(stretched_vocal2)
        
        if offset1 > offset2:
            v2 = AudioSegment.silent(duration=int(offset1 - offset2)) + v2
        elif offset2 > offset1:
            v1 = AudioSegment.silent(duration=int(offset2 - offset1)) + v1
            m1 = AudioSegment.silent(duration=int(offset2 - offset1)) + m1

        v1 = high_pass_filter(v1, 250) + 3.0
        v2 = high_pass_filter(v2, 250) + 3.0
        m1 = m1 - 3.0 
        
        max_len = max(len(m1), len(v1), len(v2))
        v1 = v1 + AudioSegment.silent(duration=max_len - len(v1))
        v2 = v2 + AudioSegment.silent(duration=max_len - len(v2))
        m1 = m1 + AudioSegment.silent(duration=max_len - len(m1))

        print("    ИИ нарезает вокал (Эксклюзивный Гейт)...")
        
        beat_ms = 60000.0 / bpm1
        phrase_ms = int(beat_ms * 16)
        
        final_vocal = AudioSegment.empty()
        turn = 1
        
        for i in range(0, max_len, phrase_ms):
            chunk_v1 = v1[i:i+phrase_ms]
            chunk_v2 = v2[i:i+phrase_ms]
            
            if len(chunk_v1) == 0: break
            fade_time = min(50, len(chunk_v1)//2)
            
            rms_v1 = chunk_v1.rms
            rms_v2 = chunk_v2.rms
            threshold = 300
            
            if turn == 1:
                if rms_v1 > threshold:
                    final_vocal += chunk_v1.fade_in(fade_time).fade_out(fade_time)
                elif rms_v2 > threshold:
                    final_vocal += chunk_v2.fade_in(fade_time).fade_out(fade_time)
                    turn = 2 
                else:
                    final_vocal += AudioSegment.silent(duration=len(chunk_v1))
                turn = 2
            else:
                if rms_v2 > threshold:
                    final_vocal += chunk_v2.fade_in(fade_time).fade_out(fade_time)
                elif rms_v1 > threshold:
                    final_vocal += chunk_v1.fade_in(fade_time).fade_out(fade_time)
                    turn = 1
                else:
                    final_vocal += AudioSegment.silent(duration=len(chunk_v2))
                turn = 1
                
        final_mix = m1.overlay(final_vocal)
        
        if final_mix.max_dBFS > -1.0:
            final_mix = final_mix.apply_gain(-1.0 - final_mix.max_dBFS)
            
        final_mix.export(output_path, format="mp3", bitrate="320k")
        print(f"✅ Баттл готов: {output_path}")
        return True, output_path
        
    except Exception as e:
        return False, f"Ошибка при создании баттла: {str(e)}"
    finally:
        if is_temp and os.path.exists(stretched_vocal2):
            os.remove(stretched_vocal2)