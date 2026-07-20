"""
CKB Sorgu Geçmişi Veritabanı
==============================
Her sorguyu, üretilen cevabı (Ham/Akıcı), kaynakları, süre ölçümlerini ve
sistem kaynak kullanımını SQLite'a (history.db) kaydeder.

Bunu şunlar için kullanabilirsin:
- Accuracy/F1 gibi metrikleri hesaplamak: her kayda elle "dogru_mu" (1/0)
  ve/veya "referans_cevap" (senin yazdığın doğru cevap) etiketi ekleyip
  compute_metrics.py ile özetleyebilirsin.
- Gecikme (latency) analizleri: timings_json içinde her aşamanın süresi var.
- Sistem kaynak kullanımı analizleri: resources_json içinde RAM/CPU var.
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


class HistoryStore:
    def __init__(self, db_path: str = "./history.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _conn(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def _init_db(self):
        with self._conn() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS query_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    query TEXT NOT NULL,
                    answer_type TEXT NOT NULL,       -- 'ham' | 'akici'
                    answer_text TEXT,
                    sources_json TEXT,                -- kullanılan kaynakların [{path, line}, ...] listesi
                    timings_json TEXT,                 -- {"treesearch_ms": ..., "expansion_ms": ..., ...}
                    resources_json TEXT,               -- {"rss_mb": ..., "cpu_percent": ..., ...}
                    grounding_json TEXT,               -- akıcı cevap için doğrulama sonucu (varsa)
                    removed_sentence_count INTEGER,    -- akıcı cevaptan filtrelenen uydurma cümle sayısı
                    referans_cevap TEXT,               -- (elle doldurulur) F1 hesaplamak için doğru cevap
                    dogru_mu INTEGER                   -- (elle doldurulur) NULL=etiketlenmedi, 1=doğru, 0=yanlış
                )
            """)
            con.execute("CREATE INDEX IF NOT EXISTS idx_query_log_ts ON query_log(timestamp)")

    def log(self, query: str, answer_type: str, answer_text: str = None,
            sources=None, timings=None, resources=None, grounding=None,
            removed_sentence_count: int = 0) -> int:
        with self._conn() as con:
            cur = con.execute(
                """INSERT INTO query_log
                   (timestamp, query, answer_type, answer_text, sources_json,
                    timings_json, resources_json, grounding_json,
                    removed_sentence_count, referans_cevap, dogru_mu)
                   VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL)""",
                (
                    time.time(), query, answer_type, answer_text,
                    json.dumps(sources or [], ensure_ascii=False),
                    json.dumps(timings or {}, ensure_ascii=False),
                    json.dumps(resources or {}, ensure_ascii=False),
                    json.dumps(grounding, ensure_ascii=False) if grounding else None,
                    removed_sentence_count,
                ),
            )
            return cur.lastrowid

    def set_label(self, row_id: int, dogru_mu: int = None, referans_cevap: str = None):
        """Bir kayda elle doğru/yanlış etiketi ve/veya referans (altın)
        cevap ekler — F1/Accuracy hesaplamak için gereklidir."""
        fields, values = [], []
        if dogru_mu is not None:
            fields.append("dogru_mu = ?")
            values.append(dogru_mu)
        if referans_cevap is not None:
            fields.append("referans_cevap = ?")
            values.append(referans_cevap)
        if not fields:
            return
        values.append(row_id)
        with self._conn() as con:
            con.execute(f"UPDATE query_log SET {', '.join(fields)} WHERE id = ?", values)

    def fetch_recent(self, limit: int = 20) -> list:
        with self._conn() as con:
            rows = con.execute(
                "SELECT * FROM query_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def fetch_all(self) -> list:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM query_log ORDER BY id ASC").fetchall()
            return [dict(r) for r in rows]

    def stats_summary(self) -> dict:
        """Hızlı özet: ortalama gecikme, ortalama RAM, toplam sorgu sayısı vb."""
        rows = self.fetch_all()
        if not rows:
            return {}
        latencies = []
        rams = []
        for r in rows:
            try:
                t = json.loads(r["timings_json"] or "{}")
                res = json.loads(r["resources_json"] or "{}")
                if "treesearch_ms" in t:
                    latencies.append(t["treesearch_ms"])
                if "rss_mb" in res:
                    rams.append(res["rss_mb"])
            except Exception:
                pass
        return {
            "toplam_sorgu": len(rows),
            "ortalama_treesearch_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "ortalama_ram_mb": round(sum(rams) / len(rams), 1) if rams else None,
            "etiketlenen_kayit": sum(1 for r in rows if r["dogru_mu"] is not None),
        }
