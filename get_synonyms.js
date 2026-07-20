const getWord = require('tdk-all-api');

const word = process.argv[2];

async function fetchData() {
    try {
        const result = await getWord(word);
        const synonyms = [];
        
        // Eğer anlamlar (means) varsa
        if (result.means) {
            for (const m of result.means) {
                if (m.mean) synonyms.push(m.mean);
            }
        }
        
        // Eğer bileşik kelimeler (compounds) varsa
        if (result.compounds) {
            for (const c of result.compounds) {
                synonyms.push(c);
            }
        }
        
        // JSON olarak çıktı ver
        console.log(JSON.stringify(synonyms));
    } catch (e) {
        // Hata durumunda boş dizi döndür
        console.log(JSON.stringify([]));
    }
}

fetchData();