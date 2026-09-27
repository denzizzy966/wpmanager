function layarUpdate() {
  return {
    grid: null,
    terpilih: [],
    berjalan: false,
    progres: [],
    galat: '',
    info: '',
    stagingAktif: false,
    pesanKonfirmasiUji: '',
    _timer: null,

    siapkan(dataset) {
      this.stagingAktif = dataset.staging === '1';
      this.pesanKonfirmasiUji = dataset.pesanKonfirmasi || '';
    },

    kolom() {
      const kolom = [
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
      ];
      if (this.stagingAktif) {
        kolom.push({
          dataField: 'uji',
          caption: 'Uji staging',
          // Kelas dari dua string tetap; alasan berasal dari hasil uji (teks
          // halaman staging yang bisa dikendalikan site) sehingga lewat esc().
          cellTemplate: (nilai) => (nilai
            ? `<span class="${nilai.hasil === 'lolos' ? 'dg-badge-success' : 'dg-badge-danger'}" title="${esc(nilai.alasan || '')}">${nilai.hasil === 'lolos' ? 'Lolos uji' : 'Gagal uji'}</span>`
            : ''),
        });
      }
      kolom.push({ dataField: 'last_scan_at', caption: 'Terakhir Scan', dataType: 'date' });
      return kolom;
    },

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
        columns: this.kolom(),
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

    async ujiStaging(konfirmasi = false) {
      if (!this.terpilih.length) return;
      this.galat = '';
      this.info = '';
      const items = this.terpilih.map((b) => ({
        site_id: b.site_id, tipe: b.tipe, slug: b.slug, ke_versi: b.versi_tersedia,
      }));
      let respons;
      try {
        respons = await fetch('/api/staging/uji', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items, konfirmasi }),
        });
      } catch (e) {
        this.galat = `Gagal menghubungi server: ${e.message}`;
        return;
      }
      if (!respons.ok) {
        const pesan = await pesanGalat(respons);
        // Server membalas "<nama site>: <pesan konfirmasi>" bila staging
        // diubah sejak tarik. Dicocokkan persis dengan pesan server (bukan
        // substring), karena nama site ikut di awal teks galat.
        const minta = respons.status === 409 && !konfirmasi && this.pesanKonfirmasiUji
          && pesan.endsWith(`: ${this.pesanKonfirmasiUji}`);
        if (minta && window.confirm(pesan)) {
          await this.ujiStaging(true);
          return;
        }
        this.galat = `Uji tidak dijalankan. ${pesan}`;
        return;
      }
      this.info = 'Uji di staging diantrekan. Hasilnya tampil di kolom Uji staging setelah selesai.';
      this.berjalan = true;
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
