const _ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/**
 * Meloloskan nilai untuk disisipkan ke dalam HTML.
 *
 * DataGrid meng-escape sendiri isi sel biasa, tetapi nilai kembalian
 * cellTemplate disisipkan apa adanya — escaping menjadi tanggung jawab penulis
 * template. Sebagian besar nilai di grid ini berasal dari site client, yang
 * tidak kita kendalikan dan yang mungkin justru sudah disusupi.
 */
function esc(nilai) {
  if (nilai === null || nilai === undefined) return "";
  return String(nilai).replace(/[&<>"']/g, (c) => _ESC[c]);
}

/**
 * Pesan yang bisa dibaca manusia dari respons HTTP yang gagal.
 *
 * FastAPI membalas {"detail": "..."} untuk HTTPException dan
 * {"detail": [{msg: ...}, ...]} untuk galat validasi. Hasilnya teks polos:
 * pemanggil WAJIB menyisipkannya lewat textContent/x-text, bukan sebagai markup
 * mentah -- detail bisa memuat nilai yang berasal dari site client.
 */
async function pesanGalat(respons) {
  let rincian = "";
  try {
    const data = await respons.json();
    if (typeof data.detail === "string") {
      rincian = data.detail;
    } else if (Array.isArray(data.detail)) {
      rincian = data.detail.map((d) => d.msg).join("; ");
    }
  } catch (e) {
    // Body bukan JSON (mis. halaman error proxy); status saja sudah cukup.
  }
  return `HTTP ${respons.status}${rincian ? ": " + rincian : ""}`;
}
