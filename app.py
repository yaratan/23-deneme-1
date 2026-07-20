import time
from datetime import datetime
from pathlib import Path

import streamlit as st

from ckb_engine import CKBEngine
from ckb_history import HistoryStore

st.set_page_config(page_title="Curriculum Knowledge Base", layout="wide")

MD_OUTPUT_DIR = "./markdown_output"
UPLOAD_TMP_DIR = "./_uploads_tmp"
MAX_HISTORY = 20


@st.cache_resource
def get_engine():
    return CKBEngine(db_path="./index.db", synonym_json_path="./es_anlamli.json")


engine = get_engine()


@st.cache_resource
def get_history():
    return HistoryStore(db_path="./history.db")


history = get_history()

if "history" not in st.session_state:
    st.session_state["history"] = []

ana_kolon, yan_panel = st.columns([3, 1])

# =====================================================================
# YAN PANEL
# =====================================================================
with yan_panel:
    st.subheader("⚙️ Sistem")
    if "last_resources" in st.session_state:
        r = st.session_state["last_resources"]
        st.markdown(
            f"**RAM (bu süreç):** {r['rss_mb']} MB\n\n"
            f"**CPU (bu süreç):** %{r['cpu_percent']}\n\n"
            f"**Sistem CPU:** %{r['system_cpu_percent']}\n\n"
            f"**Sistem RAM:** %{r['system_ram_percent']}"
        )
    else:
        st.caption("Henüz sorgu yapılmadı.")

    st.divider()
    st.subheader("🕘 Sorgu Geçmişi")
    if st.session_state["history"]:
        for h in reversed(st.session_state["history"][-MAX_HISTORY:]):
            st.caption(f"{h['zaman']} · {h['sure']:.2f}sn · {h['sonuc_sayisi']} sonuç")
            if st.button(h["soru"], key=f"hist_{h['id']}", use_container_width=True):
                st.session_state["rerun_query"] = h["soru"]
                st.rerun()
        if st.button("🗑️ Geçmişi temizle", use_container_width=True):
            st.session_state["history"] = []
            st.rerun()
    else:
        st.caption("Henüz sorgu yapılmadı.")

    with st.expander("💾 Kalıcı geçmiş (history.db)"):
        ozet = history.stats_summary()
        if ozet:
            st.caption(f"Toplam kayıtlı sorgu: {ozet['toplam_sorgu']}")
            st.caption(f"Etiketlenen (doğru/yanlış): {ozet['etiketlenen_kayit']}")
        else:
            st.caption("Henüz kayıt yok.")

    st.divider()
    st.subheader("📚 Belgeler")

    try:
        belgeler_listesi = engine.list_documents()
    except Exception:
        belgeler_listesi = []

    if belgeler_listesi:
        for d in belgeler_listesi:
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"📄 `{d.get('doc_id', d.get('doc_name', '?'))}`")
            if c2.button("🗑️", key=f"sil_{d.get('doc_id')}", help="İndeksten sil"):
                try:
                    n = engine.remove_document(d["doc_id"])
                    st.success(f"Silindi ({n} kayıt).")
                    st.rerun()
                except Exception as e:
                    st.error(f"Silinemedi: {e}")
    else:
        st.caption("İndekste belge yok.")

    st.markdown("**Yeni belge ekle** (.pdf / .md)")

    if "last_upload_results" not in st.session_state:
        st.session_state["last_upload_results"] = []

    if st.session_state["last_upload_results"]:
        st.markdown("**Son yükleme sonucu:**")
        for res in st.session_state["last_upload_results"]:
            if res["basarili"]:
                st.success(
                    f"✅ {res['dosya']} — sorgulamaya hazır\n\n"
                    f"⏱️ Dönüştürme: {res['donusum_sure']:.1f} sn · "
                    f"İndeksleme: {res['index_sure']:.1f} sn · "
                    f"**Toplam: {res['toplam_sure']:.1f} sn**"
                )
            else:
                st.error(f"❌ {res['dosya']}: {res['hata']}")
        if st.button("Kapat", key="close_upload_results", use_container_width=True):
            st.session_state["last_upload_results"] = []
            st.rerun()
        st.divider()

    yuklenen = st.file_uploader(
        "Dosya seç", type=["pdf", "md"], label_visibility="collapsed",
        accept_multiple_files=True, key="file_uploader_widget",
    )
    if yuklenen and st.button("➕ İndeksle", use_container_width=True, key="index_button"):
        Path(UPLOAD_TMP_DIR).mkdir(exist_ok=True)
        results = []
        for f in yuklenen:
            tmp_path = Path(UPLOAD_TMP_DIR) / f.name
            tmp_path.write_bytes(f.getbuffer())
            t_baslangic = time.time()
            donusum_sure = 0.0
            index_sure = 0.0
            try:
                with st.spinner(f"{f.name} işleniyor..."):
                    if tmp_path.suffix.lower() == ".pdf":
                        t_c0 = time.time()
                        md_path = engine.convert_pdf_to_markdown(str(tmp_path), MD_OUTPUT_DIR)
                        donusum_sure = time.time() - t_c0
                    else:
                        md_path = str(tmp_path)

                    t_i0 = time.time()
                    engine.add_document(md_path, force=True)
                    index_sure = time.time() - t_i0

                results.append({
                    "dosya": f.name,
                    "basarili": True,
                    "donusum_sure": donusum_sure,
                    "index_sure": index_sure,
                    "toplam_sure": time.time() - t_baslangic,
                })
            except Exception as e:
                results.append({"dosya": f.name, "basarili": False, "hata": str(e)})

        st.session_state["last_upload_results"] = results
        st.rerun()

# =====================================================================
# ANA KOLON: Arama
# =====================================================================
with ana_kolon:
    st.title("📘 Curriculum Knowledge Base")

    varsayilan_soru = st.session_state.pop("rerun_query", "")
    soru = st.text_input("Sorunuzu yazın:", value=varsayilan_soru,
                          placeholder="Örn: Hz. Ali kimdir")
    genislet = st.checkbox(
        "🔍 Eş anlamlı genişletme (yerel sözlük — hızı etkilemez)",
        value=True,
    )

    if soru:
        col1, col2 = st.columns(2)
        with col1:
            ham_tiklandi = st.button("📄 Ham Cevap", use_container_width=True)
        with col2:
            akici_tiklandi = st.button("✨ Akıcı Cevap", use_container_width=True)

        if ham_tiklandi or akici_tiklandi:
            t_baslangic = time.time()
            sure_yeri = st.empty()
            sure_yeri.caption("⏱️ 0.00 saniye (arıyor...)")

            try:
                with st.spinner("Aranıyor..."):
                    sonuc = engine.search(soru, use_query_expansion=genislet)
                t_arama_bitti = time.time()

                akici_metin = None
                grounding = None
                grounded_sentences = []
                removed_count = 0
                if akici_tiklandi:
                    with st.spinner("Akıcı cevap üretiliyor (Ollama)..."):
                        akici_metin = engine.rewrite_text(soru, sonuc["documents"])
                        if akici_metin:
                            grounding = engine.check_grounding(akici_metin, sonuc["documents"])
                            grounded_sentences = [g for g in grounding if g["grounded"]]
                            removed_count = len(grounding) - len(grounded_sentences)
                t_bitti = time.time()

                toplam_sure = t_bitti - t_baslangic
                arama_suresi = t_arama_bitti - t_baslangic
                uretim_suresi = t_bitti - t_arama_bitti
                sure_yeri.caption(
                    f"⏱️ Toplam: {toplam_sure:.2f} sn "
                    f"(arama: {arama_suresi:.2f} sn"
                    + (f", akıcı cevap üretimi: {uretim_suresi:.2f} sn)" if akici_tiklandi else ")")
                )

                t = sonuc.get("timings", {})
                st.caption(
                    f"🔬 Motor detayı — TreeSearch: {t.get('treesearch_ms', 0)} ms · "
                    f"Genişletme: {t.get('expansion_ms', 0)} ms · "
                    f"Ham cevap çıkarımı: {t.get('extractive_answer_ms', 0)} ms"
                )

                if sonuc.get("resources"):
                    st.session_state["last_resources"] = sonuc["resources"]

                if sonuc.get("used_query") and sonuc["used_query"] != soru:
                    st.caption(f"🔎 Genişletilmiş sorgu: `{sonuc['used_query']}`")

                belgeler = sonuc.get("documents", [])

                # Sorgu geçmişi (oturum içi)
                st.session_state["history"].append({
                    "id": len(st.session_state["history"]),
                    "soru": soru,
                    "zaman": datetime.now().strftime("%H:%M:%S"),
                    "sure": toplam_sure,
                    "sonuc_sayisi": len(belgeler),
                })

                # Kalıcı veritabanı kaydı (history.db) — F1/Accuracy için
                kaynak_ozet = [{"path": d["path"], "line": d.get("line")} for d in belgeler[:5]]
                if ham_tiklandi:
                    ham_extractive = sonuc.get("extractive_answer") or {}
                    history.log(
                        query=soru, answer_type="ham",
                        answer_text=ham_extractive.get("summary", ""),
                        sources=kaynak_ozet, timings=t, resources=sonuc.get("resources"),
                    )
                if akici_tiklandi and akici_metin is not None:
                    history.log(
                        query=soru, answer_type="akici", answer_text=akici_metin,
                        sources=kaynak_ozet, timings=t, resources=sonuc.get("resources"),
                        grounding=grounding, removed_sentence_count=removed_count,
                    )

                # --- HAM CEVAP (İstediğin Yeni Yapı) ---
                if ham_tiklandi:
                    extractive = sonuc.get("extractive_answer") or {}
                    st.subheader("📄 Ham Cevap")
                    
                    if extractive.get("summary"):
                        st.markdown(f"**Özet:** {extractive['summary']}")
                        st.markdown("---")
                    
                    if extractive.get("details"):
                        st.markdown("**Detaylı Kaynak Cümleleri:**")
                        for item in extractive["details"]:
                            st.markdown(f"- {item['text']}")
                            st.caption(f"📍 {item['source']}")
                    else:
                        st.warning("Sorguyla örtüşen belirgin bilgi bulunamadı.")
                    
                    st.caption("✅ Tüm bilgiler kaynak metinlerden birebir alınmıştır.")
                    st.markdown("---")

                # --- AKICI CEVAP (Ollama, yeniden yazılmış, doğrulanmış) ---
                if akici_tiklandi:
                    if akici_metin is None:
                        st.info(
                            "✨ Akıcı Cevap üretilemedi (kaynak bulunamadı). "
                            "Ham Cevap'ı deneyin."
                        )
                    else:
                        st.subheader("✨ Akıcı Cevap")
                        if engine.last_rewrite_used_fallback:
                            st.caption(
                                "ℹ️ Ollama kullanılamıyor (kurulu değil/çalışmıyor), "
                                "bu yüzden LLM KULLANILMADAN, kaynaktan birebir alınan cümlelerle "
                                "anında oluşturulan bir alternatif gösteriliyor:"
                            )
                            st.markdown(akici_metin)
                        elif grounding:
                            if grounded_sentences:
                                for g in grounded_sentences:
                                    st.markdown(g["text"])
                            else:
                                st.warning(
                                    "Üretilen cevabın hiçbir cümlesi kaynak metinlerle "
                                    "doğrulanamadı, bu yüzden hiçbiri gösterilmiyor. "
                                    "Ham Cevap'ı kullanın."
                                )
                            if removed_count:
                                with st.expander(
                                    f"🛑 {removed_count} cümle kaynakla doğrulanamadığı için "
                                    "GÖSTERİLMEDİ (olası uydurma)"
                                ):
                                    st.caption(
                                        "Bu cümleler, sistemin oluşturduğu cevaptan çıkarıldı "
                                        "çünkü ya kaynak metinle yeterince örtüşmüyor ya da "
                                        "içindeki isimler kaynakta birlikte geçmiyor. Sadece "
                                        "şeffaflık için gösteriliyor, güvenilir bilgi olarak "
                                        "KULLANILMAMALIDIR."
                                    )
                                    for g in grounding:
                                        if not g["grounded"]:
                                            st.caption(f"~~{g['text']}~~")
                            st.caption(
                                "⚠️ Bu cevap bir LLM tarafından yeniden yazılmıştır. "
                                "Doğrulanamayan cümleler otomatik filtrelendi, ama %100 "
                                "halüsinasyon garantisi generatif bir modelde mümkün "
                                "değildir. Kesin/uydurmasız bilgi için her zaman Ham "
                                "Cevap'ı esas alın."
                            )
                        else:
                            st.markdown(akici_metin)
                        st.markdown("---")

                # Kaynak metinler
                if belgeler:
                    st.subheader(f"📖 Kaynak Metinler ({len(belgeler)} sonuç bulundu)")
                    for idx, doc in enumerate(belgeler):
                        with st.expander(f"Sonuç #{idx+1}: {doc.get('title') or doc.get('path')}"):
                            st.markdown(f"**Dosya:** `{doc.get('path')}`")
                            if doc.get("line"):
                                st.markdown(f"**Satır:** {doc['line']}")
                            if doc.get("ancestors"):
                                st.markdown(f"**Başlık Yolu:** {' > '.join(doc['ancestors'])}")
                            st.markdown("---")
                            st.markdown(doc.get("content") or "İçerik bulunamadı")

            except Exception as e:
                st.error(f"Bir hata oluştu: {e}")