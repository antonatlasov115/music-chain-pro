import os
import subprocess

def extract_minus(file_path, base_output_dir):
    print(f" Demucs: Начинаю изоляцию вокала для {os.path.basename(file_path)}...")
    stems_dir = os.path.join(base_output_dir, "Stems")
    os.makedirs(stems_dir, exist_ok=True)
    
    cmd = [
        "demucs",
        "--two-stems=vocals",
        "--mp3",
        "-n", "htdemucs",
        "-o", stems_dir,
        file_path
    ]
    
    #  МАГИЯ ЗДЕСЬ: Заставляем Windows понимать любые символы (даже эмодзи)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    
    try:
        # Передаем наш env в subprocess
        subprocess.run(cmd, check=True, capture_output=True, text=True, encoding="utf-8", env=env)
        
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        result_folder = os.path.join(stems_dir, "htdemucs", base_name)
        
        minus_path = os.path.join(result_folder, "no_vocals.mp3")
        vocals_path = os.path.join(result_folder, "vocals.mp3")
        
        if os.path.exists(minus_path) and os.path.exists(vocals_path):
            print(f"✅ Demucs успешно разделил трек: {base_name}")
            return minus_path, vocals_path
        else:
            return None, None
            
    except Exception as e:
        print(f"❌ Ошибка нейросети Demucs: {e}")
        return None, None