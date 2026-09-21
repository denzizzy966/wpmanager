function layarUpdate() {
  return {
    grid: null,
    terpilih: [],
    berjalan: false,
    progres: [],
    galat: '',
    _timer: null,

    async muat() {
      const data = await (await fetch('/api/packages')).json();
      if (this.grid) {
        // setData() sudah memanggil _rebuild() lalu _renderAll() secara
        // internal (isi yang sama persis dengan refresh()); memanggil
        // refresh() sesudahnya hanya akan menggambar ulang untuk kedua
        // kalinya tanpa manfaat tambahan.
        this.grid.setData(data);
        return;
      }
      this.grid = new DataGrid('#grid', {
        dataSource: data,
        keyExpr: 'id',
        selection: 'multiple',
        columns: [
          { dataField: 'client_nama', caption: 'Client' },
          { dataField: 'site_nama', caption: 'Site' },
          { dataField: 'tipe', caption: 'Tipe' },
          { dataField: 'nama', caption: 'Nama' },
          { dataField: 'versi_terpasang', caption: 'Terpasang' },
          {
            dataField: 'versi_tersedia',
            caption: 'Tersedia',
            // nilai berasal dari string versi yang dibaca connector dari site
            // client — site yang justru sedang diawasi karena mungkin sudah
            // disusupi. cellTemplate tidak di-escape otomatis oleh DataGrid
            // (berbeda dari sel biasa), jadi esc() wajib di sini.
            cellTemplate: (nilai) => `<span class="dg-badge-warning">${esc(nilai)}</span>`,
          },
          { dataField: 'last_scan_at', caption: 'Terakhir Scan', dataType: 'date' },
        ],
        summary: { totalItems: [{ column: 'nama', type: 'count' }] },
        onSelectionChanged: (e) => { this.terpilih = e.rows; },
      });
    },

    async jalankanUpdate() {
      if (!this.terpilih.length) return;
      this.galat = '';
      this.berjalan = true;
      const items = this.terpilih.map((b) => ({
        site_id: b.site_id, tipe: b.tipe, slug: b.slug, ke_versi: b.versi_tersedia,
      }));
      // Tanpa pemeriksaan ini, POST yang ditolak (sesi habis, site sudah
      // dicabut, 403 asal) terlihat persis seperti antrean yang langsung
      // selesai: strip progres kosong, grid dimuat ulang, dan operator
      // menyimpulkan update berjalan padahal tidak ada satu job pun dibuat.
      let respons;
      try {
        respons = await fetch('/api/jobs/update', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items }),
        });
      } catch (e) {
        this.berjalan = false;
        this.galat = `Gagal menghubungi server: ${e.message}`;
        return;
      }
      if (!respons.ok) {
        this.berjalan = false;
        this.galat = `Update tidak dijalankan. ${await pesanGalat(respons)}`;
        return;
      }
      this.pantau();
    },

    pantau() {
      clearInterval(this._timer);
      this._timer = setInterval(async () => {
        this.progres = await (await fetch('/api/jobs/active')).json();
        if (this.progres.length === 0) {
          clearInterval(this._timer);
          this.berjalan = false;
          this.terpilih = [];
          this.muat();
        }
      }, 2000);
    },
  };
}
