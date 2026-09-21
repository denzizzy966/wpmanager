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
