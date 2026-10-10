// Perilaku kerangka aplikasi: tema terang/gelap, sidebar ciut, laci menu di
// layar kecil, dan tombol lihat kata sandi. Tidak menyentuh data server.
(function () {
  'use strict';

  const akar = document.documentElement;
  const KUNCI_TEMA = 'wpmgr-tema';
  const KUNCI_SISI = 'wpmgr-sisi';
  const layarKecil = window.matchMedia('(max-width: 899.98px)');
  const skemaGelap = window.matchMedia('(prefers-color-scheme: dark)');

  function baca(kunci) {
    try { return localStorage.getItem(kunci); } catch (e) { return null; }
  }

  function simpan(kunci, nilai) {
    try { localStorage.setItem(kunci, nilai); } catch (e) { /* storage diblokir: pilihan berlaku sampai halaman ditutup */ }
  }

  // ---- Tema ----
  function sinkronTombolTema() {
    const gelap = akar.getAttribute('data-theme') === 'dark';
    document.querySelectorAll('[data-aksi="tema"]').forEach((b) => b.setAttribute('aria-pressed', String(gelap)));
  }

  function pasangTema(tema) {
    akar.setAttribute('data-theme', tema);
    sinkronTombolTema();
  }

  // Tanpa pilihan tersimpan, tema mengikuti sistem operasi, termasuk bila sistem berganti.
  const ikutiSistem = (e) => { if (!baca(KUNCI_TEMA)) pasangTema(e.matches ? 'dark' : 'light'); };
  if (skemaGelap.addEventListener) skemaGelap.addEventListener('change', ikutiSistem);

  // ---- Sidebar ----
  const sisi = document.getElementById('sisi');
  const tirai = document.querySelector('.tirai');
  const tombolBuka = document.querySelector('[data-aksi="buka-menu"]');
  const tombolCiut = document.querySelector('[data-aksi="ciutkan"]');

  function ciut() { return akar.getAttribute('data-sisi') === 'ciut'; }

  // Tooltip (atribut title) hanya saat sidebar menjadi rel ikon.
  function sinkronCiut() {
    if (!sisi) return;
    const rel = ciut() && !layarKecil.matches;
    sisi.querySelectorAll('.menu a, .sisi-tombol, .akun').forEach((el) => {
      const label = el.querySelector('.menu-label, .akun-email');
      if (!label) return;
      if (rel) el.setAttribute('title', label.textContent.trim());
      else if (!el.classList.contains('akun')) el.removeAttribute('title');
    });
    if (tombolCiut) {
      tombolCiut.setAttribute('aria-expanded', String(!ciut()));
      tombolCiut.setAttribute('aria-label', ciut() ? 'Lebarkan sidebar' : 'Ciutkan sidebar');
      tombolCiut.setAttribute('title', ciut() ? 'Lebarkan sidebar' : 'Ciutkan sidebar');
    }
  }

  function laciBuka() { return akar.getAttribute('data-laci') === 'buka'; }

  function bukaLaci() {
    if (!sisi) return;
    akar.setAttribute('data-laci', 'buka');
    if (tirai) tirai.hidden = false;
    if (tombolBuka) tombolBuka.setAttribute('aria-expanded', 'true');
    const pertama = sisi.querySelector('.menu a');
    // Tunggu satu frame: elemen baru bisa difokus setelah visibility berubah.
    requestAnimationFrame(() => { if (pertama) pertama.focus(); });
  }

  function tutupLaci(kembalikanFokus) {
    if (!laciBuka()) return;
    akar.removeAttribute('data-laci');
    if (tirai) tirai.hidden = true;
    if (tombolBuka) {
      tombolBuka.setAttribute('aria-expanded', 'false');
      if (kembalikanFokus) tombolBuka.focus();
    }
  }

  document.addEventListener('click', (e) => {
    const el = e.target.closest('[data-aksi]');
    if (el) {
      const aksi = el.getAttribute('data-aksi');
      if (aksi === 'tema') {
        const baru = akar.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        pasangTema(baru);
        simpan(KUNCI_TEMA, baru);
      } else if (aksi === 'ciutkan') {
        if (ciut()) akar.removeAttribute('data-sisi');
        else akar.setAttribute('data-sisi', 'ciut');
        simpan(KUNCI_SISI, ciut() ? 'ciut' : 'lebar');
        sinkronCiut();
      } else if (aksi === 'buka-menu') {
        bukaLaci();
      } else if (aksi === 'tutup-menu') {
        tutupLaci(true);
      }
      return;
    }
    // Navigasi dari laci menutup laci (halaman berikutnya dimuat dalam keadaan tertutup juga).
    if (laciBuka() && e.target.closest('#sisi a')) tutupLaci(false);
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && laciBuka() && !document.querySelector('dialog[open]')) {
      tutupLaci(true);
    }
  });

  const gantiLayar = () => { if (!layarKecil.matches) tutupLaci(false); sinkronCiut(); };
  if (layarKecil.addEventListener) layarKecil.addEventListener('change', gantiLayar);

  // ---- Lihat kata sandi ----
  document.querySelectorAll('[data-lihat-sandi]').forEach((tombol) => {
    const isian = document.getElementById(tombol.getAttribute('data-lihat-sandi'));
    if (!isian) return;
    tombol.addEventListener('click', () => {
      const lihat = isian.type === 'password';
      isian.type = lihat ? 'text' : 'password';
      tombol.setAttribute('aria-pressed', String(lihat));
      tombol.setAttribute('aria-label', lihat ? 'Sembunyikan kata sandi' : 'Tampilkan kata sandi');
    });
  });

  sinkronTombolTema();
  sinkronCiut();
}());
