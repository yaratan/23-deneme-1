"""
CKB Metrik Hesaplama — history.db'den Accuracy / F1
======================================================

Ön koşul: Uygulamayı kullanarak birkaç sorgu çalıştırmış olman lazım
(her sorgu otomatik olarak history.db'ye kaydedilir).

Sonra iki şeyden en az birini elle doldurman gerekir (Python ile):

    from ckb_history import HistoryStore
    h = HistoryStore("./history.db")
    for row in h.fetch_recent(50):
        print(row["id"], row["query"], "->", row["answer_text"])
    # id=7 olan kayıt doğruysa:
    h.set_label(7, dogru_mu=1)
    # F1 hesaplamak için referans (altın) cevap da eklenebilir:
    h.set_label(7, referans_cevap="Hz. Ali, Hz. Osman'ın şehit edilmesinden sonra halife seçilmiştir.")

Sonra:
    python compute_metrics.py
"""
import re
import sys
from collections import Counter

from ckb_history import HistoryStore


def _tokens(text: str) -> list:
    return re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE)


def f1_score(pred: str, ref: str) -> float:
    """SQuAD tarzı token-seviyesi F1 (precision/recall kelime kümesi
    örtüşmesinden hesaplanır)."""
    pred_tokens = _tokens(pred)
    ref_tokens = _tokens(ref)
    if not pred_tokens or not ref_tokens:
        return 0.0
    common = Counter(pred_tokens) & Counter(ref_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(ref_tokens)
    return 2 * precision * recall / (precision + recall)


def main(db_path: str = "./history.db"):
    h = HistoryStore(db_path)
    rows = h.fetch_all()
    if not rows:
        print("history.db boş — önce uygulamada birkaç sorgu çalıştır.")
        return

    print(f"Toplam kayıt: {len(rows)}")

    # --- Accuracy: elle 'dogru_mu' etiketlenen kayıtlardan ---
    labeled = [r for r in rows if r["dogru_mu"] is not None]
    if labeled:
        correct = sum(1 for r in labeled if r["dogru_mu"] == 1)
        print(f"\nAccuracy ({len(labeled)} etiketli kayıt): {correct/len(labeled):.2%}")
    else:
        print("\nAccuracy: henüz hiçbir kayıt 'dogru_mu' ile etiketlenmemiş "
              "(bkz. dosyanın üstündeki örnek).")

    # --- F1: 'referans_cevap' girilen kayıtlardan ---
    with_ref = [r for r in rows if r["referans_cevap"]]
    if with_ref:
        f1s = [f1_score(r["answer_text"], r["referans_cevap"]) for r in with_ref]
        print(f"\nOrtalama F1 ({len(with_ref)} referanslı kayıt): {sum(f1s)/len(f1s):.2%}")
        for r, f1 in zip(with_ref, f1s):
            print(f"  #{r['id']:3d} F1={f1:.2%}  \"{r['query']}\"")
    else:
        print("\nF1: henüz hiçbir kayıda 'referans_cevap' girilmemiş.")

    # --- Genel istatistikler ---
    stats = h.stats_summary()
    print("\n--- Genel ---")
    for k, v in stats.items():
        print(f"{k}: {v}")

    # --- Ham vs Akıcı halüsinasyon oranı ---
    akici_rows = [r for r in rows if r["answer_type"] == "akici"]
    if akici_rows:
        toplam_filtrelenen = sum(r["removed_sentence_count"] or 0 for r in akici_rows)
        print(f"\nAkıcı Cevap sorgu sayısı: {len(akici_rows)}")
        print(f"Toplam filtrelenen (olası uydurma) cümle sayısı: {toplam_filtrelenen}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "./history.db")
