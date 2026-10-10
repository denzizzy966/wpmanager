// Dialog dan toast pengganti alert/confirm/prompt bawaan browser.
//
// Semua teks (judul, pesan, label tombol) dipasang lewat textContent: pesan
// bisa memuat string dari server atau site client dan tidak boleh pernah
// menjadi markup. Elemen dibangun dengan DOM API saja.
//
//   dialogKonfirmasi({judul, pesan, teksYa, teksTidak, bahaya}) -> Promise<boolean>
//   dialogTanya({judul, pesan, placeholder, teksYa, bahaya})     -> Promise<string|null>
//   dialogInfo({judul, pesan})                                   -> Promise<void>
//   toast(pesan, jenis)   jenis: 'info' | 'sukses' | 'galat'
(function () {
  'use strict';

  let nomor = 0;

  function el(tag, kelas, teks) {
    const e = document.createElement(tag);
    if (kelas) e.className = kelas;
    if (teks !== undefined && teks !== null) e.textContent = String(teks);
    return e;
  }

  function dapatFokus(akar) {
    return Array.from(akar.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
    )).filter((x) => x.offsetParent !== null || x === document.activeElement);
  }

  // Inti: membangun <dialog> modal, mengembalikan Promise yang selesai saat
  // dialog ditutup dengan nilai dari `hasilYa()` atau `nilaiBatal`.
  function buka(opsi) {
    const {
      judul, pesan, teksYa, teksTidak, bahaya, isian, placeholder, nilaiBatal, hasilYa, tanpaBatal,
    } = opsi;
    nomor += 1;
    const idJudul = `dialog-judul-${nomor}`;
    const idPesan = `dialog-pesan-${nomor}`;
    const pembuka = document.activeElement;

    const dlg = el('dialog', `dialog${bahaya ? ' dialog-bahaya' : ''}`);
    // Konfirmasi dan pertanyaan menuntut jawaban: alertdialog. Info biasa: dialog.
    dlg.setAttribute('role', tanpaBatal ? 'dialog' : 'alertdialog');
    dlg.setAttribute('aria-modal', 'true');
    dlg.setAttribute('aria-labelledby', idJudul);
    dlg.setAttribute('aria-describedby', idPesan);

    const form = el('form', 'dialog-isi');
    form.setAttribute('method', 'dialog');
    form.noValidate = true;

    const kepala = el('div', 'dialog-kepala');
    if (bahaya) {
      const ikon = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      ikon.setAttribute('viewBox', '0 0 24 24');
      ikon.setAttribute('aria-hidden', 'true');
      ikon.setAttribute('class', 'dialog-ikon');
      const jalur = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      jalur.setAttribute('d', 'M12 4 2.8 19.5h18.4zM12 10v4.5M12 17.2h.01');
      ikon.appendChild(jalur);
      kepala.appendChild(ikon);
    }
    const h = el('h2', 'dialog-judul', judul || 'Konfirmasi');
    h.id = idJudul;
    kepala.appendChild(h);
    form.appendChild(kepala);

    const p = el('p', 'dialog-pesan', pesan || '');
    p.id = idPesan;
    form.appendChild(p);

    let input = null;
    if (isian) {
      input = el('input', 'dialog-isian');
      input.type = 'text';
      input.autocomplete = 'off';
      input.spellcheck = false;
      input.setAttribute('autocapitalize', 'off');
      input.setAttribute('aria-labelledby', idPesan);
      if (placeholder) input.placeholder = String(placeholder);
      form.appendChild(input);
    }

    const aksi = el('div', 'dialog-aksi');
    let tombolTidak = null;
    if (!tanpaBatal) {
      tombolTidak = el('button', '', teksTidak || 'Batal');
      tombolTidak.type = 'button';
      aksi.appendChild(tombolTidak);
    }
    const tombolYa = el('button', bahaya ? 'bahaya-penuh' : 'utama', teksYa || 'Lanjutkan');
    tombolYa.type = 'submit';
    aksi.appendChild(tombolYa);
    form.appendChild(aksi);
    dlg.appendChild(form);
    document.body.appendChild(dlg);

    return new Promise((selesai) => {
      let tertutup = false;

      function tutup(nilai) {
        if (tertutup) return;
        tertutup = true;
        document.removeEventListener('keydown', jebakFokus, true);
        if (dlg.open) dlg.close();
        dlg.remove();
        if (pembuka && typeof pembuka.focus === 'function' && document.contains(pembuka)) {
          pembuka.focus();
        }
        selesai(nilai);
      }

      // Fokus tetap di dalam dialog: Tab dari elemen terakhir kembali ke yang pertama.
      function jebakFokus(e) {
        if (e.key !== 'Tab' || !dlg.open) return;
        const daftar = dapatFokus(dlg);
        if (!daftar.length) return;
        const awal = daftar[0];
        const akhir = daftar[daftar.length - 1];
        if (e.shiftKey && (document.activeElement === awal || !dlg.contains(document.activeElement))) {
          e.preventDefault();
          akhir.focus();
        } else if (!e.shiftKey && (document.activeElement === akhir || !dlg.contains(document.activeElement))) {
          e.preventDefault();
          awal.focus();
        }
      }
      document.addEventListener('keydown', jebakFokus, true);

      form.addEventListener('submit', (e) => {
        e.preventDefault();
        tutup(hasilYa(input));
      });
      if (tombolTidak) tombolTidak.addEventListener('click', () => tutup(nilaiBatal));
      // Esc = batal (event "cancel" bawaan <dialog>).
      dlg.addEventListener('cancel', (e) => {
        e.preventDefault();
        tutup(nilaiBatal);
      });
      // Penutupan paksa oleh browser (mis. Esc berulang) tetap dihitung batal.
      dlg.addEventListener('close', () => tutup(nilaiBatal));
      // Klik pada latar (di luar kotak dialog) = batal. Hanya bila tekanannya juga
      // dimulai di latar: seret teks dari isian lalu lepas di luar tidak menutup.
      let tekanDiLatar = false;
      dlg.addEventListener('pointerdown', (e) => { tekanDiLatar = e.target === dlg; });
      dlg.addEventListener('click', (e) => {
        if (e.target === dlg && tekanDiLatar) tutup(nilaiBatal);
      });

      if (typeof dlg.showModal === 'function') {
        dlg.showModal();
      } else {
        dlg.setAttribute('open', '');
      }
      // Dialog berbahaya memfokus Batal: Enter tidak langsung menjalankan aksi.
      const fokusAwal = input || (bahaya && tombolTidak) || tombolYa;
      fokusAwal.focus();
    });
  }

  function dialogKonfirmasi(opsi = {}) {
    return buka({
      ...opsi,
      isian: false,
      nilaiBatal: false,
      hasilYa: () => true,
    });
  }

  function dialogTanya(opsi = {}) {
    return buka({
      ...opsi,
      isian: true,
      nilaiBatal: null,
      hasilYa: (input) => input.value,
    });
  }

  function dialogInfo(opsi = {}) {
    return buka({
      judul: opsi.judul || 'Informasi',
      pesan: opsi.pesan,
      teksYa: opsi.teksYa || 'Tutup',
      tanpaBatal: true,
      isian: false,
      nilaiBatal: undefined,
      hasilYa: () => undefined,
    });
  }

  // ---- Toast ----
  const LAMA_TOAST = { info: 4500, sukses: 4500, galat: 7000 };
  let wadahToast = null;

  function toast(pesan, jenis = 'info') {
    const j = Object.prototype.hasOwnProperty.call(LAMA_TOAST, jenis) ? jenis : 'info';
    if (!wadahToast || !document.body.contains(wadahToast)) {
      wadahToast = el('div', 'toast-wadah');
      wadahToast.setAttribute('aria-live', 'polite');
      wadahToast.setAttribute('aria-atomic', 'false');
      document.body.appendChild(wadahToast);
    }
    const t = el('div', `toast toast-${j}`, pesan);
    if (j === 'galat') t.setAttribute('role', 'alert');
    const hapus = () => {
      if (!t.isConnected) return;
      t.classList.add('toast-keluar');
      setTimeout(() => t.remove(), 200);
    };
    t.addEventListener('click', hapus);
    wadahToast.appendChild(t);
    setTimeout(hapus, LAMA_TOAST[j]);
  }

  window.dialogKonfirmasi = dialogKonfirmasi;
  window.dialogTanya = dialogTanya;
  window.dialogInfo = dialogInfo;
  window.toast = toast;
}());
