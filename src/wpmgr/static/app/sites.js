const WARNA_STATUS = {
  active: 'dg-badge-success',
  pending_pair: 'dg-badge-info',
  needs_reconnect: 'dg-badge-warning',
  blocked: 'dg-badge-danger',
  unreachable: 'dg-badge-danger',
  disabled: 'dg-badge-muted',
};

function layarSite() {
  return {
    grid: null,

    async muat() {
      const data = await (await fetch('/api/sites')).json();
      if (this.grid) {
        // setData() menggambar ulang grid sendiri (lihat updates.js); tidak
        // perlu memanggil refresh() sesudahnya.
        this.grid.setData(data);
        return;
      }
      this.grid = new DataGrid('#grid', {
        dataSource: data,
        keyExpr: 'id',
        columns: [
          { dataField: 'nama', caption: 'Site' },
          { dataField: 'client_nama', caption: 'Client' },
          {
            dataField: 'status',
            caption: 'Status',
            // WARNA_STATUS[v] hanya pernah menghasilkan salah satu kelas CSS
            // tetap di atas atau string kosong, jadi bagian itu sendiri aman.
            // v yang ditampilkan sebagai teks tetap dilewatkan lewat esc() —
            // status hari ini memang cuma enam nilai enum yang tidak bisa
            // disuntik, tapi cellTemplate tidak di-escape otomatis oleh
            // DataGrid, dan kolom Aksi di bawah membuktikan tidak semua nilai
            // di grid ini seaman itu. Konsistensi di sini mencegah baris
            // berikutnya menyalin pola yang salah.
            cellTemplate: (v) => `<span class="${WARNA_STATUS[v] || ''}">${esc(v)}</span>`,
          },
          { dataField: 'wp_version', caption: 'WP' },
          { dataField: 'php_version', caption: 'PHP' },
          { dataField: 'jumlah_update', caption: 'Update' },
          { dataField: 'last_seen_at', caption: 'Terakhir Terlihat', dataType: 'date' },
          {
            caption: 'Aksi',
            calculateCellValue: (baris) => baris.id,
            // id adalah UUID yang dibuat server (uuid.uuid4()), bukan input
            // bebas dari site client — tapi tetap dilewatkan lewat esc()
            // demi konsistensi: pembaca kode tidak bisa langsung menebak
            // interpolasi mana yang "aman" dari sekadar membaca satu baris.
            cellTemplate: (id) =>
              `<button data-sso="${esc(id)}">Masuk</button> ` +
              `<button data-scan="${esc(id)}">Scan</button> ` +
              `<a href="/sites/${esc(id)}">Detail</a>`,
          },
        ],
      });

      // Delegasi event pada wadah grid, bukan pada tiap tombol: DataGrid
      // menggambar ulang isinya setiap kali disortir, difilter, dikelompokkan,
      // atau dipaginasi, sehingga listener yang menempel langsung pada tombol
      // akan lenyap begitu tombol itu digambar ulang. Listener pada wadah
      // tetap hidup melewati setiap penggambaran ulang.
      document.getElementById('grid').addEventListener('click', async (ev) => {
        const sso = ev.target.getAttribute('data-sso');
        const scan = ev.target.getAttribute('data-scan');
        if (sso) {
          const r = await (await fetch(`/api/sso/${sso}`)).json();
          window.open(r.url, '_blank', 'noopener');
        } else if (scan) {
          await fetch('/api/jobs/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ site_id: scan }),
          });
          ev.target.textContent = 'Antre…';
        }
      });
    },
  };
}
