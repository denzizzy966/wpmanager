import pytest

from wpmgr.web.routes_pages import POLA_BULAN


@pytest.mark.parametrize(
    "bulan",
    [
        # Regex lama dipakai "$" (bukan "\Z"): tanpa re.MULTILINE, "$" tetap
        # cocok tepat sebelum SATU baris baru di akhir string, jadi
        # "2026-09\n" lolos sebagai "2026-09" yang sah. "\Z" menolaknya.
        "2026-09\n",
        # Regex lama dipakai "\d" (bukan "[0-9]") untuk tahun: "\d" Python
        # (tanpa re.ASCII) juga cocok dengan digit Unicode non-ASCII seperti
        # digit lebar-penuh di bawah ini ("２０２６" == "2026" menurut int()).
        # "[0-9]" hanya menerima ASCII 0-9. (Bagian bulan tetap ASCII di
        # kedua versi karena "0[1-9]"/"1[0-2]" adalah kelas karakter literal.)
        "２０２６-09",
    ],
)
def test_pola_bulan_menolak_jebakan_dolar_dan_d(bulan):
    assert POLA_BULAN.match(bulan) is None
