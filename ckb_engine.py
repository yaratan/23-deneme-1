"""
CKB (Curriculum Knowledge Base) - Ana Arama Motoru
====================================================

Boru hattı: yazım düzeltme -> stopwords temizleme -> eş anlamlı genişletme
(TDK + Ollama) -> wildcard ekleme -> TreeSearch (tree mode, FTS5).

Ollama ve Node.js/TDK entegrasyonları OPSİYONELDİR: ikisi de çalışmıyorsa
veya kurulu değilse motor sessizce devre dışı bırakır ve düz sorguyla
aramaya devam eder. Yani bu iki bağımlılık eksik olsa bile "Ham Cevap"
özelliği her zaman çalışır.
"""

import concurrent.futures
import json
import logging
import re
import subprocess
import time
from pathlib import Path

import psutil
from treesearch import TreeSearch

logger = logging.getLogger("ckb_engine")

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

STOPWORDS = {
    # Türkçe
    "ne", "nedir", "nasıl", "niçin", "niye", "kim", "kimdir", "nerede",
    "hangi", "mi", "mı", "mu", "mü", "ile", "ve", "veya", "bir", "bu",
    "şu", "o", "de", "da", "ki", "için", "gibi", "olan", "olarak",
    # İngilizce
    "what", "is", "the", "of", "for", "on", "at", "which", "where",
    "when", "why", "how", "a", "an", "and", "or",
}

# Node.js betiğinin bulunduğu dizin (bu dosyayla aynı klasörde varsayılır)
_SCRIPT_DIR = Path(__file__).resolve().parent
_SYNONYM_SCRIPT = _SCRIPT_DIR / "get_synonyms.js"

_OLLAMA_MODEL = "llama3.2:3b"

# Cümle sonu gibi görünen ama olmayan yaygın Türkçe kısaltmalar
_ABBREVIATIONS = {"hz", "dr", "prof", "doç", "sn", "vb", "vs", "yy", "m.ö", "m.s", "no"}


def _clean_markdown(text: str) -> str:
    """Kaynak node içeriğindeki markdown gürültüsünü (başlık #, madde
    işaretleri, numaralı liste 1)/2), kalın/italik *, tablo | çizgileri)
    temizler VE her orijinal satırı kendi cümlesi olarak korur (satır
    sonuna nokta yoksa ekler). Bu son kısım kritik: kaynaktaki madde
    işaretli listeler çoğu zaman nokta ile bitmiyor — bu yüzden temizlik
    sonrası hepsi TEK DEV CÜMLEYE birleşip alakasız maddelerin (örn.
    'Pankuş' sorusuna Asurlular/Yunanlılar hakkındaki alakasız cümlelerin
    de) cevaba karışmasına yol açıyordu. Satır bazında nokta eklemek her
    maddeyi ayrı, filtrelenebilir bir cümle haline getirir."""
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[\-\*\u2022]+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+[\.\)]\s*", "", text, flags=re.MULTILINE)  # "1) " / "1. " madde no'ları
    text = re.sub(r"\*{1,3}", "", text)  # kalın/italik işaretleri (** / *)
    text = re.sub(r"-{3,}", " ", text)
    text = text.replace("|", " ")

    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    fixed_lines = []
    for ln in lines:
        if ln[-1] not in ".!?:":
            ln += "."
        fixed_lines.append(ln)
    text = " ".join(fixed_lines)

    text = re.sub(r"\s+", " ", text).strip()
    return text


def _split_sentences(text: str) -> list:
    """Kısaltmaları (Hz., Dr., ...) yanlışlıkla cümle sonu saymayan basit
    cümle bölücü."""
    raw_parts = re.split(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ0-9])", text)
    merged = []
    for part in raw_parts:
        if merged:
            last_word = merged[-1].rstrip(".!?").split()[-1].lower() if merged[-1].split() else ""
            if last_word in _ABBREVIATIONS:
                merged[-1] = merged[-1] + " " + part
                continue
        merged.append(part)
    return merged


# Cümle başında büyük harfle başlayan ama özel isim OLMAYAN yaygın Türkçe
# kelimeler — bunları "entity" (kişi/yer adı) sanmamak için filtreliyoruz.
_NON_ENTITY_STARTERS = {
    "bu", "şu", "o", "ancak", "ayrıca", "bunun", "bunlar", "böylece",
    "sonuç", "sonra", "daha", "her", "bazı", "diğer", "aynı", "yine",
    "ama", "fakat", "çünkü", "eğer", "hatta", "genellikle", "özellikle",
}


def _capitalized_candidates(sentence: str) -> set:
    """Bir cümledeki büyük harfle başlayan TÜM adayları döner (pozisyon
    fark etmeksizin — cümle başı dahil). Bilinen 'entity olmayan' başlangıç
    kelimeleri (Bu, Ancak, ...) ve stopwords hariç tutulur."""
    words = re.findall(r"[\wÇĞİÖŞÜçğıöşü]+", sentence, flags=re.UNICODE)
    return {
        w for w in words
        if len(w) >= 3 and w[0].isupper()
        and w.lower() not in _NON_ENTITY_STARTERS
        and w.lower() not in STOPWORDS
    }


def _entity_pairs(entities: set) -> set:
    ents = sorted(entities)
    return {frozenset((ents[i], ents[j]))
            for i in range(len(ents)) for j in range(i + 1, len(ents))}


def _extract_anchor_terms(query: str, flat_results: list) -> set:
    """Sorgudaki kelimelerden, KORPUSTA (o an dönen belgelerde) özel isim
    olarak geçenleri 'çapa terim' (anchor term) olarak işaretler.

    NEDEN GEREKLİ: "gordion nerenin başkenti" gibi bir sorguda "başkenti"
    kelimesi onlarca belgede (Kudüs, Ninova, Gordion, Roma...) geçebilir
    ve tek başına ayırt edici değildir. Ama "Gordion" korpusta özel isim
    olarak geçiyorsa ve sorguda da geçiyorsa, bu SORUNUN ASIL ÖZNESİDİR
    ve diğer belgeler bu belgenin önüne geçmemelidir.

    Kullanıcı sorguyu tamamen küçük harfle yazsa bile ("gordion...") bu
    fonksiyon çalışır: karşılaştırma sorgu tarafında case-insensitive
    yapılır, korpustaki YAZIMA (büyük harfli haline) göre eşleşme aranır.
    _capitalized_candidates zaten bilinen isim-olmayan kelimeleri
    (STOPWORDS, _NON_ENTITY_STARTERS) eliyor, o yüzden burada ek bir
    filtre gerekmiyor."""
    corpus_entities = set()
    for doc in flat_results:
        content = _clean_markdown(doc.get("content", ""))
        for sent in _split_sentences(content):
            corpus_entities |= _capitalized_candidates(sent)

    corpus_entities_lower = {e.lower(): e for e in corpus_entities}
    query_terms = {t.lower() for t in re.findall(r"\w+", query, flags=re.UNICODE)}
    return {corpus_entities_lower[t] for t in query_terms if t in corpus_entities_lower}


def _rerank_by_anchor_terms(flat_results: list, anchors: set) -> tuple:
    """anchors boşsa (sorguda korpusla eşleşen özel isim yoksa) dokunmaz.
    Varsa: içeriğinde en az bir anchor terim GEÇEN belgeleri öne alır
    (case-sensitive arama — "Gordion" ile "gordion" karışmasın diye
    korpustaki orijinal yazımı kullanıyoruz). Eşleşmeyenler tamamen
    silinmez, sona itilir (Kaynak Metinler sekmesinde hâlâ görünsünler,
    şeffaflık için).

    Döner: (yeniden_sıralı_liste, kanıt_var_mı: bool). kanıt_var_mı
    False ise HİÇBİR belgede anchor terim geçmiyor demektir — bu durumda
    rewrite_text çağrılmadan önce sonucu kontrol edin (bkz. rewrite_text
    içindeki has_sufficient_evidence parametresi)."""
    if not anchors:
        return flat_results, True

    matched, unmatched = [], []
    for doc in flat_results:
        content = doc.get("content", "")
        (matched if any(a in content for a in anchors) else unmatched).append(doc)

    if matched:
        return matched + unmatched, True
    return flat_results, False


def _try_import_ollama():
    try:
        import ollama  # noqa: F401
        return ollama
    except ImportError:
        logger.warning("ollama paketi bulunamadı (pip install ollama). "
                        "Yazım düzeltme ve akıcı cevap devre dışı.")
        return None


def _load_local_synonym_lexicon(json_path: Path) -> dict:
    """es_anlamli.json (phpMyAdmin JSON export formatı) dosyasını okuyup
    {kelime: [eş anlamlı, ...]} sözlüğüne çevirir. Tamamen yerelde çalışır,
    ağ isteği veya alt süreç YOKTUR -> sorgu başına maliyeti sıfıra yakındır
    (tek seferlik yükleme motor başlatılırken yapılır)."""
    if not json_path.exists():
        logger.warning("Yerel eş anlamlı sözlüğü bulunamadı: %s", json_path)
        return {}
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        # phpMyAdmin export formatı: [header, database, {"data": [...]}]
        records = None
        for item in raw:
            if isinstance(item, dict) and "data" in item:
                records = item["data"]
                break
        if records is None:
            records = raw  # düz liste formatına da izin ver

        lexicon = {}
        for r in records:
            word = (r.get("kelime") or "").strip().lower()
            if not word:
                continue
            syns = []
            for key in ("esanlam", "esanlam2", "esanlam3", "esanlam4"):
                val = (r.get(key) or "").strip()
                if val:
                    syns.append(val)
            if syns:
                lexicon[word] = syns
        logger.info("Yerel eş anlamlı sözlüğü yüklendi: %d kelime", len(lexicon))
        return lexicon
    except Exception as e:
        logger.warning("Yerel eş anlamlı sözlüğü okunamadı: %s", e)
        return {}


def _read_source_lines(source_path: str, line_start, line_end) -> str:
    """Node özeti (summary) pytreesearch tarafından KIRPILMIŞ olabilir
    (uzun bölümlerde "..." ile ortası atılabiliyor — tam olarak Ali/Yezid
    hatasına yol açan şey buydu: özne cümleden düşmüştü). Bu yüzden LLM'e
    bağlam olarak vermeden önce, mümkünse asıl kaynak dosyadan TAM satırları
    okuyoruz. Dosya bulunamazsa (taşınmış/silinmiş) None döner, çağıran
    taraf summary'ye geri düşer."""
    if not source_path or not line_start or not line_end:
        return None
    try:
        with open(source_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        start = max(0, int(line_start) - 1)
        end = min(len(lines), int(line_end))
        if start >= end:
            return None
        return "".join(lines[start:end])
    except Exception:
        return None


class CKBEngine:
    def __init__(self, db_path="./index.db",
                 enable_ollama=True,
                 enable_tdk_synonyms=True,
                 synonym_json_path="./es_anlamli.json",
                 ollama_model=_OLLAMA_MODEL):
        self.db_path = db_path
        self.ollama_model = ollama_model

        # SQLite/TreeSearch thread güvenliği: tüm arama çağrılarını TEK bir
        # worker thread'de sırayla çalıştırıyoruz.
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._process = psutil.Process()
        self._process.cpu_percent(interval=None)  # ilk çağrı referans alır, göz ardı edilir

        self._ollama = _try_import_ollama() if enable_ollama else None
        self._ollama_available = self._ollama is not None and self._check_ollama_alive()
        self.last_rewrite_used_fallback = False

        # Yerel eş anlamlı sözlüğü: TEK SEFERLİK yükleme, sorgu başına maliyet
        # yok. TDK online / Node.js artık genişletme için ZORUNLU DEĞİL.
        self._synonym_lexicon = _load_local_synonym_lexicon(Path(synonym_json_path))

        # TDK online (Node.js) artık sadece yerel sözlükte kelime bulunamazsa
        # devreye giren OPSİYONEL bir yedek; varsayılan olarak kapalı tutmak
        # istersen enable_tdk_synonyms=False verebilirsin.
        self.enable_tdk_synonyms = enable_tdk_synonyms and _SYNONYM_SCRIPT.exists()
        if enable_tdk_synonyms and not _SYNONYM_SCRIPT.exists():
            logger.warning("get_synonyms.js bulunamadı: %s", _SYNONYM_SCRIPT)

    def close(self):
        self.executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------
    # Ollama yardımcıları
    # ------------------------------------------------------------------

    def _check_ollama_alive(self):
        """Ollama servisi ayakta mı ve model yüklü mü, hızlıca kontrol et."""
        try:
            self._ollama.list()
            return True
        except Exception as e:
            logger.warning("Ollama servisine ulaşılamadı: %s", e)
            return False

    def correct_spelling(self, query: str) -> str:
        """Ollama ile yazım düzeltme. Başarısız olursa orijinal sorguyu döner."""
        if not self._ollama_available:
            return query
        try:
            prompt = (
                "Aşağıdaki arama sorgusundaki yazım hatalarını düzelt. "
                "SADECE düzeltilmiş sorguyu yaz, başka hiçbir şey ekleme, "
                "tırnak işareti kullanma:\n\n" + query
            )
            resp = self._ollama.chat(
                model=self.ollama_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0},
                keep_alive="30m",
            )
            corrected = resp["message"]["content"].strip().strip('"').strip()
            return corrected if corrected else query
        except Exception as e:
            logger.warning("Yazım düzeltme başarısız: %s", e)
            return query

    def get_synonyms_from_ollama(self, word: str, limit: int = 3) -> list:
        """Ollama'dan tek kelimelik eş anlamlılar iste (JSON dizi olarak)."""
        if not self._ollama_available:
            return []
        try:
            prompt = (
                f'"{word}" kelimesinin Türkçedeki en fazla {limit} eş anlamlısını '
                'JSON dizisi olarak ver. SADECE JSON dizisi yaz, başka hiçbir '
                'metin ekleme. Örnek: ["kelime1", "kelime2"]'
            )
            resp = self._ollama.chat(
                model=self.ollama_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0},
                keep_alive="30m",
            )
            content = resp["message"]["content"].strip()
            content = re.sub(r"^```(json)?|```$", "", content, flags=re.MULTILINE).strip()
            synonyms = json.loads(content)
            if isinstance(synonyms, list):
                return [str(s) for s in synonyms][:limit]
            return []
        except Exception as e:
            logger.warning("Ollama eş anlamlı üretimi başarısız (%s): %s", word, e)
            return []

    def rewrite_text(self, query: str, raw_nodes: list,
                      max_nodes: int = 3, max_chars_per_node: int = 800,
                      max_output_tokens: int = 400,
                      has_sufficient_evidence: bool = True) -> str:
        """
        Akıcı Cevap üretimi — Ollama ne kadar sürerse sürsün SONUNA KADAR
        beklenir (zaman aşımı YOK, kullanıcı isteği üzerine kaldırıldı).
        Ollama tamamen kullanılamıyorsa (kurulu değil/çalışmıyor), LLM
        kullanmadan Ham Cevap'tan bir alternatif üretilir — ama bu SADECE
        Ollama'ya hiç ERİŞİLEMEDİĞİNDE devreye girer, yavaş olduğu için değil.

        has_sufficient_evidence: search()'ün döndürdüğü
        "has_sufficient_evidence" değerini buraya geçirin. False ise
        (sorgudaki özel isim hiçbir belgede geçmiyorsa) LLM'i HİÇ
        ÇAĞIRMADAN doğrudan "yeterli bilgi yok" döneriz — hem gereksiz
        bekleme süresini keser hem de LLM'in yanlış belgeden (örn.
        "Gordion" yerine "Kudüs" belgesinden) cevap uydurmasını en
        baştan engeller.
        """
        self.last_rewrite_used_fallback = False
        if not raw_nodes:
            return None
        if not has_sufficient_evidence:
            return "Bu konuda kaynaklarda yeterli ve net bilgi bulunmamaktadır."

        if self._ollama_available:
            try:
                context = "\n\n---\n\n".join(
                    f"[Kaynak: {n['path']}] {n['content'][:max_chars_per_node]}"
                    for n in raw_nodes[:max_nodes]
                )
                prompt = (
                    "Aşağıdaki kaynak metinlere DAYANARAK, kullanıcının sorusuna "
                    "Türkçe, akıcı ve doğal bir cevap yaz. Kaynakta tarih veya "
                    "önemli olay bilgisi VARSA belirt; yoksa uydurma. SADECE "
                    "verilen kaynaklardaki bilgiyi kullan, hiçbir şey ekleme, "
                    "kaynakta açıkça belirtilmeyen kişi/olay ilişkileri KURMA. "
                    "Kaynaklarda cevap yoksa 'Bu konuda kaynaklarda yeterli bilgi "
                    "bulunamadı.' yaz.\n\n"
                    f"Soru: {query}\n\nKaynaklar:\n{context}\n\nCevap:"
                )
                resp = self._ollama.chat(
                    model=self.ollama_model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0.0, "num_predict": max_output_tokens, "num_ctx": 4096},
                    keep_alive="30m",
                )
                return resp["message"]["content"].strip()
            except Exception as e:
                logger.warning("Akıcı cevap üretimi başarısız, LLM'siz alternatife geçiliyor: %s", e)

        # --- Ollama tamamen kullanılamıyor: LLM'siz alternatif ---
        self.last_rewrite_used_fallback = True
        extractive = self.build_extractive_answer(query, raw_nodes, max_sentences=4, max_docs=2)
        if not extractive or not extractive.get("summary"):
            return "Bu konuda kaynaklarda yeterli bilgi bulunamadı."
        return extractive["summary"]

    def check_grounding(self, answer_text: str, raw_nodes: list, min_overlap: int = 2) -> list:
        """
        Akıcı Cevap tamamen halüsinasyonsuz OLAMAZ (üretken bir LLM
        kullanıyor) — ama iki katmanlı bir doğrulama ile riski ciddi
        şekilde azaltabiliriz:

        1) Kelime örtüşmesi: cümlenin kaynak metinlerle konu örtüşmesi var mı.
        2) İLİŞKİ DOĞRULAMASI (asıl kritik katman): cümlede iki veya daha
           fazla özel isim (kişi/yer adı) birlikte geçiyorsa — örn. "Ali'nin
           oğlu Yezid..." — bu isim ÇİFTİNİN kaynak metinde AYNI CÜMLE
           içinde birlikte geçip geçmediği kontrol edilir. Geçmiyorsa,
           LLM muhtemelen iki ayrı yerde geçen iki gerçek ismi birbirine
           YANLIŞ BAĞLAMIŞTIR (tam olarak "Ali'nin oğlu Yezid" hatası budur:
           Ali ve Yezid kaynakta ayrı cümlelerde geçiyor, LLM ikisini
           birbirine yanlış bağladı). Bu durumda cümle "grounded=False"
           olur ve arayüz bunu GÖSTERMEZ.

        Döner: [{"text", "grounded", "overlap", "relation_verified"}, ...]
        """
        if not answer_text or not raw_nodes:
            return []

        source_sentences = []
        source_terms = set()
        for n in raw_nodes[:5]:
            content = _clean_markdown(n.get("content", ""))
            for s in _split_sentences(content):
                source_sentences.append(s)
            source_terms |= {t.lower() for t in re.findall(r"\w+", content, flags=re.UNICODE)}

        # 1) Kaynaktaki TÜM cümlelerden (pozisyon fark etmeksizin) bilinen
        #    özel isim adaylarını topla -> "bilinen isim sözlüğü"
        known_entities = set()
        for s in source_sentences:
            known_entities |= _capitalized_candidates(s)

        # 2) Bu sözlüğe göre, kaynak cümlelerin HER BİRİNDE hangi isimlerin
        #    BİRLİKTE geçtiğini kaydet
        cooccurring_pairs = set()
        for s in source_sentences:
            ents_here = _capitalized_candidates(s) & known_entities
            cooccurring_pairs |= _entity_pairs(ents_here)

        results = []
        for sent in _split_sentences(answer_text):
            sent_clean = sent.strip()
            if len(sent_clean) < 15:
                continue
            sent_terms = {t.lower() for t in re.findall(r"\w+", sent_clean, flags=re.UNICODE)
                          if t.lower() not in STOPWORDS}
            overlap = len(sent_terms & source_terms)

            # 3) Cevap cümlesindeki isimleri de AYNI bilinen-isim sözlüğüyle
            #    kesiştir (pozisyon bağımsız — "Ali'nin oğlu..." cümlesinde
            #    "Ali" cümle başında olsa bile artık doğru şekilde
            #    yakalanıyor, önceki sürümdeki hata buydu)
            entities = _capitalized_candidates(sent_clean) & known_entities
            pairs_in_sentence = _entity_pairs(entities)
            relation_verified = pairs_in_sentence.issubset(cooccurring_pairs) if pairs_in_sentence else True

            grounded = (overlap >= min_overlap) and relation_verified
            results.append({
                "text": sent_clean,
                "grounded": grounded,
                "overlap": overlap,
                "relation_verified": relation_verified,
            })
        return results

    # ------------------------------------------------------------------
    # TDK yardımcısı (Node.js alt süreci)
    # ------------------------------------------------------------------

    def get_synonyms_from_tdk(self, word: str, timeout: float = 5.0) -> list:
        if not self.enable_tdk_synonyms:
            return []
        try:
            result = subprocess.run(
                ["node", str(_SYNONYM_SCRIPT), word],
                capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode != 0:
                logger.warning("TDK betiği hata kodu döndürdü (%s): %s",
                                word, result.stderr.strip())
                return []
            return json.loads(result.stdout.strip() or "[]")
        except subprocess.TimeoutExpired:
            logger.warning("TDK betiği zaman aşımına uğradı: %s", word)
            return []
        except Exception as e:
            logger.warning("TDK eş anlamlı sorgusu başarısız (%s): %s", word, e)
            return []

    # ------------------------------------------------------------------
    # Yerel eş anlamlı sözlüğü
    # ------------------------------------------------------------------

    def get_synonyms_local(self, word: str, limit: int = 4) -> list:
        """Bellekteki sözlükten anında (ağ isteği YOK) eş anlamlı döner."""
        return self._synonym_lexicon.get(word.strip().lower(), [])[:limit]

    # ------------------------------------------------------------------
    # Sorgu genişletme
    # ------------------------------------------------------------------

    def expand_query(self, query: str, max_synonym_terms: int = 4,
                      use_tdk_fallback: bool = False,
                      use_ollama_fallback: bool = False) -> str:
        """
        Stopwords temizle, eş anlamlıları EKLE.

        Öncelik sırası: (1) yerel es_anlamli.json sözlüğü — anında, ağ
        isteği yok, sorgu hızını ETKİLEMEZ. (2) `use_tdk_fallback=True`
        verilirse ve kelime yerel sözlükte yoksa TDK online API'ye (Node.js
        alt süreci üzerinden) sorulur — bu yavaştır, varsayılan kapalı.
        (3) `use_ollama_fallback=True` ise Ollama'ya sorulur — en yavaş
        seçenek, varsayılan kapalı.
        """
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)
        keep = [t for t in tokens if t.lower() not in STOPWORDS]
        if not keep:
            keep = tokens  # her şey stopword ise orijinali koru

        extra_terms = []
        needs_fallback = []
        for token in keep:
            syns = self.get_synonyms_local(token)
            if syns:
                for s in syns:
                    if s.lower() != token.lower() and len(extra_terms) < max_synonym_terms:
                        extra_terms.append(s)
            else:
                needs_fallback.append(token)

        # Yerel sözlükte bulunamayan kelimeler için opsiyonel yavaş yedekler
        if use_tdk_fallback and self.enable_tdk_synonyms and needs_fallback:
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(needs_fallback))) as pool:
                futures = {pool.submit(self.get_synonyms_from_tdk, t, 2.0): t for t in needs_fallback}
                for fut in concurrent.futures.as_completed(futures, timeout=3.0):
                    token = futures[fut]
                    try:
                        syns = fut.result()
                    except Exception:
                        syns = []
                    for s in syns:
                        s = s.strip()
                        if s and s.lower() != token.lower() and len(extra_terms) < max_synonym_terms:
                            extra_terms.append(s)
        elif use_ollama_fallback and needs_fallback:
            for token in needs_fallback[:max_synonym_terms]:
                for s in self.get_synonyms_from_ollama(token):
                    if s.strip() and len(extra_terms) < max_synonym_terms:
                        extra_terms.append(s.strip())

        expanded_terms = keep + extra_terms
        return " ".join(expanded_terms) if expanded_terms else query

    # ------------------------------------------------------------------
    # Uydurmasız (extractive) cevap — LLM KULLANMAZ
    # ------------------------------------------------------------------

    def build_extractive_answer(self, query: str, flat_results: list,
                                 max_sentences: int = 6, max_docs: int = 3) -> dict:
        """Ham Cevap - Özet paragraf + Detaylı cümleler"""
        if not flat_results:
            return {"summary": "", "details": []}

        query_terms = {
            t.lower() for t in re.findall(r"\w+", query, flags=re.UNICODE)
            if t.lower() not in STOPWORDS
        }

        all_relevant_sentences = []
        docs_used = set()

        for doc in flat_results[:max_docs]:
            if doc["path"] in docs_used:
                continue
            content = doc.get("content") or ""
            content = _clean_markdown(content)
            sentences = _split_sentences(content)

            for sent in sentences:
                sent_clean = sent.strip()
                # 15'e düşürüldü: başlık/tarih satırları (örn. "HZ. ALİ DÖNEMİ
                # (656-661)") kısa ama önemli — eski 30 karakter eşiği bunları
                # eleyip tarihlerin cevaptan kaybolmasına sebep oluyordu.
                if len(sent_clean) < 15:
                    continue
                sent_terms = {t.lower() for t in re.findall(r"\w+", sent_clean, flags=re.UNICODE)}
                overlap = len(query_terms & sent_terms)
                if overlap >= 1:
                    # Tarih (yıl/yıl aralığı) içeren cümlelere öncelik puanı —
                    # müfredat sorularında "ne zaman" bilgisi kritik önemde.
                    has_date = bool(re.search(r"\b\d{3,4}\b", sent_clean))
                    all_relevant_sentences.append({
                        "text": sent_clean,
                        "overlap": overlap + (2 if has_date else 0),
                        "doc_score": doc.get("score", 0),
                        "source": self._format_location(doc),
                    })

            docs_used.add(doc["path"])

        if not all_relevant_sentences:
            return {"summary": "Bu konuda yeterli bilgi bulunamadı.", "details": []}

        all_relevant_sentences.sort(key=lambda x: (x["overlap"], x["doc_score"]), reverse=True)

        # Özet Paragraf
        top_sentences = all_relevant_sentences[:4]
        summary_parts = [s["text"].rstrip(".!?") for s in top_sentences]
        summary = ". ".join(summary_parts) + "."

        # Detaylar
        details = all_relevant_sentences[:max_sentences]

        return {
            "summary": summary,
            "details": [{"text": d["text"], "source": d["source"]} for d in details]
        }
      
    @staticmethod
    def _format_location(doc: dict) -> str:
        """Bir sonucun tam kaynak konumunu okunabilir tek satıra çevirir:
        Dosya > Başlık Yolu (satır X-Y)"""
        parts = [doc.get("path", "Bilinmiyor")]
        if doc.get("ancestors"):
            parts.append(" > ".join(doc["ancestors"]))
        loc = " / ".join(parts)
        if doc.get("line"):
            loc += f" (satır {doc['line']})"
        return loc

    # ------------------------------------------------------------------
    # Kaynak kullanımı (RAM / CPU)
    # ------------------------------------------------------------------

    def get_resource_usage(self) -> dict:
        """Bu Python sürecinin o anki RAM ve CPU kullanımı.
        CPU yüzdesi son çağrıdan bu yana geçen süreye göre hesaplanır
        (psutil'in interval=None modu) — yani art arda iki çağrı arasındaki
        CPU kullanımını yansıtır."""
        mem = self._process.memory_info()
        return {
            "rss_mb": round(mem.rss / (1024 * 1024), 1),
            "cpu_percent": self._process.cpu_percent(interval=None),
            "system_cpu_percent": psutil.cpu_percent(interval=None),
            "system_ram_percent": psutil.virtual_memory().percent,
        }

    # ------------------------------------------------------------------
    # Belge yönetimi (ekle / sil / listele)
    # ------------------------------------------------------------------

    def list_documents(self) -> list:
        def _list():
            ts = TreeSearch(db_path=self.db_path)
            return ts.get_indexed_files()
        return self.executor.submit(_list).result()

    def add_document(self, file_path: str, force: bool = False) -> list:
        """Bir .md (veya TreeSearch'ün desteklediği başka bir metin) dosyasını
        indekse ekler ve indeksi diske kaydeder. PDF dosyaları için önce
        convert_pdf_to_markdown() ile Markdown'a çevirin."""
        def _add():
            ts = TreeSearch(db_path=self.db_path)
            docs = ts.index(file_path, force=force)
            ts.save_index(self.db_path)
            return docs
        return self.executor.submit(_add).result()

    def remove_document(self, doc_id: str) -> int:
        def _remove():
            ts = TreeSearch(db_path=self.db_path)
            n = ts.delete(doc_id)
            ts.save_index(self.db_path)
            return n
        return self.executor.submit(_remove).result()

    @staticmethod
    def convert_pdf_to_markdown(pdf_path: str, output_dir: str, timeout: float = 300.0) -> str:
        """opendataloader-pdf CLI ile PDF'i Markdown'a çevirir (Java 11+ ve
        `pip install opendataloader-pdf` gerektirir). Dönen Markdown dosyasının
        yolunu verir."""
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            # NOT: opendataloader-pdf CLI'sinde "convert" diye bir alt komut
            # YOK. Doğru kullanım: opendataloader-pdf <dosya> -o <klasör> -f markdown
            # ("convert" kelimesi ilk positional argüman (input_path) olarak
            # yorumlanıp "dosya bulunamadı" hatasına ve çıkış kodu 1'e sebep
            # oluyordu — önceki sürümdeki gerçek hata buydu).
            result = subprocess.run(
                ["opendataloader-pdf", pdf_path, "-o", str(out_dir), "-f", "markdown"],
                capture_output=True, text=True, timeout=timeout,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "'opendataloader-pdf' komutu bulunamadı. Kurulum: "
                "`pip install opendataloader-pdf` ve Java 11+ kurulu/PATH'te "
                "olmalı (`java -version` ile kontrol edin)."
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"opendataloader-pdf dönüştürme başarısız (Java kurulu mu?): "
                f"{result.stderr.strip()}"
            )
        expected = out_dir / (Path(pdf_path).stem + ".md")
        if not expected.exists():
            candidates = list(out_dir.glob(f"{Path(pdf_path).stem}*.md"))
            if candidates:
                expected = candidates[0]
            else:
                raise RuntimeError(f"Dönüştürme sonrası Markdown dosyası bulunamadı: {out_dir}")
        return str(expected)

    # ------------------------------------------------------------------
    # Arama
    # ------------------------------------------------------------------

    def _flatten(self, result: dict, doc_source_paths: dict = None) -> list:
        """pytreesearch'ün doc->node ağacını app.py'nin beklediği düz
        (path/line/ancestors/content) liste haline getirir.

        `content` için önce KAYNAK DOSYADAN tam satırları okumayı dener
        (doc_source_paths verilmişse) — çünkü node['summary'] pytreesearch
        tarafından kırpılmış olabilir. Dosya okunamazsa summary'ye düşer."""
        doc_source_paths = doc_source_paths or {}
        ancestor_map = {}
        for p in result.get("paths", []):
            titles = [seg["title"] for seg in p.get("path", [])[:-1]]
            ancestor_map[p.get("target_node_id")] = titles

        flat = []
        for doc in result.get("documents", []):
            doc_label = doc.get("doc_name") or doc.get("doc_id") or "Bilinmiyor"
            source_path = doc_source_paths.get(doc.get("doc_id"))
            for node in doc.get("nodes", []):
                line_start = node.get("line_start")
                line_end = node.get("line_end")
                line = f"{line_start}-{line_end}" if line_start else None

                full_text = _read_source_lines(source_path, line_start, line_end)
                content = full_text if full_text else (node.get("summary") or node.get("text") or "")

                flat.append({
                    "path": doc_label,
                    "line": line,
                    "ancestors": ancestor_map.get(node.get("node_id"), []),
                    "content": content,
                    "content_truncated": full_text is None,  # summary'ye düşüldüyse kırpılmış olabilir
                    "title": node.get("title"),
                    "score": node.get("score", 0),
                })
        flat.sort(key=lambda x: x.get("score") or 0, reverse=True)
        return flat

    def search(self, query: str, use_query_expansion: bool = True,
               correct_spelling: bool = False) -> dict:
        """
        Yerel eş anlamlı sözlüğü tamamen bellekte olduğu için
        `use_query_expansion=True` artık PRATİKTE ÜCRETSİZ (<1ms) —
        varsayılan olarak açık. Yazım düzeltme hâlâ bir Ollama çağrısı
        gerektirdiği için varsayılan kapalı. TDK online / Ollama yedekleri
        `expand_query()`'ye ayrıca `use_tdk_fallback` / `use_ollama_fallback`
        ile açılabilir ama bunlar saniyeler sürebilir.
        """
        timings = {}
        working_query = query

        t0 = time.time()
        if correct_spelling:
            working_query = self.correct_spelling(working_query)
        timings["spelling_ms"] = round((time.time() - t0) * 1000, 1)

        t0 = time.time()
        if use_query_expansion:
            working_query = self.expand_query(working_query)
        timings["expansion_ms"] = round((time.time() - t0) * 1000, 1)

        def _search():
            ts = TreeSearch(db_path=self.db_path)
            raw_result = ts.search(working_query, search_mode="tree")
            source_paths = {d["doc_id"]: d.get("source_path") for d in ts.get_indexed_files()}
            return raw_result, source_paths

        t0 = time.time()
        future = self.executor.submit(_search)
        raw, doc_source_paths = future.result()
        timings["treesearch_ms"] = round((time.time() - t0) * 1000, 1)

        flat = self._flatten(raw, doc_source_paths)

        # ANCHOR TERİM FİLTRESİ: sorgudaki özel isim (varsa) korpusta hangi
        # belgede geçiyorsa o belgeleri öne al. Bu adım build_extractive_answer
        # ve rewrite_text'ten ÖNCE, yani belgeler max_docs/max_nodes ile
        # kesilmeden ÖNCE çalışır — "Gordion" belgesi TreeSearch'ün ham
        # skoruna göre 4. sırada bile olsa, artık 1. sıraya taşınır ve
        # kesilme (truncation) sırasında kaybolmaz.
        anchor_terms = _extract_anchor_terms(query, flat)
        flat, has_evidence = _rerank_by_anchor_terms(flat, anchor_terms)

        t0 = time.time()
        extractive = self.build_extractive_answer(query, flat)
        timings["extractive_answer_ms"] = round((time.time() - t0) * 1000, 1)

        return {
            "documents": flat,
            "extractive_answer": extractive,
            "used_query": working_query,
            "original_query": query,
            "timings": timings,
            "resources": self.get_resource_usage(),
            "anchor_terms": sorted(anchor_terms),
            "has_sufficient_evidence": has_evidence,
        }
