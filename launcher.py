"""
CKB Launcher
============
Bu script CKB'yi başlatır: sanal ortamı bulur, Streamlit sunucusunu arka
planda çalıştırır, tarayıcıyı otomatik açar. PyInstaller ile gerçek bir
.exe dosyasına dönüştürülmek için tasarlanmıştır (aşağıdaki BUILD_EXE.md'ye
bakın).
"""
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_FILE = APP_DIR / "app.py"
PORT = 8501


def main():
    os.chdir(APP_DIR)

    print("CKB başlatılıyor, lütfen bekleyin...")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(APP_FILE),
         "--server.port", str(PORT), "--server.headless", "true"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    time.sleep(4)  # Streamlit'in ayağa kalkması için kısa bekleme
    webbrowser.open(f"http://localhost:{PORT}")

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


if __name__ == "__main__":
    main()
