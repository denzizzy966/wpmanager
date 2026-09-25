from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from wpmgr.keamanan import StatusKeamanan, nilai_keamanan
from wpmgr.models import KejadianLogin, LoginGagal

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc)
JAM_INI = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
_id = iter(range(1, 10_000))


def login(sesi, site, waktu, jenis="berhasil", username="admin", ip="203.0.113.9",
          negara=None, jalur="form", dicatat_pada=None):
    # dicatat_pada default = waktu (kejadian dianggap terkumpul seketika)
    # supaya test tidak bergantung pada jam dinding sungguhan lewat
    # server_default now() -- hanya test yang sengaja menguji jeda
    # pengumpulan (koreksi #11) yang mengoper dicatat_pada berbeda.
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=next(_id), waktu=waktu, jenis=jenis,
                           username=username, ip=ip, negara=negara, jalur=jalur,
                           dicatat_pada=dicatat_pada if dicatat_pada is not None else waktu))
    sesi.commit()


def gagal(sesi, site, jam, jumlah, ip="198.51.100.7", username="admin"):
    sesi.add(LoginGagal(site_id=site.id, jam=jam, ip=ip, username=username, jalur="form",
                        jumlah=jumlah))
    sesi.commit()


def test_site_tanpa_kejadian_aman(sesi, site):
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_admin_baru_perlu_diperiksa_sampai_ditandai(sesi, site):
    login(sesi, site, SEKARANG - timedelta(hours=3), jenis="admin_baru", username="backdoor")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "backdoor" in hasil.alasan[0]

    site.keamanan_diperiksa_pada = SEKARANG - timedelta(hours=1)
    sesi.commit()
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_brute_force_yang_tembus(sesi, site):
    gagal(sesi, site, JAM_INI - timedelta(hours=2), 6, ip="203.0.113.9")
    login(sesi, site, SEKARANG - timedelta(minutes=10), ip="203.0.113.9")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "203.0.113.9" in hasil.alasan[0]


def test_gagal_di_bawah_ambang_tidak_dianggap_tembus(sesi, site):
    gagal(sesi, site, JAM_INI - timedelta(hours=2), 4, ip="203.0.113.9")
    login(sesi, site, SEKARANG - timedelta(minutes=10), ip="203.0.113.9")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_login_sso_tidak_pernah_mencurigakan(sesi, site):
    gagal(sesi, site, JAM_INI - timedelta(hours=2), 30, ip="203.0.113.9")
    login(sesi, site, SEKARANG - timedelta(minutes=10), ip="203.0.113.9", jalur="sso")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_login_dari_negara_baru(sesi, site):
    login(sesi, site, SEKARANG - timedelta(days=10), negara="ID")
    login(sesi, site, SEKARANG - timedelta(minutes=5), negara="RU", ip="192.0.2.5")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "RU" in hasil.alasan[0]


def test_login_pertama_kali_tidak_dinilai_negaranya(sesi, site):
    login(sesi, site, SEKARANG - timedelta(minutes=5), negara="RU")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_negara_sama_aman(sesi, site):
    login(sesi, site, SEKARANG - timedelta(days=10), negara="ID")
    login(sesi, site, SEKARANG - timedelta(minutes=5), negara="ID")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_riwayat_tanpa_negara_tidak_membuat_negara_baru(sesi, site):
    login(sesi, site, SEKARANG - timedelta(days=10), negara=None)
    login(sesi, site, SEKARANG - timedelta(minutes=5), negara="ID")
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_diserang_karena_total(sesi, site):
    for i in range(5):
        gagal(sesi, site, JAM_INI, 10, ip=f"198.51.100.{i}")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.diserang
    assert hasil.percobaan_sejam == 50


def test_diserang_karena_satu_ip(sesi, site):
    gagal(sesi, site, JAM_INI, 20)
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.diserang
    assert hasil.ip_teratas[0] == ("198.51.100.7", 20)


def test_serangan_lama_tidak_dihitung(sesi, site):
    gagal(sesi, site, JAM_INI - timedelta(hours=2), 500)
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_baris_ip_lain_tidak_dihitung_sebagai_satu_ip(sesi, site):
    gagal(sesi, site, JAM_INI, 30, ip="", username="(lainnya)")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.aman
    assert hasil.ip_teratas[0] == ("(IP lain)", 30)


# --- Fix round 1 (koreksi #11): ember jam dan waktu masuk data -------------


def test_serangan_pinggir_jam_masih_dihitung(sesi, site):
    """LoginGagal.jam adalah AWAL jam: ember 11:00 mewakili gagal sepanjang
    11:00-11:59, jadi masih relevan diperiksa pada pukul 12:05, bukan cuma
    tepat pukul 12:00 atau lebih lambat."""
    sekarang = datetime(2026, 9, 22, 12, 5, tzinfo=timezone.utc)
    gagal(sesi, site, datetime(2026, 9, 22, 11, 0, tzinfo=timezone.utc), 60)
    hasil = nilai_keamanan(sesi, site, sekarang)
    assert hasil.status == StatusKeamanan.diserang
    assert hasil.percobaan_sejam == 60


def test_serangan_satu_ip_pinggir_jam(sesi, site):
    sekarang = datetime(2026, 9, 22, 12, 5, tzinfo=timezone.utc)
    gagal(sesi, site, datetime(2026, 9, 22, 11, 0, tzinfo=timezone.utc), 25)
    hasil = nilai_keamanan(sesi, site, sekarang)
    assert hasil.status == StatusKeamanan.diserang
    assert hasil.ip_teratas[0] == ("198.51.100.7", 25)


def test_gagal_25_jam_sebelum_sukses_masih_dihitung(sesi, site):
    """Ember 24-25 jam sebelum sukses tetap masuk jendela `waktu - 25 jam`
    (koreksi #11), walau sudah di luar batas lama `jam >= waktu - 24 jam`."""
    waktu_sukses = SEKARANG - timedelta(minutes=10)
    gagal(sesi, site, waktu_sukses - timedelta(hours=24, minutes=20), 6, ip="203.0.113.9")
    login(sesi, site, waktu_sukses, ip="203.0.113.9")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "203.0.113.9" in hasil.alasan[0]


def test_admin_baru_dicatat_belakangan_tetap_terdeteksi(sesi, site):
    """waktu di site datang sebelum klik "Sudah diperiksa", tetapi baris baru
    tersimpan (dicatat_pada) sesudahnya -- collect_events selalu telat."""
    site.keamanan_diperiksa_pada = SEKARANG - timedelta(hours=2)
    sesi.commit()
    login(sesi, site, SEKARANG - timedelta(hours=3), jenis="admin_baru", username="backdoor",
          dicatat_pada=SEKARANG - timedelta(hours=1))
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "backdoor" in hasil.alasan[0]


def test_admin_baru_sudah_terlihat_saat_diperiksa_dianggap_selesai(sesi, site):
    """waktu di site datang SESUDAH klik "Sudah diperiksa", tetapi baris itu
    sudah tersimpan (dicatat_pada) SEBELUM klik -- admin sudah melihatnya
    saat memeriksa, jadi tidak boleh terbuka lagi."""
    login(sesi, site, SEKARANG + timedelta(hours=1), jenis="admin_baru", username="backdoor",
          dicatat_pada=SEKARANG - timedelta(hours=2))
    site.keamanan_diperiksa_pada = SEKARANG - timedelta(hours=1)
    sesi.commit()
    assert nilai_keamanan(sesi, site, SEKARANG).status == StatusKeamanan.aman


def test_jumlah_query_konstan_walau_banyak_login(sesi, site):
    """N+1 lama menjalankan 3 query Python per login sukses; sesudah
    perbaikan jumlah query per panggilan nilai_keamanan() tidak boleh
    tumbuh mengikuti jumlah login."""
    def _hitung_query():
        n = [0]

        def _tambah(conn, cursor, statement, parameters, context, executemany):
            n[0] += 1

        bind = sesi.get_bind()
        event.listen(bind, "before_cursor_execute", _tambah)
        try:
            nilai_keamanan(sesi, site, SEKARANG)
        finally:
            event.remove(bind, "before_cursor_execute", _tambah)
        return n[0]

    login(sesi, site, SEKARANG - timedelta(minutes=5), ip="203.0.113.50")
    dengan_satu = _hitung_query()

    for i in range(19):
        login(sesi, site, SEKARANG - timedelta(minutes=i + 10), ip=f"203.0.113.{i}")
    dengan_dua_puluh = _hitung_query()

    assert dengan_dua_puluh == dengan_satu


def test_jadi_admin_perlu_diperiksa(sesi, site):
    login(sesi, site, SEKARANG - timedelta(hours=1), jenis="jadi_admin", username="editor")
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "editor" in hasil.alasan[0]
    assert "administrator" in hasil.alasan[0].lower()


def test_login_sukses_tanpa_jalur_tetap_dievaluasi(sesi, site):
    gagal(sesi, site, JAM_INI - timedelta(hours=2), 6, ip="203.0.113.9")
    login(sesi, site, SEKARANG - timedelta(minutes=10), ip="203.0.113.9", jalur=None)
    hasil = nilai_keamanan(sesi, site, SEKARANG)
    assert hasil.status == StatusKeamanan.perlu_diperiksa
    assert "203.0.113.9" in hasil.alasan[0]
