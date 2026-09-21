from datetime import date, datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from wpmgr.models import (
    CatatanError,
    JobType,
    KejadianLogin,
    LoginGagal,
    TrafficHarian,
    TrafficRincian,
    UptimeCheck,
    UptimeHasil,
    UptimeInsiden,
    UptimePutaran,
    UptimeStatus,
)

pytestmark = pytest.mark.integration

SEKARANG = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)


def test_site_baru_punya_default_lapis2(sesi, site):
    sesi.refresh(site)
    assert site.fitur == []
    assert site.uptime_status == UptimeStatus.belum_dicek
    assert site.uptime_gagal_beruntun == 0


def test_job_type_lapis2_ada():
    assert {"collect_events", "collect_traffic", "update_connector"} <= {j.value for j in JobType}


def test_uptime_check_dan_putaran(sesi, site):
    p = UptimePutaran(jumlah_site=1, jumlah_gagal=0, gangguan_dashboard=False)
    sesi.add(p)
    sesi.flush()
    sesi.add(UptimeCheck(putaran_id=p.id, site_id=site.id, hasil=UptimeHasil.naik,
                         http_status=200, waktu_ms=120))
    sesi.commit()
    assert sesi.query(UptimeCheck).count() == 1


def test_hanya_satu_insiden_terbuka_per_site(sesi, site):
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG, penyebab="HTTP 500"))
    sesi.commit()
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG, penyebab="HTTP 502"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()


def test_insiden_selesai_tidak_menghalangi_yang_baru(sesi, site):
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG, selesai=SEKARANG,
                           penyebab="HTTP 500"))
    sesi.add(UptimeInsiden(site_id=site.id, mulai=SEKARANG, penyebab="HTTP 502"))
    sesi.commit()


def test_sidik_jari_error_unik_per_site(sesi, site):
    def baris():
        return CatatanError(
            site_id=site.id, sidik_jari="a" * 32, tingkat="fatal", komponen_tipe="plugin",
            komponen_slug="elementor", pesan="x", jumlah=1,
            pertama_terlihat=SEKARANG, terakhir_terlihat=SEKARANG,
        )

    sesi.add(baris())
    sesi.commit()
    sesi.add(baris())
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()


def test_login_gagal_ip_kosong_ikut_constraint_unik(sesi, site):
    # Baris "(IP lain)" memakai '' dan harus bertabrakan dengan dirinya
    # sendiri -- itulah alasan kolom ini text, bukan inet yang boleh NULL.
    for _ in range(2):
        sesi.add(LoginGagal(site_id=site.id, jam=SEKARANG, ip="", username="(lainnya)",
                            jalur="form", jumlah=1))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()


def test_kejadian_login_unik_per_id_di_site(sesi, site):
    for _ in range(2):
        sesi.add(KejadianLogin(site_id=site.id, id_di_site=7, waktu=SEKARANG,
                               jenis="berhasil", username="admin"))
    with pytest.raises(IntegrityError):
        sesi.commit()
    sesi.rollback()


def test_traffic_harian_dan_rincian(sesi, site):
    sesi.add(TrafficHarian(site_id=site.id, tanggal=date(2026, 9, 21), sumber="plugin",
                           kunjungan=10, pengunjung=7))
    sesi.add(TrafficRincian(site_id=site.id, tanggal=date(2026, 9, 21), sumber="plugin",
                            dimensi="halaman", kunci="/", kunjungan=4))
    sesi.commit()


def test_hapus_site_ikut_menghapus_data_monitoring(sesi, site):
    sesi.add(CatatanError(site_id=site.id, sidik_jari="b" * 32, tingkat="warning",
                          komponen_tipe="core", pesan="x", jumlah=1,
                          pertama_terlihat=SEKARANG, terakhir_terlihat=SEKARANG))
    sesi.commit()
    sesi.delete(site)
    sesi.commit()
    assert sesi.query(CatatanError).count() == 0
