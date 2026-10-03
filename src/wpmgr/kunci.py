"""Advisory lock PostgreSQL untuk perintah cron yang tidak boleh tumpang-tindih."""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

KUNCI_UPTIME = 72_140_001
KUNCI_SSL = 72_140_002
KUNCI_GA4 = 72_140_003
KUNCI_RETENSI = 72_140_004
KUNCI_GEOIP = 72_140_005
KUNCI_STAGING_JEDA = 72_140_006
KUNCI_STAGING_SERTIFIKAT = 72_140_007
KUNCI_STAGING_PANGKAS = 72_140_008
KUNCI_HOSTING_DNS = 72_140_009
KUNCI_HOSTING_SERTIFIKAT = 72_140_010
KUNCI_HOSTING_BACKUP = 72_140_011


@contextmanager
def kunci_advisory(engine, kunci: int) -> Iterator[bool]:
    """Pegang lock selama blok berjalan; menghasilkan False bila sudah dipegang.

    Lock level sesi melekat pada koneksi, jadi diambil dan dilepas pada satu
    koneksi khusus yang dipegang sampai blok selesai. Lewat Session biasa,
    unlock bisa terjadi di koneksi pool yang lain dan gagal diam-diam.
    """
    with engine.connect() as conn:
        dapat = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": kunci}).scalar())
        conn.commit()
        try:
            yield dapat
        finally:
            if dapat:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": kunci})
                conn.commit()
