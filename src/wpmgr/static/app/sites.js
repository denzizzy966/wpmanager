// Font Awesome Free 6 by @fontawesome - https://fontawesome.com License - https://fontawesome.com/license/free (Icons: CC BY 4.0)
// Ikon aksi tabel: konstanta statis, tanpa data dinamis di dalam SVG.
// Path resmi Font Awesome Free 6.7.2 solid: right-to-bracket, arrows-rotate, eye.
const svgIkon = (viewBox, d) =>
  `<svg class="ikon" viewBox="${viewBox}" fill="currentColor" aria-hidden="true" focusable="false"><path d="${d}"/></svg>`;
const IKON = Object.freeze({
  masuk: svgIkon('0 0 512 512', 'M217.9 105.9L340.7 228.7c7.2 7.2 11.3 17.1 11.3 27.3s-4.1 20.1-11.3 27.3L217.9 406.1c-6.4 6.4-15 9.9-24 9.9c-18.7 0-33.9-15.2-33.9-33.9l0-62.1L32 320c-17.7 0-32-14.3-32-32l0-64c0-17.7 14.3-32 32-32l128 0 0-62.1c0-18.7 15.2-33.9 33.9-33.9c9 0 17.6 3.6 24 9.9zM352 416l64 0c17.7 0 32-14.3 32-32l0-256c0-17.7-14.3-32-32-32l-64 0c-17.7 0-32-14.3-32-32s14.3-32 32-32l64 0c53 0 96 43 96 96l0 256c0 53-43 96-96 96l-64 0c-17.7 0-32-14.3-32-32s14.3-32 32-32z'),
  scan: svgIkon('0 0 512 512', 'M105.1 202.6c7.7-21.8 20.2-42.3 37.8-59.8c62.5-62.5 163.8-62.5 226.3 0L386.3 160 352 160c-17.7 0-32 14.3-32 32s14.3 32 32 32l111.5 0c0 0 0 0 0 0l.4 0c17.7 0 32-14.3 32-32l0-112c0-17.7-14.3-32-32-32s-32 14.3-32 32l0 35.2L414.4 97.6c-87.5-87.5-229.3-87.5-316.8 0C73.2 122 55.6 150.7 44.8 181.4c-5.9 16.7 2.9 34.9 19.5 40.8s34.9-2.9 40.8-19.5zM39 289.3c-5 1.5-9.8 4.2-13.7 8.2c-4 4-6.7 8.8-8.1 14c-.3 1.2-.6 2.5-.8 3.8c-.3 1.7-.4 3.4-.4 5.1L16 432c0 17.7 14.3 32 32 32s32-14.3 32-32l0-35.1 17.6 17.5c0 0 0 0 0 0c87.5 87.4 229.3 87.4 316.7 0c24.4-24.4 42.1-53.1 52.9-83.8c5.9-16.7-2.9-34.9-19.5-40.8s-34.9 2.9-40.8 19.5c-7.7 21.8-20.2 42.3-37.8 59.8c-62.5 62.5-163.8 62.5-226.3 0l-.1-.1L125.6 352l34.4 0c17.7 0 32-14.3 32-32s-14.3-32-32-32L48.4 288c-1.6 0-3.2 .1-4.8 .3s-3.1 .5-4.6 1z'),
  detail: svgIkon('0 0 576 512', 'M288 32c-80.8 0-145.5 36.8-192.6 80.6C48.6 156 17.3 208 2.5 243.7c-3.3 7.9-3.3 16.7 0 24.6C17.3 304 48.6 356 95.4 399.4C142.5 443.2 207.2 480 288 480s145.5-36.8 192.6-80.6c46.8-43.5 78.1-95.4 93-131.1c3.3-7.9 3.3-16.7 0-24.6c-14.9-35.7-46.2-87.7-93-131.1C433.5 68.8 368.8 32 288 32zM144 256a144 144 0 1 1 288 0 144 144 0 1 1 -288 0zm144-64c0 35.3-28.7 64-64 64c-7.1 0-13.9-1.2-20.3-3.3c-5.5-1.8-11.9 1.6-11.7 7.4c.3 6.9 1.3 13.8 3.2 20.7c13.7 51.2 66.4 81.6 117.6 67.9s81.6-66.4 67.9-117.6c-11.1-41.5-47.8-69.4-88.6-71.1c-5.8-.2-9.2 6.1-7.4 11.7c2.1 6.4 3.3 13.2 3.3 20.3z'),
});

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
    galat: '',
    terpilih: [],
    mengirim: false,
    info: '',

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
          {
            dataField: 'connector_version',
            caption: 'Connector',
            // Versi dilaporkan site client, jadi tetap lewat esc(); kelas
            // badge hanya salah satu dari dua string tetap.
            cellTemplate: (v, baris) =>
              `${esc(v || '—')}${baris.connector_usang ? ' <span class="dg-badge-warning">usang</span>' : ''}`,
          },
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
              `<button type="button" class="tombol-aksi" data-sso="${esc(id)}" aria-label="Masuk wp-admin" title="Masuk wp-admin">${IKON.masuk}</button>` +
              `<button type="button" class="tombol-aksi" data-scan="${esc(id)}" aria-label="Scan" title="Scan">${IKON.scan}</button>` +
              `<a class="tombol-aksi" href="/sites/${esc(id)}" aria-label="Detail" title="Detail">${IKON.detail}</a>`,
          },
        ],
        onSelectionChanged: (e) => { this.terpilih = e.rows; },
      });

      // Delegasi event pada wadah grid, bukan pada tiap tombol: DataGrid
      // menggambar ulang isinya setiap kali disortir, difilter, dikelompokkan,
      // atau dipaginasi, sehingga listener yang menempel langsung pada tombol
      // akan lenyap begitu tombol itu digambar ulang. Listener pada wadah
      // tetap hidup melewati setiap penggambaran ulang.
      document.getElementById('grid').addEventListener('click', async (ev) => {
        const sso = ev.target.getAttribute('data-sso');
        const scan = ev.target.getAttribute('data-scan');
        if (!sso && !scan) return;
        this.galat = '';
        // Kegagalan ditampilkan di halaman, bukan ditelan: dulu SSO yang
        // gagal membuka tab "undefined", dan Scan yang ditolak tetap
        // menampilkan "Antre…" untuk job yang tidak pernah dibuat.
        try {
          if (sso) {
            const r = await fetch(`/api/sso/${sso}`);
            if (!r.ok) {
              this.galat = `SSO gagal. ${await pesanGalat(r)}`;
              return;
            }
            window.open((await r.json()).url, '_blank', 'noopener');
          } else {
            const r = await fetch('/api/jobs/scan', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ site_id: scan }),
            });
            if (!r.ok) {
              this.galat = `Scan tidak diantrekan. ${await pesanGalat(r)}`;
              return;
            }
            ev.target.textContent = 'Antre…';
          }
        } catch (e) {
          this.galat = `Gagal menghubungi server: ${e.message}`;
        }
      });
    },

    async perbaruiConnector() {
      this.galat = '';
      this.info = '';
      const tidakBisa = this.terpilih.filter((b) => !b.bisa_self_update);
      if (tidakBisa.length) {
        this.galat = `${tidakBisa.length} site memakai connector lama yang harus diperbarui ` +
          'manual sekali lewat wp-admin: ' + tidakBisa.map((b) => b.nama).join(', ');
        return;
      }
      this.mengirim = true;
      try {
        const r = await fetch('/api/jobs/update-connector', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ site_ids: this.terpilih.map((b) => b.id) }),
        });
        if (!r.ok) {
          this.galat = `Pembaruan tidak dijadwalkan. ${await pesanGalat(r)}`;
          return;
        }
        const data = await r.json();
        this.info = `${data.job_ids.length} pembaruan connector dijadwalkan. Pantau hasilnya di halaman Aktivitas.`;
      } catch (e) {
        this.galat = `Gagal menghubungi server: ${e.message}`;
      } finally {
        this.mengirim = false;
      }
    },
  };
}
