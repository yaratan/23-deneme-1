"""
CKB Değerlendirme Scripti — Recall / Precision / Gecikme
==========================================================

Kullanım:
    python eval_recall.py test_set.json

test_set.json formatı (bkz. test_set_ornek.json):
[
  {
    "query": "Hz. Ali kimdir",
    "relevant_doc_ids": ["9siniftarih"],
    "relevant_titles": ["HZ. ALİ DÖNEMİ"]   // opsiyonel, daha hassas eşleşme için
  },
  ...
]

Nasıl ground truth hazırlanır:
1. Test etmek istediğiniz 15-30 soruyu yazın (müfredattan çeşitli konular).
2. Her soru için, "doğru cevabın içinde olması gereken" belge/bölümleri
   SİZ elle işaretleyin (kitaptaki ilgili başlığı/sayfayı okuyup karar
   verin). Bu adım otomatikleştirilemez — ground truth'un kalitesi bu
   elle etiketlemenin kalitesine bağlıdır.
3. relevant_doc_ids alanına o belgenin doc_id'sini yazın (engine.list_documents()
   ile görebilirsiniz). Bölüm bazlı hassasiyet istiyorsanız relevant_titles'a
   başlık metninin bir kısmını yazın (script alt string eşleşmesi yapar).

Bu script şunları hesaplar:
- Recall@K: ilgili belgelerin kaçı ilk K sonuç içinde bulundu
- Precision@K: ilk K sonucun kaçı gerçekten ilgiliydi
- Ortalama gecikme (ms)
"""

import json
import sys
import time

from ckb_engine import CKBEngine


def evaluate(test_set_path: str, db_path: str = "./index.db", k: int = 5,
             use_query_expansion: bool = False):
    with open(test_set_path, "r", encoding="utf-8") as f:
        test_set = json.load(f)

    engine = CKBEngine(db_path=db_path, enable_ollama=False)

    total_recall = 0.0
    total_precision = 0.0
    total_latency_ms = 0.0
    rows = []

    for case in test_set:
        query = case["query"]
        relevant_doc_ids = set(case.get("relevant_doc_ids", []))
        relevant_titles = case.get("relevant_titles", [])

        t0 = time.time()
        result = engine.search(query, use_query_expansion=use_query_expansion)
        latency_ms = (time.time() - t0) * 1000

        top_k = result["documents"][:k]

        def is_relevant(doc):
            if doc["path"] in relevant_doc_ids:
                return True
            for rt in relevant_titles:
                if rt.lower() in (doc.get("title") or "").lower():
                    return True
                if rt.lower() in (doc.get("content") or "").lower():
                    return True
            return False

        hits = [d for d in top_k if is_relevant(d)]

        # Recall: en az bir alakalı sonuç bulunduysa 1, yoksa 0
        # (belge/bölüm bazlı tam recall için relevant_doc_ids'i genişletip
        #  kaç TANESİNİN bulunduğunu da sayabilirsiniz — burada basit
        #  "en az biri bulundu mu" recall'u hesaplanıyor)
        recall = 1.0 if hits else 0.0
        precision = len(hits) / len(top_k) if top_k else 0.0

        total_recall += recall
        total_precision += precision
        total_latency_ms += latency_ms

        rows.append({
            "query": query,
            "recall": recall,
            "precision": round(precision, 2),
            "latency_ms": round(latency_ms, 1),
            "top_result": top_k[0]["path"] if top_k else None,
        })

        status = "✅" if recall else "❌"
        print(f"{status} [{latency_ms:6.1f} ms] {query}")

    n = len(test_set)
    print("\n" + "=" * 60)
    print(f"Toplam soru sayısı : {n}")
    print(f"Ortalama Recall@{k} : {total_recall / n:.2%}")
    print(f"Ortalama Precision@{k}: {total_precision / n:.2%}")
    print(f"Ortalama gecikme    : {total_latency_ms / n:.1f} ms")
    print("=" * 60)

    return rows


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Kullanım: python eval_recall.py test_set.json [db_path] [k]")
        sys.exit(1)
    test_path = sys.argv[1]
    db_path = sys.argv[2] if len(sys.argv) > 2 else "./index.db"
    k = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    evaluate(test_path, db_path=db_path, k=k)
