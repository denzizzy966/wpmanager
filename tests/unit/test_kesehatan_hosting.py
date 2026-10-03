from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from wpmgr.kesehatan import TAB_MASALAH, TINGKAT_MASALAH, URUTAN_CHIP, masalah_hosting
from wpmgr.models import StatusHosting

S = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def _h(**k):
    dasar = {"status": StatusHosting.pratinjau, "gagal_asal": None, "dilayani_vps_pada": None, "aktif_pada": None,
             "backup_terakhir_pada": None, "backup_gagal_pada": None}
    dasar.update(k)
    return SimpleNamespace(**dasar)


def test_chip_terdaftar():
    for chip, tingkat in (("hosting_gagal", 1), ("pindah_gagal", 2), ("backup_gagal", 2)):
        assert chip in URUTAN_CHIP and TINGKAT_MASALAH[chip] == tingkat and TAB_MASALAH[chip] == "hosting"
    assert URUTAN_CHIP.index("hosting_gagal") < URUTAN_CHIP.index("diserang")


@pytest.mark.parametrize("h,masalah", [
    (None, []),
    (_h(), []),
    (_h(status=StatusHosting.gagal, gagal_asal="salinan"), ["pindah_gagal"]),
    (_h(status=StatusHosting.gagal, gagal_asal=None), ["pindah_gagal"]),
    (_h(status=StatusHosting.gagal, gagal_asal="produksi", dilayani_vps_pada=S - timedelta(hours=1),
        aktif_pada=None), ["hosting_gagal"]),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S - timedelta(days=3), aktif_pada=S - timedelta(days=3),
        backup_terakhir_pada=S - timedelta(hours=30)), []),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S - timedelta(days=3), aktif_pada=S - timedelta(days=3),
        backup_terakhir_pada=S - timedelta(hours=37)), ["backup_gagal"]),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S - timedelta(days=3), aktif_pada=S - timedelta(hours=2)),
     []),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S - timedelta(days=3), aktif_pada=S - timedelta(hours=40)),
     ["backup_gagal"]),
    (_h(status=StatusHosting.aktif, dilayani_vps_pada=S, aktif_pada=S, backup_terakhir_pada=S,
        backup_gagal_pada=S), ["backup_gagal"]),
    (_h(status=StatusHosting.pratinjau, backup_gagal_pada=S), []),
])
def test_masalah_hosting(h, masalah):
    assert masalah_hosting(h, S) == masalah
