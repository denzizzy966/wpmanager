const TAB_SAH = ['ringkasan', 'paket', 'uptime', 'error', 'login', 'traffic', 'staging', 'aktivitas'];
const TEKS_KEAMANAN_DETAIL = { aman: 'Aman', diserang: 'Diserang', perlu_diperiksa: 'Perlu diperiksa' };

function detailSite(siteId, tabAwal) {
  return {
    siteId,
    tab: tabAwal,
    galat: '',
    info: '',
    ga4: '',
    uptime: null,
    errors: null,
    logins: null,
    traffic: null,

    mulai() {
      // Tab dari URL (tautan halaman Kesehatan) didahulukan; tanpa itu, tab
      // terakhir yang dibuka di browser ini.
      if (!new URLSearchParams(location.search).get('tab')) {
        try {
          const t = localStorage.getItem('wpmgr_tab');
          // Tab Staging hanya ada bila fitur staging menyala (staging.js dimuat).
          if (TAB_SAH.includes(t) && (t !== 'staging' || typeof tabStaging === 'function')) this.tab = t;
        } catch (e) { /* localStorage bisa diblokir; tab bawaan tetap berlaku */ }
      }
      this.muatTab();
    },

    pilih(t) {
      this.tab = t;
      try { localStorage.setItem('wpmgr_tab', t); } catch (e) { /* abaikan */ }
      history.replaceState(null, '', `?tab=${encodeURIComponent(t)}`);
      this.muatTab();
    },

    async ambil(url) {
      const r = await fetch(url);
      if (!r.ok) throw new Error(await pesanGalat(r));
      return r.json();
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

    async muatTab() {
      this.galat = '';
      const dasar = `/api/sites/${this.siteId}`;
      try {
        if (this.tab === 'uptime') this.uptime = await this.ambil(`${dasar}/uptime?hari=30`);
        if (this.tab === 'error') this.errors = await this.ambil(`${dasar}/errors`);
        if (this.tab === 'login') this.logins = await this.ambil(`${dasar}/logins?hari=30`);
        if (this.tab === 'traffic') this.traffic = await this.ambil(`${dasar}/traffic?hari=30`);
      } catch (e) {
        this.galat = `Data tidak dapat dimuat. ${e.message}`;
      }
    },

    async tandaiSelesai(id) {
      try {
        await this.kirim('POST', `/api/sites/${this.siteId}/errors/${id}/selesai`);
        await this.muatTab();
      } catch (e) {
        this.galat = `Error tidak dapat ditandai selesai. ${e.message}`;
      }
    },

    async sudahDiperiksa() {
      try {
        await this.kirim('POST', `/api/sites/${this.siteId}/keamanan/diperiksa`);
        await this.muatTab();
      } catch (e) {
        this.galat = `Status tidak dapat diperbarui. ${e.message}`;
      }
    },

    async simpanGa4() {
      this.info = '';
      try {
        await this.kirim('PUT', `/api/sites/${this.siteId}/ga4`, { property_id: this.ga4 });
        this.info = 'Property GA4 disimpan. Data diambil pada pengambilan harian berikutnya.';
      } catch (e) {
        this.galat = `Property GA4 tidak disimpan. ${e.message}`;
      }
    },

    async sso() {
      try {
        const d = await this.ambil(`/api/sso/${this.siteId}`);
        window.open(d.url, '_blank', 'noopener');
      } catch (e) {
        this.galat = `SSO gagal. ${e.message}`;
      }
    },

    async scan() {
      this.info = '';
      try {
        await this.kirim('POST', '/api/jobs/scan', { site_id: this.siteId });
        this.info = 'Scan diantrekan.';
      } catch (e) {
        this.galat = `Scan tidak diantrekan. ${e.message}`;
      }
    },

    panelTraffic() {
      if (!this.traffic) return [];
      const panel = [{
        kunci: 'plugin', label: 'penghitung plugin', data: this.traffic.plugin,
        kosong: 'Belum ada data dari penghitung plugin. Data mulai terkumpul setelah connector 2.x terpasang.',
      }];
      if (this.traffic.ga4_terpasang) {
        panel.push({
          kunci: 'ga4', label: 'Google Analytics', data: this.traffic.ga4,
          kosong: this.traffic.ga4_error || 'Data GA4 belum diambil. Pengambilan berjalan sekali sehari.',
        });
      }
      return panel;
    },

    tinggi(n, maks) {
      return !n || !maks ? 0 : Math.max(2, Math.round((n / maks) * 100));
    },

    teksKeamanan(s) { return TEKS_KEAMANAN_DETAIL[s] || s; },
    persen(p) { return p == null ? '—' : `${p}%`; },
    waktu(iso) { return iso ? new Date(iso).toLocaleString('id-ID') : '—'; },
  };
}
