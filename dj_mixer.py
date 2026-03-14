import os
import subprocess
import sys

def create_continuous_mix(file_paths, output_filename="dj_mix.wav"):
    if not file_paths or len(file_paths) < 2:
        return False, "Для диджей-микса нужно минимум 2 трека!"

    print(" MixingBear: Встаю за пульт! Синхронизирую BPM и биты...")
    
    # Полный путь к итоговому файлу
    output_path = os.path.abspath(output_filename)
    
    # Путь к скрипту микшера
    mixingbear_script = r"C:\Users\anton\Desktop\music chain\MixingBear\mixer.py"
    
    # 1. ПРЕДОХРАНИТЕЛЬ: Проверяем, существует ли скрипт мишки
    if not os.path.exists(mixingbear_script):
        error_msg = f"Не могу найти скрипт MixingBear по пути: {mixingbear_script}"
        print(f"❌ {error_msg}")
        return False, error_msg

    # 2. ПРЕДОХРАНИТЕЛЬ: Проверяем, существуют ли скачанные треки
    for p in file_paths:
        if not os.path.exists(p):
            error_msg = f"Не найден трек для сведения: {p}"
            print(f"❌ {error_msg}")
            return False, error_msg
            
    try:
        # sys.executable — это абсолютный путь к твоему python.exe
        # Флаг "-X utf8" заставляет Windows понимать эмодзи и любые символы!
        command = [sys.executable, "-X", "utf8", mixingbear_script]
        command.extend(file_paths)
        command.extend(["-o", output_path])
        
        print(f"🔧 Отладочная команда: {' '.join(command)}")
        
        # Запускаем сведение!
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True,
            errors="replace" # <--- ИСПРАВЛЕНИЕ ЗДЕСЬ (Не даст боту упасть из-за кодировки Windows)
        )
        
        print(f"✅ MixingBear закончил сведение: {output_path}")
        return True, output_path
        
    except subprocess.CalledProcessError as e:
        # Если сам скрипт mixer.py упал с ошибкой, выводим её
        error_output = e.stderr if e.stderr else e.stdout
        print(f"❌ Ошибка внутри MixingBear:\n{error_output}")
        return False, f"Ошибка внутри MixingBear:\n{error_output}"
        
    except Exception as e:
        # Если упала сама система
        print(f"❌ Системная ошибка: {e}")
        return False, str(e)