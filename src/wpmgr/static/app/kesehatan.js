const URUTAN_CHIP = [
  'mati', 'perlu_diperiksa', 'dorong_gagal', 'diserang', 'error_baru', 'ssl', 'koneksi',
  'penangkap_terbatas', 'staging_gagal', 'traffic_anjlok', 'traffic_melonjak', 'connector_usang',
];
const LABEL_CHIP = {
  mati: 'mati', perlu_diperiksa: 'perlu diperiksa', dorong_gagal: 'dorong ke produksi gagal', diserang: 'diserang',
  error_baru: 'error baru', ssl: 'SSL bermasalah', koneksi: 'koneksi bermasalah',
  penangkap_terbatas: 'penangkap terbatas', staging_gagal: 'staging gagal', traffic_anjlok: 'traffic anjlok',
  traffic_melonjak: 'traffic melonjak', connector_usang: 'connector usang',
};
const TINGKAT_CHIP = {
  mati: 1, perlu_diperiksa: 1, dorong_gagal: 1, diserang: 2, error_baru: 2, ssl: 2, koneksi: 2,
  penangkap_terbatas: 2, staging_gagal: 2, traffic_anjlok: 3, traffic_melonjak: 3, connector_usang: 3,
};
const KELAS_TINGKAT = { 1: 'chip-merah', 2: 'chip-kuning', 3: 'chip-biru' };
const TEKS_UPTIME = { naik: 'Naik', mati: 'Mati', terblokir: 'Terblokir', belum_dicek: 'Belum dicek' };
const KELAS_UPTIME = {
  naik: 'dg-badge-success', mati: 'dg-badge-danger', terblokir: 'dg-badge-warning', belum_dicek: 'dg-badge-muted',
};
const TEKS_KEAMANAN = { aman: 'Aman', diserang: 'Diserang', perlu_diperiksa: 'Perlu diperiksa' };
const KELAS_KEAMANAN = {
  aman: 'dg-badge-success', diserang: 'dg-badge-warning', perlu_diperiksa: 'dg-badge-danger',
};

// Kelas selalu salah satu string tetap dari peta di atas; teks tetap lewat esc().
function lencana(kelas, teks) {
  return `<span class="${kelas}">${esc(teks)}</span>`;
}

function layarKesehatan() {
  return {
    grid: null,
    data: [],
    chip: {},
    filter: null,
    galat: '',
    diperbarui: '',
    urutanChip: URUTAN_CHIP,
    _sedangMemuat: false,

    label(k) { return LABEL_CHIP[k]; },
    kelasChip(k) { return KELAS_TINGKAT[TINGKAT_CHIP[k]]; },
    semuaSehat() { return this.data.length > 0 && URUTAN_CHIP.every((k) => !this.chip[k]); },

    mulai() {
      this.muat();
      // Tanpa notifikasi, halaman yang dibiarkan terbuka adalah satu-satunya
      // tempat masalah baru terlihat.
      setInterval(() => this.muat(), 60000);
    },

    async muat() {
      // Tanpa penjagaan ini, penyegaran otomatis tiap 60 detik bisa saling
      // menumpuk kalau satu permintaan lebih lambat dari interval-nya
      // (koneksi lambat, server sibuk): beberapa fetch berjalan bersamaan dan
      // baris terakhir yang tiba yang menang, bukan yang paling baru diminta.
      if (this._sedangMemuat) return;
      this._sedangMemuat = true;
      try {
        const r = await fetch('/api/kesehatan');
        if (!r.ok) {
          this.galat = `Data kesehatan tidak dapat dimuat. ${await pesanGalat(r)}`;
          return;
        }
        const d = await r.json();
        this.galat = '';
        this.data = d.baris;
        this.chip = d.chip;
        this.diperbarui = new Date().toLocaleTimeString('id-ID');
        this.terapkan();
      } catch (e) {
        this.galat = `Gagal menghubungi server: ${e.message}`;
      } finally {
        this._sedangMemuat = false;
      }
    },

    pilihChip(k) {
      this.filter = this.filter === k ? null : k;
      this.terapkan();
    },

    terapkan() {
      const baris = this.filter ? this.data.filter((b) => b.masalah.includes(this.filter)) : this.data;
      if (this.grid) {
        this.grid.setData(baris);
        return;
      }
      this.grid = new DataGrid('#grid', {
        dataSource: baris,
        keyExpr: 'id',
        selection: false,
        columns: [
          {
            dataField: 'nama',
            caption: 'Site',
            cellTemplate: (v, b) => `<a href="/sites/${esc(b.id)}?tab=${esc(b.tab)}">${esc(v)}</a>`,
          },
          {
            dataField: 'uptime_status',
            caption: 'Uptime',
            cellTemplate: (v, b) => lencana(KELAS_UPTIME[v] || '', TEKS_UPTIME[v] || v)
              + (b.uptime_persen_24j == null ? '' : ` ${esc(b.uptime_persen_24j)}%`),
          },
          {
            dataField: 'keamanan',
            caption: 'Keamanan',
            cellTemplate: (v, b) => lencana(KELAS_KEAMANAN[v] || '', TEKS_KEAMANAN[v] || v)
              + (b.percobaan_sejam ? ` ${esc(b.percobaan_sejam)}/jam` : ''),
          },
          {
            dataField: 'error_baru',
            caption: 'Error',
            cellTemplate: (v, b) => (v
              ? lencana('dg-badge-warning', `${v} baru`) + (b.error_setelah_update ? ' · setelah update' : '')
              : '0'),
          },
          {
            dataField: 'traffic_kemarin',
            caption: 'Traffic kemarin',
            cellTemplate: (v, b) => `${v == null ? '—' : esc(v)}`
              + (b.anomali ? ' ' + lencana(b.anomali === 'anjlok' ? 'dg-badge-danger' : 'dg-badge-info', b.anomali) : ''),
          },
          {
            dataField: 'ssl_sisa_hari',
            caption: 'SSL',
            cellTemplate: (v, b) => {
              if (b.ssl_error) return lencana('dg-badge-danger', 'error');
              if (v == null) return '—';
              return v < 14 ? lencana('dg-badge-warning', `${v} hari`) : `${esc(v)} hari`;
            },
          },
          { dataField: 'koneksi', caption: 'Koneksi' },
          {
            dataField: 'connector_version',
            caption: 'Connector',
            cellTemplate: (v, b) => esc(v || '—')
              + (b.masalah.includes('connector_usang') ? ' ' + lencana('dg-badge-info', 'usang') : ''),
          },
        ],
      });
    },
  };
}
