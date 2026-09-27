const TEKS_JOB_STAGING = {
  staging_tarik: 'Menyalin dari produksi', staging_uji_update: 'Uji update di staging',
  staging_dorong: 'Mendorong ke produksi', staging_kembalikan: 'Mengembalikan produksi dari snapshot',
};
const TEKS_MODE_SNAPSHOT = {
  timpa_penuh: 'timpa penuh (berkas + database)', hanya_kode: 'hanya kode (berkas saja)',
};
// Cermin STATUS_SNAPSHOT_SAH di wpmgr.staging.dorong: hanya snapshot ini yang bisa dikembalikan.
const SNAPSHOT_SAH = ['tersedia', 'dipakai'];
const JEDA_POLLING = 3000;
// Galat yang tidak akan sembuh sendiri (sesi habis, site dicabut): polling berhenti.
const STATUS_BERHENTI = [401, 404];
// Galat lain (server sibuk, jaringan putus) dicoba ulang, tetapi tidak selamanya.
const MAKS_GAGAL_BERUNTUN = 5;

// Cermin pra-pemeriksaan 409 `_periksa_dorong` di routes_staging.py (R19),
// supaya tombol nonaktif dengan alasan yang terbaca. Server tetap memeriksa
// ulang dan pesannya tetap ditampilkan bila permintaan tetap dikirim.
const ALASAN_DORONG = {
  belumTarik: 'staging belum pernah selesai disalin dari produksi.',
  dijeda: 'staging sedang dijeda; jalankan dulu.',
  sibuk: 'staging sedang disegarkan atau diuji.',
  salinanGagal: 'Salinan staging belum utuh (penyegaran terakhir gagal); segarkan ulang sebelum mendorong.',
  job: 'tunggu pekerjaan staging yang sedang berjalan selesai.',
};

function tabStaging(siteId) {
  return {
    siteId,
    namaSite: '',
    pesanDiubah: '',
    tabAktif: false,
    data: null,
    galat: '',
    info: '',
    // Kata sandi preview hanya hidup di state komponen ini sampai ditutup;
    // tidak pernah disimpan di penyimpanan browser.
    sandi: '',
    dialogDorong: false,
    mode: 'hanya_kode',
    perubahan: null,
    memeriksa: false,
    konfirmasiNama: '',
    email: null,
    emailTerbuka: null,
    urlSso: '',
    _timer: null,
    _memuat: false,
    _muatLagi: false,
    _gagalBeruntun: 0,
    // Galat yang dipasang muat() sendiri; hanya itu yang dihapus saat muat()
    // berikutnya berhasil (galat aksi pengguna tetap tampil).
    _galatMuat: '',

    mulai(dataset, tab) {
      // Nama site dan pesan server datang lewat data-* (autoescape Jinja),
      // bukan lewat ekspresi Alpine.
      this.namaSite = dataset.nama || '';
      this.pesanDiubah = dataset.pesanDiubah || '';
      this.tabAktif = tab === 'staging';
      this.$watch('tab', (t) => {
        this.tabAktif = t === 'staging';
        this.sinkron();
      });
      document.addEventListener('visibilitychange', () => this.sinkron());
      this.sinkron();
    },

    terlihat() { return this.tabAktif && document.visibilityState === 'visible'; },

    // Polling hanya selama tab Staging terlihat; begitu tab ditinggalkan
    // atau halaman disembunyikan, timer dihentikan.
    sinkron() {
      if (this.terlihat()) {
        this.muat();
      } else {
        clearTimeout(this._timer);
        this._timer = null;
      }
    },

    dasar() { return `/api/sites/${this.siteId}/staging`; },

    async muat() {
      // Satu permintaan dalam perjalanan: panggilan di tengahnya ditandai
      // dan dijalankan sekali sesudahnya, bukan ditumpuk.
      if (this._memuat) {
        this._muatLagi = true;
        return;
      }
      this._memuat = true;
      clearTimeout(this._timer);
      this._timer = null;
      let adaJob = !!(this.data && this.data.job);
      let lanjut = true;
      try {
        const r = await fetch(this.dasar());
        if (!r.ok) {
          lanjut = !STATUS_BERHENTI.includes(r.status);
          throw new Error(await pesanGalat(r));
        }
        this.data = await r.json();
        adaJob = !!this.data.job;
        this._gagalBeruntun = 0;
        if (this._galatMuat && this.galat === this._galatMuat) this.galat = '';
        this._galatMuat = '';
      } catch (e) {
        this._gagalBeruntun += 1;
        if (this._gagalBeruntun >= MAKS_GAGAL_BERUNTUN) lanjut = false;
        this.galat = `Data staging tidak dapat dimuat. ${e.message}`
          + (lanjut ? '' : ' Pembaruan otomatis dihentikan; muat ulang halaman untuk mencoba lagi.');
        this._galatMuat = this.galat;
      } finally {
        this._memuat = false;
      }
      if (this._muatLagi && lanjut) {
        this._muatLagi = false;
        this.muat();
        return;
      }
      this._muatLagi = false;
      if (lanjut && adaJob && this.terlihat()) this._timer = setTimeout(() => this.muat(), JEDA_POLLING);
    },

    async kirim(method, url, body) {
      const r = await fetch(url, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!r.ok) throw new Error(await pesanGalat(r));
      return r.json();
    },

    mulaiAksi() {
      this.galat = '';
      this.info = '';
    },

    async buatAtauSegarkan(perluKonfirmasi) {
      this.mulaiAksi();
      if (perluKonfirmasi && !window.confirm('Staging diubah sejak tarik terakhir. Perubahan di staging akan tertimpa. Lanjutkan?')) return;
      try {
        const d = await this.kirim('POST', this.dasar(), { konfirmasi: !!perluKonfirmasi });
        if (d.sandi) this.sandi = d.sandi;
        this.info = 'Penyalinan dari produksi diantrekan.';
      } catch (e) {
        // Penanda "diubah" bisa baru terbaca server saat permintaan ini:
        // hanya pesan konfirmasi yang persis sama yang ditawarkan ulang.
        if (!perluKonfirmasi && this.pesanDiubah && e.message === `HTTP 409: ${this.pesanDiubah}`) {
          await this.buatAtauSegarkan(true);
          return;
        }
        this.galat = `Staging tidak dapat dibuat atau disegarkan. ${e.message}`;
      }
      this.muat();
    },

    async aksi(nama, sukses) {
      this.mulaiAksi();
      try {
        await this.kirim('POST', `${this.dasar()}/${nama}`);
        this.info = sukses;
      } catch (e) {
        this.galat = e.message;
      }
      this.muat();
    },

    async batal() {
      await this.aksi('batal', 'Pembatalan diminta; job berhenti di antara potongan.');
    },

    async sso() {
      this.mulaiAksi();
      try {
        const d = await this.kirim('GET', `${this.dasar()}/sso`);
        // Tautan cadangan: window.open sesudah await bisa diblokir popup blocker.
        this.urlSso = d.url;
        window.open(d.url, '_blank', 'noopener');
      } catch (e) {
        this.galat = `SSO staging gagal. ${e.message}`;
      }
    },

    async sandiBaru() {
      if (!window.confirm('Kata sandi preview lama langsung tidak berlaku. Lanjutkan?')) return;
      this.mulaiAksi();
      try {
        this.sandi = (await this.kirim('POST', `${this.dasar()}/sandi`)).sandi;
      } catch (e) {
        this.galat = `Kata sandi tidak dapat dibuat ulang. ${e.message}`;
      }
    },

    tutupSandi() { this.sandi = ''; },

    async hapus() {
      if (!window.confirm('Hapus staging ini? Snapshot produksi tetap disimpan.')) return;
      this.mulaiAksi();
      try {
        await this.kirim('DELETE', this.dasar());
        this.info = 'Staging dihapus.';
        this.sandi = '';
        this.urlSso = '';
        this.email = null;
        this.emailTerbuka = null;
        this.dialogDorong = false;
      } catch (e) {
        this.galat = `Staging tidak dihapus. ${e.message}`;
      }
      this.muat();
    },

    alasanDorong() {
      const d = this.data;
      if (!d || !d.staging) return '';
      const st = d.staging;
      if (!st.ditarik_pada) return ALASAN_DORONG.belumTarik;
      if (st.status === 'gagal' && st.gagal_asal === 'salinan') return ALASAN_DORONG.salinanGagal;
      if (st.status === 'menyalin' || st.status === 'berjalan_uji') return ALASAN_DORONG.sibuk;
      if (!st.aktif || st.status === 'dijeda') return ALASAN_DORONG.dijeda;
      if (d.job) return ALASAN_DORONG.job;
      return '';
    },

    bukaDorong() {
      this.mode = 'hanya_kode';
      this.perubahan = null;
      this.konfirmasiNama = '';
      this.dialogDorong = true;
    },

    bolehDorong() {
      if (this.alasanDorong()) return false;
      // Timpa penuh yang diketahui menimpa data baru butuh nama site persis;
      // bila belum diperiksa, job memeriksa sendiri dan menolak tanpa mengubah produksi.
      if (this.mode === 'timpa_penuh' && this.perubahan && this.perubahan.length) {
        return this.konfirmasiNama === this.namaSite;
      }
      return true;
    },

    async cekTandaAir() {
      this.mulaiAksi();
      this.perubahan = null;
      this.memeriksa = true;
      try {
        const d = await this.kirim('GET', `${this.dasar()}/tanda-air`);
        this.perubahan = Array.isArray(d.perubahan) ? d.perubahan : [];
      } catch (e) {
        this.galat = `Data baru di produksi tidak dapat diperiksa. ${e.message}`;
      } finally {
        this.memeriksa = false;
      }
    },

    async dorong() {
      this.mulaiAksi();
      try {
        await this.kirim('POST', `${this.dasar()}/dorong`,
          { mode: this.mode, konfirmasi_nama: this.mode === 'timpa_penuh' ? this.konfirmasiNama : null });
        this.dialogDorong = false;
        this.info = 'Dorongan diantrekan. Snapshot produksi dibuat lebih dulu.';
      } catch (e) {
        this.galat = `Dorongan tidak diantrekan. ${e.message}`;
      }
      this.muat();
    },

    // Tidak digerbangi keadaan salinan (R19): pemulihan produksi tetap bisa
    // berjalan walau staging gagal atau sudah dihapus.
    async kembalikan(id, mode) {
      const catatan = mode === 'timpa_penuh'
        ? 'Berkas dan database produksi akan dikembalikan ke kondisi sebelum dorongan.'
        : 'Hanya berkas yang dikembalikan; database tidak disentuh (ekspor database tetap ada di snapshot untuk pemulihan manual).';
      const nama = window.prompt(`${catatan}\nKetik nama site untuk melanjutkan:`);
      if (nama === null) return;
      this.mulaiAksi();
      try {
        await this.kirim('POST', `${this.dasar()}/kembalikan`, { snapshot_id: id, konfirmasi_nama: nama });
        this.info = 'Pengembalian diantrekan.';
      } catch (e) {
        this.galat = `Pengembalian tidak diantrekan. ${e.message}`;
      }
      this.muat();
    },

    async muatEmail() {
      this.mulaiAksi();
      try {
        const d = await this.kirim('GET', `${this.dasar()}/email`);
        this.email = Array.isArray(d) ? d : [];
      } catch (e) {
        this.galat = `Kotak email staging tidak dapat dimuat. ${e.message}`;
      }
    },

    async bukaEmail(id) {
      this.mulaiAksi();
      try {
        this.emailTerbuka = await this.kirim('GET', `${this.dasar()}/email/${encodeURIComponent(id)}`);
      } catch (e) {
        this.galat = `Email tidak dapat dibuka. ${e.message}`;
      }
    },

    async salin(teks) {
      try {
        await navigator.clipboard.writeText(teks);
        this.info = 'Kata sandi disalin.';
      } catch (e) {
        this.galat = 'Salin manual: clipboard tidak diizinkan browser.';
      }
    },

    teksPaket(paket) {
      if (!Array.isArray(paket)) return '—';
      return paket.map((p) => `${p.slug} ${p.dari || '?'} → ${p.ke}`).join(', ');
    },
    teksMode(mode) { return TEKS_MODE_SNAPSHOT[mode] || '—'; },
    snapshotSah(s) { return SNAPSHOT_SAH.includes(s.status); },
    aman(url) { return typeof url === 'string' && url.startsWith('https://') ? url : '#'; },
    teksJob(tipe) { return TEKS_JOB_STAGING[tipe] || tipe; },
    waktu(iso) { return iso ? new Date(iso).toLocaleString('id-ID') : '—'; },
  };
}
