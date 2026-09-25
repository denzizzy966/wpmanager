from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wpmgr.keamanan import StatusError, error_menyalakan_chip, status_error

SEKARANG = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
JAM = timedelta(hours=1)


def e(pertama, terakhir, selesai=None, tingkat="fatal"):
    return SimpleNamespace(pertama_terlihat=SEKARANG - pertama, terakhir_terlihat=SEKARANG - terakhir,
                           ditandai_selesai_pada=None if selesai is None else SEKARANG - selesai,
                           tingkat=tingkat)


@pytest.mark.parametrize(
    ("err", "harapan"),
    [
        (e(2 * JAM, JAM), StatusError.baru),
        (e(48 * JAM, JAM), StatusError.masih_terjadi),
        (e(72 * JAM, 30 * JAM), StatusError.berhenti),
        (e(72 * JAM, 30 * JAM, selesai=20 * JAM), StatusError.selesai),
        # Muncul lagi setelah ditandai selesai: terbuka kembali.
        (e(72 * JAM, JAM, selesai=20 * JAM), StatusError.masih_terjadi),
    ],
)
def test_status_error(err, harapan):
    assert status_error(err, SEKARANG) == harapan


def test_chip_hanya_untuk_fatal_dan_database_yang_aktif():
    assert error_menyalakan_chip(e(2 * JAM, JAM), SEKARANG)
    assert error_menyalakan_chip(e(2 * JAM, JAM, tingkat="database"), SEKARANG)
    assert not error_menyalakan_chip(e(2 * JAM, JAM, tingkat="warning"), SEKARANG)
    assert not error_menyalakan_chip(e(72 * JAM, 30 * JAM), SEKARANG)
