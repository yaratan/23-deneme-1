# CKB'yi Çift Tıklanabilir .exe Yapmak

## Önemli, dürüst bir uyarı
Gerçek bir Windows `.exe` dosyası SADECE Windows üzerinde, PyInstaller ile
üretilebilir — bu ortamda (Linux) senin için derleyip veremem. Ama süreç
5 dakika sürer ve sonrasında `CKB.exe`'ye çift tıklayarak açarsın.

İki seçenek var, ikisi de dosyalar arasında:

## Seçenek A — Basit ve dayanıklı: `CKB_Baslat.bat` (önerilen)
Zaten paylaştım. Çift tıklayınca sanal ortamı kurar/kullanır, Streamlit'i
başlatır, tarayıcı otomatik açılır. **PowerShell açmana hiç gerek yok.**
`.bat` dosyaları teknik olarak `.exe` değildir ama davranış olarak
aynıdır (çift tık → çalışır) ve PyInstaller'dan çok daha az sorun çıkarır
(Streamlit'in dinamik import yapısı PyInstaller ile bazen çakışabiliyor).

Windows'ta `.bat` ikonunu değiştirip gerçek bir `.exe` gibi göstermek
istersen: [Bat To Exe Converter](https://www.f2ko.de/programs/bat-to-exe-converter/)
gibi ücretsiz bir araçla `.bat`'ı saniyeler içinde `.exe`'ye sarabilirsin
— kod hiç değişmez, sadece görünüm/simge değişir.

## Seçenek B — Gerçek PyInstaller .exe
Windows makinende, proje klasöründe:

```powershell
pip install pyinstaller
pyinstaller --onefile --name CKB --add-data "app.py;." --add-data "ckb_engine.py;." --add-data "es_anlamli.json;." --add-data "get_synonyms.js;." --add-data "index.db;." launcher.py
```

Bu `dist\CKB.exe` üretir. Çift tıklayınca `launcher.py` çalışır, Streamlit'i
arka planda başlatır, tarayıcıyı açar.

**Bilinen risk:** Streamlit + PyInstaller kombinasyonu bazı Streamlit
sürümlerinde "ModuleNotFoundError" ile başarısız olabiliyor (Streamlit
runtime'ı dinamik olarak modül yüklüyor, PyInstaller bunu bazen göremiyor).
Eğer `CKB.exe` çalışmazsa hata mesajını `dist\CKB.exe` yerine terminalden
`python launcher.py` ile çalıştırıp gör, sorunu orada teşhis etmek daha
kolay. Bu yüzden Seçenek A'yı (`.bat`) birincil çözüm olarak öneriyorum —
daha az hareketli parça var, daha az kırılıyor.
