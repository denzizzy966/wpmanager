from datetime import datetime, timedelta, timezone

import pytest

from wpmgr.keamanan import StatusKeamanan, nilai_keamanan
from wpmgr.models import KejadianLogin, LoginGagal

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, 12, 30, tzinfo=timezone.utc)
JAM_INI = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
_id = iter(range(1, 10_000))


def login(sesi, site, waktu, jenis="berhasil", username="admin", ip="203.0.113.9",
          negara=None, jalur="form"):
    sesi.add(KejadianLogin(site_id=site.id, id_di_site=next(_id), waktu=waktu, jenis=jenis,
                           username=username, ip=ip, negara=negara, jalur=jalur))
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
