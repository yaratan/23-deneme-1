@echo off
REM ============================================================
REM CKB (Curriculum Knowledge Base) - Cift tikla baslatici
REM Bu dosyayi diger .py dosyalariyla AYNI klasore koyun ve
REM cift tiklayin. PowerShell komutu yazmaniza gerek kalmaz.
REM ============================================================

cd /d "%~dp0"

REM Sanal ortam yoksa olustur (ilk calistirmada bir kere)
if not exist "venv\" (
    echo [1/3] Ilk kurulum yapiliyor, bu biraz surebilir...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

echo [2/3] Node.js bagimliliklari kontrol ediliyor...
if not exist "node_modules\" (
    call npm install
)

echo [3/3] CKB baslatiliyor... Tarayici otomatik acilacak.
streamlit run app.py

pause
