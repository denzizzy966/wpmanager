const TEKS_JOB_HOSTING = {
  pindah_tarik: 'Menyalin ke VPS', pindah_aktifkan: 'Mengaktifkan di VPS', backup_hosting: 'Backup situs',
};
// Langkah aktivasi sesudah prod-aktifkan dikirim: batal tidak berlaku lagi
// (cermin LANGKAH_AKTIFKAN_SESUDAH_TUKAR di wpmgr.jobs.queue).
const LANGKAH_SESUDAH_TUKAR = ['tukar', 'verifikasi', 'beres'];
const JEDA_POLLING_HOSTING = 3000;
const JEDA_POLLING_DNS = 30000;
const STATUS_BERHENTI_HOSTING = [401, 404];
const MAKS_GAGAL_HOSTING = 5;
const TEKS_SIBUK_HOSTING = 'Menunggu pekerjaan hosting yang sedang berjalan selesai.';
// Teks tetap (final review I2): hosting lama bisa sudah mati, jadi salinan VPS mungkin satu-satunya.
const TEKS_BATAL_PINDAH = 'Container, database, dan salinan di VPS akan dihapus (site lama tidak disentuh). '
  + 'PERINGATAN: bila hosting lama sudah mati atau tidak terjangkau, salinan VPS ini mungkin '
  + 'satu-satunya salinan site yang tersisa dan tidak bisa dikembalikan. Ketik domain untuk konfirmasi:';

// Semua teks dari server (domain, DNS, instruksi, galat, job) hanya masuk ke
// DOM lewat x-text; tidak ada HTML mentah. Kata sandi pratinjau hanya hidup di
// state komponen ini dan tidak pernah disimpan di storage browser.
function tabHosting(siteId) {
  return {
    siteId,
    tabAktif: false,
    data: null,
    galat: '',
    info: '',
    sandi: '',
    _timer: null,
    _memuat: false,
    _ulang: false,
    _gagalBeruntun: 0,
    _galatMuat: '',

    mulai(tab) {
      this.tabAktif = tab === 'hosting';
      this.$watch('tab', (t) => {
        this.tabAktif = t === 'hosting';
        if (!this.tabAktif) this.sandi = '';
        this.sinkron();
      });
      document.addEventListener('visibilitychange', () => this.sinkron());
      this.sinkron();
    },

    terlihat() { return this.tabAktif && document.visibilityState === 'visible'; },

    sinkron() {
      if (this.terlihat()) {
        this.muat();
      } else {
        clearTimeout(this._timer);
        this._timer = null;
      }
    },

    dasar() { return `/api/sites/${this.siteId}/hosting`; },

    // 3 detik selama ada job aktif (atau status yang pasti berarti ada job); 30 detik saat menunggu DNS
    // tanpa job, supaya aktivasi otomatis cron terlihat (putusan L20).
    jedaPolling() {
      if (!this.data) return null;
      if (this.data.job) return JEDA_POLLING_HOSTING;
      const status = this.data.hosting ? this.data.hosting.status : null;
      if (status === 'menyalin' || status === 'mengaktifkan') return JEDA_POLLING_HOSTING;
      return status === 'menunggu_dns' ? JEDA_POLLING_DNS : null;
    },

    async muat() {
      // Tidak pernah dua permintaan bersamaan; permintaan yang tertahan diulang sesudahnya.
      if (this._memuat) { this._ulang = true; return; }
      this._memuat = true;
      this._ulang = false;
      clearTimeout(this._timer);
      this._timer = null;
      let lanjut = true;
      try {
        const r = await fetch(this.dasar());
        if (!r.ok) {
          lanjut = !STATUS_BERHENTI_HOSTING.includes(r.status);
          throw new Error(await pesanGalat(r));
        }
        this.data = await r.json();
        this._gagalBeruntun = 0;
        if (this._galatMuat && this.galat === this._galatMuat) this.galat = '';
        this._galatMuat = '';
      } catch (e) {
        this._gagalBeruntun += 1;
        if (this._gagalBeruntun >= MAKS_GAGAL_HOSTING) lanjut = false;
        this.galat = `Data hosting tidak dapat dimuat. ${e.message}`
          + (lanjut ? '' : ' Pembaruan otomatis dijeda; buka ulang tab atau muat ulang halaman.');
        this._galatMuat = this.galat;
      } finally {
        this._memuat = false;
      }
      if (this._ulang && lanjut && this.terlihat()) { this.muat(); return; }
      const jeda = this.jedaPolling();
      if (lanjut && jeda && this.terlihat()) this._timer = setTimeout(() => this.muat(), jeda);
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

    async pindahkan() {
      if (this.alasanPindah()) return;
      if (!window.confirm('Salin seluruh berkas dan database site ini ke VPS? Site lama tidak diubah.')) return;
      this.mulaiAksi();
      try {
        const d = await this.kirim('POST', this.dasar(), {});
        this.sandi = d.sandi;
        this.info = 'Penyalinan ke VPS diantrekan.';
      } catch (e) {
        this.galat = `Pindah hosting tidak dapat dimulai. ${e.message}`;
      }
      this.muat();
    },

    async salinUlang() {
      if (!window.confirm('Salin ulang dari hosting lama? Perubahan di salinan VPS akan tertimpa.')) return;
      await this.aksi('tarik', 'Salin ulang diantrekan.');
    },

    async sandiBaru() {
      if (!window.confirm('Kata sandi pratinjau lama langsung tidak berlaku. Lanjutkan?')) return;
      this.mulaiAksi();
      try {
        this.sandi = (await this.kirim('POST', `${this.dasar()}/sandi`)).sandi;
      } catch (e) {
        this.galat = `Kata sandi tidak dapat dibuat ulang. ${e.message}`;
      }
    },

    tutupSandi() { this.sandi = ''; },

    async aktifkan(tanpaTarik, konfirmasi) {
      this.mulaiAksi();
      try {
        await this.kirim('POST', `${this.dasar()}/aktifkan`,
          { tanpa_tarik_ulang: !!tanpaTarik, konfirmasi: konfirmasi || '' });
        this.info = 'Aktivasi diantrekan; jangan ubah DNS sampai selesai.';
      } catch (e) {
        // Pesan 409/400 server (termasuk hasil cek DNS) tampil apa adanya sebagai teks.
        this.galat = `Belum bisa diaktifkan. ${e.message}`;
      }
      this.muat();
    },

    async aktifkanTanpaSalin() {
      if (this.alasanTanpaSalin()) return;
      const h = this.data.hosting;
      // Hanya dari status dengan salinan utuh (menunggu DNS), jadi salinannya memang dari ditarik_pada.
      const domain = window.prompt(`Aktifkan memakai salinan VPS dari ${this.waktu(h.ditarik_pada)} tanpa menyalin `
        + 'ulang dari hosting lama. Semua perubahan di site lama sejak itu hilang (pos, halaman, isian form, '
        + 'pengguna) dan tidak bisa diambil lagi bila hosting lama sudah mati. Ketik domain untuk konfirmasi:');
      if (domain === null) return;
      await this.aktifkan(true, domain);
    },

    async batalkanPindah() {
      const domain = window.prompt(TEKS_BATAL_PINDAH);
      if (domain === null) return;
      this.mulaiAksi();
      try {
        await this.kirim('DELETE', this.dasar(), { konfirmasi: domain });
        this.info = 'Pindah hosting dibatalkan.';
      } catch (e) {
        this.galat = `Pindah hosting tidak dapat dibatalkan. ${e.message}`;
      }
      this.muat();
    },

    async backupSekarang() {
      await this.aksi('backup', 'Backup diantrekan.');
    },

    // Alasan tombol nonaktif; string kosong = boleh. Cermin prasyarat API (409 tetap ditampilkan).
    alasanPindah() {
      if (!this.data) return 'Memuat…';
      if (!this.data.izin_connector) return 'Connector site ini belum mengizinkan staging.';
      return this.data.job ? TEKS_SIBUK_HOSTING : '';
    },

    alasanTanpaSalin() {
      const h = this.data ? this.data.hosting : null;
      if (!h) return '';
      if (this.data.job) return TEKS_SIBUK_HOSTING;
      // Cermin route/job: hanya salinan yang diketahui utuh (Koreksi I2.3).
      if (!h.ditarik_pada) return 'Belum ada salinan utuh; salin dulu.';
      if (h.status === 'gagal' && h.gagal_asal === 'salinan') {
        return 'Salinan terakhir setengah jadi; hanya salin ulang yang bisa merampungkannya.';
      }
      return '';
    },

    alasanTanpaBatal() {
      const job = this.data ? this.data.job : null;
      if (!job) return '';
      if (job.tipe === 'backup_hosting') return 'Backup tidak bisa dibatalkan.';
      return 'Aktivasi sudah mengubah VPS dan tidak bisa dibatalkan lagi.';
    },

    barisHosts() {
      if (!this.data || !this.data.hosting) return '';
      const h = this.data.hosting;
      return `${this.data.ipv4} ${h.domain}${h.dengan_www ? ` www.${h.domain}` : ''}`;
    },

    teksAksi(r) {
      if (r.aksi === 'ikut_apex') {
        return r.jenis === 'A' ? 'Tidak perlu diubah: www adalah CNAME ke domain utama, ikut record @'
          : 'Ikut record @';
      }
      if (r.aksi === 'setelah_cname') return 'Selesai sesudah CNAME diganti A';
      if (r.jenis === 'CAA') return 'Tambahkan agar Let\'s Encrypt boleh menerbitkan sertifikat';
      if (r.aksi === 'hapus') return r.nilai ? 'Hapus record ini' : 'Pastikan tidak ada';
      if (r.jenis === 'AAAA') return 'Ubah atau buat (IPv6 VPS)';
      return r.cname ? 'Hapus CNAME, lalu buat A' : 'Ubah atau buat';
    },

    adaCdn() {
      const h = this.data ? this.data.hosting : null;
      if (!h) return false;
      if ((h.instruksi || []).some((r) => r.cname)) return true;
      const nama = h.dns_hasil && Array.isArray(h.dns_hasil.nama) ? h.dns_hasil.nama : [];
      return nama.some((x) => x && x.kode === 'cname');
    },

    teksDns() {
      const hasil = this.data && this.data.hosting ? this.data.hosting.dns_hasil : null;
      if (!hasil) return 'belum pernah diperiksa';
      return hasil.ok ? 'DNS sudah menunjuk VPS' : 'belum menunjuk VPS sepenuhnya';
    },

    bolehBatal() {
      const job = this.data ? this.data.job : null;
      if (!job || job.tipe === 'backup_hosting') return false;
      return !(job.tipe === 'pindah_aktifkan' && LANGKAH_SESUDAH_TUKAR.includes(job.progres.tahap));
    },

    lebar() {
      const p = this.data && this.data.job ? Number(this.data.job.progres.persen) : 0;
      return Number.isFinite(p) ? Math.max(0, Math.min(100, p)) : 0;
    },

    async salin(teks) {
      try {
        await navigator.clipboard.writeText(teks);
        this.info = 'Tersalin.';
      } catch (e) {
        this.info = 'Tidak dapat menyalin otomatis; salin manual.';
      }
    },

    aman(url) { return typeof url === 'string' && url.startsWith('https://') ? url : '#'; },
    teksJob(tipe) { return TEKS_JOB_HOSTING[tipe] || tipe; },
    waktu(iso) { return iso ? new Date(iso).toLocaleString('id-ID') : '—'; },
  };
}
