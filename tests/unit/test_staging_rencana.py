import hashlib

import pytest

from wpmgr.staging.pembantu import StatusPembantu
from wpmgr.staging.rencana import (
    BalasanTidakSah,
    Entri,
    RakitRentang,
    bagi_potongan,
    bandingkan_tanda_air,
    cek_disk,
    cek_maks_aktif,
    cek_ram,
    entri_dari,
    format_byte,
    halaman_manifest,
    hasil_paket,
    potongan_tabel,
    rencana_dorong,
    selisih,
    urai_tanda_air,
)

H1, H2 = "1" * 64, "2" * 64
GB = 1024**3


def e(path, ukuran=10, mtime=100, h=H1):
    return Entri(path, ukuran, mtime, h)


def test_entri_dari_menolak_masukan_berbahaya():
    assert entri_dari({"path": "a.php", "ukuran": 3, "mtime": 5, "hash": H1}) == Entri("a.php", 3, 5, H1)
    assert entri_dari({"path": "big.bin", "ukuran": 3, "mtime": 5, "hash": None}).hash is None
    for buruk in ({"path": "../x", "ukuran": 1, "mtime": 1}, {"path": "wp-config.php", "ukuran": 1, "mtime": 1},
                  {"path": "a", "ukuran": -1, "mtime": 1}, {"path": "a", "ukuran": True, "mtime": 1},
                  {"path": "a", "ukuran": 1, "mtime": "1"}, {"path": "a", "ukuran": 2**51, "mtime": 1},
                  {"path": "a", "ukuran": 1, "mtime": 1, "hash": "XYZ"}, {"path": "a\ud800", "ukuran": 1, "mtime": 1},
                  "bukan-dict", None):
        assert entri_dari(buruk) is None, buruk


def test_selisih_baru_berubah_hapus_dan_perlindungan():
    produksi = {p.path: p for p in [e("a"), e("b", h=H2), e("besar", ukuran=99, mtime=7, h=None), e("c")]}
    lokal = {p.path: p for p in [e("b"), e("besar", ukuran=99, mtime=8, h=None), e("c"), e("lama"),
                                 e("wp-config.php"), e("wp-content/mu-plugins/wpmgr-staging.php")]}
    s = selisih(produksi, lokal)
    assert [x.path for x in s.baru] == ["a"]
    assert [x.path for x in s.berubah] == ["b", "besar"]
    assert s.hapus == ["lama"]
    assert s.byte == 10 + 10 + 99


def test_selisih_tidak_menimpa_berkas_milik_staging():
    # Produksi yang kebetulan (atau sengaja) punya mu-plugin bernama sama
    # tidak boleh menimpa mu-plugin staging saat tarik.
    produksi = {"wp-content/mu-plugins/wpmgr-staging.php": e("wp-content/mu-plugins/wpmgr-staging.php", h=H2)}
    s = selisih(produksi, {"wp-content/mu-plugins/wpmgr-staging.php": e("wp-content/mu-plugins/wpmgr-staging.php")})
    assert s.diambil == [] and s.hapus == []
    assert selisih(produksi, {}).diambil == []


def test_hash_menang_atas_mtime_bila_keduanya_ada():
    s = selisih({"a": e("a", mtime=1)}, {"a": e("a", mtime=2)})
    assert s.berubah == []


def test_bagi_potongan_paket_dan_rentang():
    entri = [e("a", 3), e("b", 4), e("besar", 20, h=None), e("c", 5), e("d", 0)]
    p = bagi_potongan(entri, ukuran_paket=8, maks_berkas=2)
    ringkas = [(x.jenis, tuple(b.path for b in x.berkas), x.dari, x.panjang) for x in p]
    # Berkas besar langsung menjadi rentang; paket kecil yang sedang dikumpulkan
    # baru dikirim saat penuh (ukuran atau jumlah berkas).
    assert ringkas == [
        ("rentang", ("besar",), 0, 8), ("rentang", ("besar",), 8, 8), ("rentang", ("besar",), 16, 4),
        ("paket", ("a", "b"), 0, 0),
        ("paket", ("c", "d"), 0, 0),
    ]
    assert [x.ukuran for x in p] == [8, 8, 4, 7, 5]


def test_rencana_hanya_kode():
    staging = {x.path: x for x in [
        e("wp-content/themes/t/style.css", h=H2), e("wp-content/plugins/p/baru.php"),
        e("wp-content/uploads/2026/baru.jpg"), e("wp-content/uploads/2026/ubah.jpg", h=H2),
        e("wp-content/plugins/wp-manager-connector/x.php", h=H2), e("wp-content/mu-plugins/wpmgr-staging.php"),
        e("wp-includes/version.php", h=H2), e("wp-config.php", h=H2),
    ]}
    produksi = {x.path: x for x in [
        e("wp-content/themes/t/style.css"), e("wp-content/plugins/p/lama.php"),
        e("wp-content/uploads/2026/ubah.jpg"), e("wp-content/plugins/wp-manager-connector/x.php"),
        e("wp-includes/version.php"), e("wp-content/uploads/pesanan.pdf"),
    ]}
    r = rencana_dorong("hanya_kode", staging, produksi)
    assert [x.path for x in r.ganti] == [
        "wp-content/plugins/p/baru.php", "wp-content/themes/t/style.css", "wp-content/uploads/2026/baru.jpg"]
    assert r.hapus == ["wp-content/plugins/p/lama.php"]
    assert r.db is False


def test_rencana_timpa_penuh_tidak_menghapus_uploads_produksi():
    staging = {x.path: x for x in [e("index.php", h=H2), e("wp-content/themes/t/a.css"), e("lain.php")]}
    produksi = {x.path: x for x in [e("index.php"), e("wp-content/themes/t/b.css"),
                                    e("wp-content/uploads/pesanan.pdf"), e("google123.html")]}
    r = rencana_dorong("timpa_penuh", staging, produksi)
    assert [x.path for x in r.ganti] == ["index.php", "wp-content/themes/t/a.css"]
    assert r.hapus == ["wp-content/themes/t/b.css"]
    assert r.db is True


# ---- tanda air -------------------------------------------------------------
# Bentuk sesuai connector (WPMGR_Staging_TandaAir::kumpulkan): pesanan HPOS dan
# pesanan di tabel posts dilaporkan terpisah sebagai pesanan_hpos/pesanan_posts.


def _ta(**sumber):
    return urai_tanda_air({"sumber": sumber, "diambil": 1})


POSTS = {"maks_id": 100, "jumlah": 50, "diubah": "2026-09-20 00:00:00", "diubah_sejak": None}


def test_tanda_air_diurai_dan_dibandingkan():
    lama = _ta(posts=POSTS, comments={"maks_id": 10, "jumlah": 8}, users={"maks_id": 3, "jumlah": 3},
               pesanan_hpos={"maks_id": 500, "jumlah": 400}, pesanan_posts={"maks_id": 0, "jumlah": 0})
    baru = _ta(posts={"maks_id": 100, "jumlah": 50, "diubah": "2026-09-25 00:00:00", "diubah_sejak": 2},
               comments={"maks_id": 22, "jumlah": 20}, users={"maks_id": 4, "jumlah": 4},
               pesanan_hpos={"maks_id": 503, "jumlah": 403}, pesanan_posts={"maks_id": 0, "jumlah": 0},
               gravity_forms={"maks_id": 5, "jumlah": 5}, sumber_aneh={"maks_id": 1, "jumlah": 1})
    assert "sumber_aneh" not in baru["sumber"]
    assert bandingkan_tanda_air(lama, baru) == [
        "3 pesanan (HPOS) baru", "12 komentar baru", "1 user baru",
        "sumber baru: isian Gravity Forms (5 entri)", "2 post diubah",
    ]
    assert bandingkan_tanda_air(lama, lama) == []


def test_pesanan_di_tabel_posts_terlihat_walau_hpos_beku():
    lama = _ta(posts=POSTS, pesanan_hpos={"maks_id": 500, "jumlah": 400}, pesanan_posts={"maks_id": 900, "jumlah": 10})
    baru = _ta(posts=POSTS, pesanan_hpos={"maks_id": 500, "jumlah": 400}, pesanan_posts={"maks_id": 902, "jumlah": 12})
    assert bandingkan_tanda_air(lama, baru) == ["2 pesanan (tabel posts) baru"]


def test_perubahan_dua_arah_dan_sumber_hilang_terdeteksi():
    lama = _ta(posts=POSTS, comments={"maks_id": 10, "jumlah": 8}, users={"maks_id": 3, "jumlah": 3},
               flamingo={"maks_id": 7, "jumlah": 2})
    # Komentar dihapus (jumlah turun, maks_id turun), user dihapus lalu satu
    # dibuat (maks_id naik, jumlah tetap), flamingo kosong sehingga hilang.
    baru = _ta(posts=POSTS, comments={"maks_id": 9, "jumlah": 6}, users={"maks_id": 4, "jumlah": 3})
    assert bandingkan_tanda_air(lama, baru) == [
        "komentar berubah (8 menjadi 6 entri)",
        "1 user baru",
        "pesan Contact Form 7 (Flamingo) tidak ada lagi di produksi (sebelumnya 2 entri)",
    ]


def test_sumber_baru_kosong_tetap_dilaporkan():
    lama = _ta(posts=POSTS)
    baru = _ta(posts=POSTS, wpforms={"maks_id": 0, "jumlah": 0})
    assert bandingkan_tanda_air(lama, baru) == ["sumber baru: isian WPForms (0 entri)"]


def test_diubah_sejak_null_berarti_tidak_diketahui_bukan_nol():
    lama = _ta(posts=POSTS)
    # Waktu GMT tanpa zona dibandingkan sebagai waktu UTC, bukan teks.
    lebih_baru = _ta(posts={**POSTS, "diubah": "2026-09-20 00:00:01"})
    assert bandingkan_tanda_air(lama, lebih_baru) == ["ada post yang diubah; jumlahnya tidak bisa dipastikan"]
    rusak = _ta(posts={**POSTS, "diubah": "kemarin"})
    assert bandingkan_tanda_air(lama, rusak) == ["perubahan post tidak bisa dipastikan"]
    nol = _ta(posts={**POSTS, "diubah_sejak": 0})
    assert bandingkan_tanda_air(lama, nol) == []


def test_tanda_air_rusak_dan_tidak_ada():
    assert urai_tanda_air({"sumber": {"posts": {"maks_id": "x", "jumlah": 1}}}) == {"sumber": {}}
    assert urai_tanda_air("bukan") is None
    assert bandingkan_tanda_air(None, {"sumber": {}})[0].startswith("Tanda air saat tarik tidak tersedia")


# ---- sumber daya -----------------------------------------------------------


def test_cek_sumber_daya_dengan_angka_jelas():
    st = StatusPembantu(mem_tersedia=int(1.5 * GB), disk_total=100 * GB, disk_bebas=20 * GB, container={}, akses={})
    assert cek_ram(st) == "RAM tersedia di VPS 1,5 GB; minimal 2,0 GB untuk menjalankan staging."
    assert cek_ram(st, "situs") == "RAM tersedia di VPS 1,5 GB; minimal 2,0 GB untuk menjalankan situs."
    assert cek_disk(st, 6 * GB) == "Sisa disk sesudah tarik akan 14,0 GB (14% dari 100,0 GB); minimal 15%."
    assert cek_disk(st, 4 * GB) is None
    assert cek_ram(StatusPembantu(3 * GB, 1, 1, {}, {})) is None
    assert cek_maks_aktif(3, 3) == "Sudah ada 3 staging aktif (batas 3). Jeda salah satu dulu."
    assert cek_maks_aktif(2, 3) is None


def test_format_byte():
    assert format_byte(0) == "0 B"
    assert format_byte(1536) == "1,5 KB"
    assert format_byte(int(2.25 * GB)) == "2,2 GB"


# ---- halaman manifest ------------------------------------------------------


def _item(path, ukuran=1):
    return {"path": path, "ukuran": ukuran, "mtime": 5, "hash": H1}


def test_halaman_manifest_pertama_membawa_info():
    h = halaman_manifest({"berkas": [_item("a.php"), _item("../x"), "rusak"], "jumlah_dilewati": 4,
                          "dilewati": [], "kursor": "a.php", "lagi": True,
                          "info": {"multisite": False, "konten_di_luar": False, "unggah_terlalu_kecil": True}},
                         None)
    assert [x.path for x in h.entri] == ["a.php"]
    assert h.dilewati == 6
    assert (h.kursor, h.lagi) == ("a.php", True)
    assert h.info["unggah_terlalu_kecil"] is True


def test_halaman_manifest_kosong_dengan_lagi_diterima_dan_info_lanjutan_diabaikan():
    # Connector berhenti di tenggat setelah hanya melewati entri anomali:
    # halaman kosong, tetapi kursor maju.
    h = halaman_manifest({"berkas": [], "kursor": "wp-content/z", "lagi": True, "info": {"multisite": True}},
                         "wp-content/a")
    assert h.entri == [] and h.lagi is True and h.kursor == "wp-content/z"
    assert h.info is None
    akhir = halaman_manifest({"berkas": [], "kursor": None, "lagi": False}, "wp-content/z")
    assert akhir.lagi is False and akhir.kursor is None


@pytest.mark.parametrize("data", [
    "bukan", {"berkas": "x", "lagi": False}, {"berkas": [], "lagi": "ya"},
    {"berkas": [], "lagi": True, "kursor": "../x"}, {"berkas": [], "lagi": True, "kursor": None},
    {"berkas": [], "lagi": True, "kursor": "sama"},
])
def test_halaman_manifest_tidak_sah_atau_tidak_maju_ditolak(data):
    with pytest.raises(BalasanTidakSah):
        halaman_manifest(data, "sama")


# ---- paket berkas ----------------------------------------------------------


def test_hasil_paket_dengan_penanda_dan_sisa():
    diminta = ["a", "b", "c", "d", "e"]
    meta = {"lengkap": False, "berkas": [
        {"path": "a", "mtime": 7}, {"path": "b", "hilang": True}, {"path": "c", "galat": "baca"},
        {"path": "d", "mtime": 9},
    ]}
    h = hasil_paket(diminta, meta, [b"isi-a", b"", b"", b"isi-d"])
    assert h.isi == [("a", b"isi-a", 7), ("d", b"isi-d", 9)]
    assert h.hilang == ["b"] and h.gagal_baca == ["c"] and h.terlalu_besar == []
    assert h.sisa == ["e"]


def test_hasil_paket_terlalu_besar_pindah_ke_rentang():
    meta = {"lengkap": False, "berkas": [{"path": "a", "terlalu_besar": True, "total": 99, "mtime": 1}]}
    h = hasil_paket(["a", "b"], meta, [b""])
    assert h.terlalu_besar == ["a"] and h.sisa == ["b"] and h.isi == []


@pytest.mark.parametrize("meta,bagian", [
    ({"lengkap": True, "berkas": [{"path": "a", "mtime": 1}]}, [b"x"]),            # lengkap tapi kurang
    ({"lengkap": False, "berkas": []}, []),                                            # tidak maju
    ({"lengkap": True, "berkas": [{"path": "b", "mtime": 1}, {"path": "a", "mtime": 1}]}, [b"", b""]),
    ({"berkas": [{"path": "a", "mtime": 1}, {"path": "b", "mtime": 1}]}, [b"", b""]),  # lengkap hilang
    ({"lengkap": True, "berkas": [{"path": "a", "mtime": "1"}, {"path": "b", "mtime": 1}]}, [b"", b""]),
    ({"lengkap": True, "berkas": [{"path": "a", "hilang": True}, {"path": "b", "mtime": 1}]}, [b"x", b""]),
])
def test_hasil_paket_tidak_sesuai_permintaan_ditolak(meta, bagian):
    with pytest.raises(BalasanTidakSah):
        hasil_paket(["a", "b"], meta, bagian)


# ---- rentang ---------------------------------------------------------------


def test_rakit_rentang_maju_sebesar_bagian_yang_diterima_dan_verifikasi_hash():
    isi = b"0123456789"
    r = RakitRentang(Entri("besar.bin", 10, 50, hashlib.sha256(isi).hexdigest()), panjang=4)
    assert r.permintaan() == ("besar.bin", 0, 4)
    # Connector boleh mengembalikan kurang dari yang diminta.
    assert r.terima({"path": "besar.bin", "dari": 0, "total": 10, "mtime": 50}, isi[:3]) == "lanjut"
    assert r.permintaan() == ("besar.bin", 3, 4)
    assert r.terima({"path": "besar.bin", "dari": 3, "total": 10, "mtime": 50}, isi[3:7]) == "lanjut"
    assert r.terima({"path": "besar.bin", "dari": 7, "total": 10, "mtime": 50}, isi[7:]) == "selesai"
    assert r.hash() == hashlib.sha256(isi).hexdigest()
    assert r.verifikasi() is True


def test_rakit_rentang_hash_beda_padahal_ukuran_mtime_sama():
    r = RakitRentang(Entri("besar.bin", 4, 50, H1), panjang=8)
    assert r.terima({"path": "besar.bin", "dari": 0, "total": 4, "mtime": 50}, b"abcd") == "selesai"
    assert r.verifikasi() is False


def test_rakit_rentang_tidak_bisa_diverifikasi_bila_berubah_sejak_manifest_atau_tanpa_hash():
    r = RakitRentang(Entri("besar.bin", 4, 50, H1), panjang=8)
    r.terima({"path": "besar.bin", "dari": 0, "total": 5, "mtime": 60}, b"abcde")
    assert r.verifikasi() is None
    r = RakitRentang(Entri("besar.bin", 4, 50, None), panjang=8)
    r.terima({"path": "besar.bin", "dari": 0, "total": 4, "mtime": 50}, b"abcd")
    assert r.verifikasi() is None


def test_rakit_rentang_berubah_hilang_dan_menyusut():
    r = RakitRentang(Entri("x", 10, 5, None), panjang=4)
    r.terima({"path": "x", "dari": 0, "total": 10, "mtime": 5}, b"abcd")
    assert r.terima({"path": "x", "dari": 4, "total": 10, "mtime": 6}, b"efgh") == "berubah"
    r = RakitRentang(Entri("x", 10, 5, None), panjang=4)
    r.terima({"path": "x", "dari": 0, "total": 10, "mtime": 5}, b"abcd")
    assert r.terima({"path": "x", "dari": 4, "total": 10, "mtime": 5}, b"") == "berubah"
    r = RakitRentang(Entri("x", 10, 5, None), panjang=4)
    assert r.terima({"path": "x", "hilang": True, "total": 0}, b"") == "hilang"


@pytest.mark.parametrize("meta,isi", [
    ({"path": "y", "dari": 0, "total": 10, "mtime": 5}, b"ab"),
    ({"path": "x", "dari": 2, "total": 10, "mtime": 5}, b"ab"),
    ({"path": "x", "dari": 0, "total": "10", "mtime": 5}, b"ab"),
    ({"path": "x", "dari": 0, "total": 3, "mtime": 5}, b"abcd"),     # lebih dari sisa
    ({"path": "x", "dari": 0, "total": 10, "mtime": 5}, b"abcde"),   # lebih dari yang diminta
])
def test_rakit_rentang_balasan_tidak_sah(meta, isi):
    r = RakitRentang(Entri("x", 10, 5, None), panjang=4)
    with pytest.raises(BalasanTidakSah):
        r.terima(meta, isi)


# ---- potongan tabel --------------------------------------------------------


def test_potongan_tabel_dengan_mode():
    p = potongan_tabel("wp_posts", None, {"tabel": "wp_posts", "kursor": "eyJhIjoxfQ==", "selesai": False,
                                          "baris": 2000, "mode": "pk"}, [b"INSERT ..."])
    assert (p.selesai, p.kursor, p.baris, p.mode, p.sql) == (False, "eyJhIjoxfQ==", 2000, "pk", b"INSERT ...")
    akhir = potongan_tabel("wp_x", "eyJhIjoxfQ==", {"tabel": "wp_x", "kursor": None, "selesai": True,
                                                    "baris": 3, "mode": "offset"}, [b""])
    assert akhir.selesai is True and akhir.kursor is None and akhir.mode == "offset"


@pytest.mark.parametrize("meta", [
    {"tabel": "wp_lain", "kursor": None, "selesai": True, "baris": 0, "mode": "pk"},
    {"tabel": "wp_x", "kursor": "K", "selesai": False, "baris": 0, "mode": "pk"},          # kursor tidak maju
    {"tabel": "wp_x", "kursor": "a b", "selesai": False, "baris": 0, "mode": "pk"},
    {"tabel": "wp_x", "kursor": None, "selesai": 1, "baris": 0, "mode": "pk"},
    {"tabel": "wp_x", "kursor": None, "selesai": True, "baris": 0, "mode": "acak"},
    {"tabel": "wp_x", "kursor": None, "selesai": True, "baris": -1, "mode": "pk"},
])
def test_potongan_tabel_tidak_sah(meta):
    with pytest.raises(BalasanTidakSah):
        potongan_tabel("wp_x", "K", meta, [b""])


# ---- fix putaran 1 -----------------------------------------------------------


def test_entri_dari_menjepit_mtime_negatif_ke_nol():
    assert entri_dari({"path": "a.php", "ukuran": 1, "mtime": -5, "hash": None}) == Entri("a.php", 1, 0, None)
    assert entri_dari({"path": "a.php", "ukuran": 1, "mtime": True, "hash": None}) is None


def test_diubah_tanda_air_hanya_format_waktu_sah():
    ta = urai_tanda_air({"sumber": {"posts": {**POSTS, "diubah": "2026-09-20 00:00:00<script>"}}})
    assert ta["sumber"]["posts"]["diubah"] == ""
    ta = urai_tanda_air({"sumber": {"posts": {**POSTS, "diubah": "2026-09-20T00:00:00"}}})
    assert ta["sumber"]["posts"]["diubah"] == ""
    ta = urai_tanda_air({"sumber": {"posts": {**POSTS, "diubah": "2026-09-20 00:00:00"}}})
    assert ta["sumber"]["posts"]["diubah"] == "2026-09-20 00:00:00"


def test_bandingkan_tanda_air_baru_tidak_ada():
    lama = _ta(posts=POSTS)
    for baru in (None, {}, {"sumber": "x"}):
        hasil = bandingkan_tanda_air(lama, baru)
        assert len(hasil) == 1 and "tidak bisa dipastikan" in hasil[0]


def test_info_manifest_hanya_bendera_yang_dikenal():
    h = halaman_manifest({"berkas": [], "lagi": False, "kursor": None, "info": {
        "multisite": 1, "konten_di_luar": "", "unggah_terlalu_kecil": True, "batas_unggah": 99 * 1024 * 1024,
        "home": "<b>x</b>", "lain": [1]}}, None)
    assert h.info == {"multisite": True, "konten_di_luar": False, "unggah_terlalu_kecil": True,
                      "batas_unggah": 4 * 1024 * 1024}
    h = halaman_manifest({"berkas": [], "lagi": False, "kursor": None, "info": {"batas_unggah": "x"}}, None)
    assert h.info == {"multisite": False, "konten_di_luar": False, "unggah_terlalu_kecil": False,
                      "batas_unggah": None}


def test_halaman_manifest_menegakkan_batas_halaman():
    from wpmgr.staging.rencana import MAKS_HALAMAN_MANIFEST

    data = {"berkas": [], "lagi": True, "kursor": "b"}
    assert halaman_manifest(data, "a", halaman=MAKS_HALAMAN_MANIFEST - 1).lagi is True
    with pytest.raises(BalasanTidakSah):
        halaman_manifest(data, "a", halaman=MAKS_HALAMAN_MANIFEST)
    # Halaman terakhir (lagi:false) di batas tetap diterima.
    assert halaman_manifest({"berkas": [], "lagi": False}, "a", halaman=MAKS_HALAMAN_MANIFEST).lagi is False


def test_rakit_rentang_total_jauh_melebihi_manifest_berarti_berubah():
    mib = 1024 * 1024
    r = RakitRentang(Entri("x", 100 * mib, 5, None), panjang=4)
    # Pertumbuhan kecil sejak manifest masih diterima...
    assert r.terima({"path": "x", "dari": 0, "total": 100 * mib + 5, "mtime": 6}, b"abcd") == "lanjut"
    # ...tetapi total yang jauh melebihi manifest tidak diikuti (bisa jadi
    # connector yang disusupi mencoba memenuhi disk).
    r = RakitRentang(Entri("x", 100 * mib, 5, None), panjang=4)
    assert r.terima({"path": "x", "dari": 0, "total": 200 * mib, "mtime": 6}, b"abcd") == "berubah"
    assert r.dari == 0


def test_selisih_dengan_daftar_dilindungi_milik_tujuan():
    dilindungi = frozenset({"wp-config.php", "wp-content/mu-plugins/wpmgr-pratinjau.php"})
    produksi = {"wp-content/mu-plugins/wpmgr-pratinjau.php": e("wp-content/mu-plugins/wpmgr-pratinjau.php", h=H2),
                "wp-content/mu-plugins/wpmgr-staging.php": e("wp-content/mu-plugins/wpmgr-staging.php")}
    lokal = {"wp-content/mu-plugins/wpmgr-pratinjau.php": e("wp-content/mu-plugins/wpmgr-pratinjau.php")}
    s = selisih(produksi, lokal, dilindungi)
    # Berkas milik tujuan tidak ditimpa; berkas staging bukan milik tujuan ini, jadi ikut disalin.
    assert [x.path for x in s.diambil] == ["wp-content/mu-plugins/wpmgr-staging.php"]
    assert s.hapus == []
