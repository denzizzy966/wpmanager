import time

import pytest

from wpmgr.staging.sql_impor import (
    ALASAN_KOMENTAR,
    ALASAN_PARTISI,
    ALASAN_TERBUKA,
    BATAS_CREATE,
    PemeriksaTerapkan,
    periksa_berkas_terapkan,
    periksa_sql,
    periksa_terapkan,
    sesuaikan_mariadb,
)

AWAL = b"DROP TABLE IF EXISTS `t`;\nCREATE TABLE `t` (`id` int);\n"
DELAPAN_MB = 8 * 1024 * 1024
# Lebih longgar dari "beberapa detik" pada mesin lambat, tetapi jauh di
# bawah waktu versi kuadratik (15,7 detik untuk 40 KB).
BATAS_DETIK = 3.0


def test_sesuaikan_hanya_create_table():
    sql = (b"DROP TABLE IF EXISTS `t`;\n"
           b"CREATE TABLE `t` (`id` int /*!80023 INVISIBLE */, `s` text COLLATE utf8mb4_0900_ai_ci "
           b"COMMENT 'utf8mb4_0900_ai_ci; /*!80016 x */', `b` blob COLLATE utf8mb4_0900_bin) "
           b"COLLATE=utf8mb4_0900_ai_ci /*!50100 TABLESPACE `x` */;\n"
           b"INSERT INTO `t` VALUES (1,'utf8mb4_0900_ai_ci /*!80016 y */',NULL);\n")
    hasil = sesuaikan_mariadb(sql)
    assert b"/*!80023" not in hasil
    assert b"COLLATE utf8mb4_unicode_520_ci COMMENT 'utf8mb4_0900_ai_ci; /*!80016 x */'" in hasil
    assert b"COLLATE utf8mb4_bin" in hasil and b"COLLATE=utf8mb4_unicode_520_ci" in hasil
    assert b"/*!50100 TABLESPACE `x` */" in hasil
    assert hasil.endswith(b"INSERT INTO `t` VALUES (1,'utf8mb4_0900_ai_ci /*!80016 y */',NULL);\n")


def test_sesuaikan_create_di_luar_batas_dibiarkan():
    sql = b"DROP TABLE IF EXISTS `t`;\n" + b"-- " + b"x" * BATAS_CREATE + b"\nCREATE TABLE `t` (a int) COLLATE=utf8mb4_0900_ai_ci;\n"
    assert sesuaikan_mariadb(sql) == sql


@pytest.mark.parametrize("sql", [
    AWAL,
    AWAL + b"INSERT INTO `t` (`id`) VALUES ('1'),('a\\'b;LOAD DATA'),(\"x\"),(-1),(5--1);\n",
    b"INSERT INTO `t` VALUES ('# bukan komentar', '-- juga bukan', '/* bukan */');\n",
    b"CREATE TABLE `t` (a int) /*!50100 PARTITION BY HASH (a) */;\n",
    b"-- komentar ' dengan kutip\nINSERT INTO `t` VALUES (1);\n",
    b"",
    b"\n",
])
def test_periksa_menerima_dump_connector(sql):
    assert periksa_sql(sql) is None


@pytest.mark.parametrize("sql", [
    b"LOAD DATA LOCAL INFILE '/etc/shadow' INTO TABLE t;\n",
    AWAL + b"load  data local infile '/var/lib/mysql/mysql/global_priv.MAD' into table `t`;\n",
    b"INSERT INTO `t` VALUES (1);\nLOAD XML LOCAL INFILE 'x' INTO TABLE t;\n",
    b"INSERT INTO `t` SELECT 1 INTO OUTFILE '/tmp/x';\n",
    b"INSERT INTO `t` SELECT 1 INTO DUMPFILE '/tmp/x';\n",
    b"INSERT INTO `t` VALUES (LOAD_FILE('/etc/passwd'));\n",
    b"INSERT INTO `t` VALUES (1) /*!50000 ; LOAD DATA LOCAL INFILE 'x' INTO TABLE t */;\n",
    b"INSERT INTO `t` VALUES (1);\n/*!50000 LOAD/**/DATA LOCAL INFILE 'x' INTO TABLE t */;\n",
    b"SET sql_mode='NO_BACKSLASH_ESCAPES';\n",
    b"DELIMITER //\n",
    b"INSERT INTO `t` VALUES (1);\n\\C gbk\n",
    b"INSERT INTO `t` VALUES ('tidak ditutup);\n",
    b"INSERT INTO `t` VALUES (1); /* tidak ditutup\n",
    b"INSERT INTO `t` VALUES (1)",
    b"INSERT INTO `t` VALUES (1);;\n",
])
def test_periksa_menolak(sql):
    assert periksa_sql(sql) is not None


@pytest.mark.parametrize("unit", [b"'\\", b"\"\\", b"`", b"/*", b"/*!50000 ", b"''", b";", b"INSERT INTO t;", b"--"])
def test_masukan_jahat_8mb_tetap_linear(unit):
    sql = unit * (DELAPAN_MB // len(unit))
    mulai = time.perf_counter()
    periksa_sql(sql)
    sesuaikan_mariadb(AWAL[:26] + sql)
    sesuaikan_mariadb(sql)
    assert time.perf_counter() - mulai < BATAS_DETIK


def test_dump_sah_8mb_cepat():
    baris = b"('" + b"a" * 200 + b"','b\\'c',123),"
    sql = AWAL + b"INSERT INTO `t` VALUES " + baris * (DELAPAN_MB // len(baris)) + b"(1,'x',2);\n"
    mulai = time.perf_counter()
    assert periksa_sql(sql) is None
    sesuaikan_mariadb(sql)
    assert time.perf_counter() - mulai < BATAS_DETIK


# ---- R8: pra-pemeriksaan dorong (Task 16) ------------------------------------


def _bertahap(sql: bytes, ukuran: int) -> str | None:
    p = PemeriksaTerapkan()
    for i in range(0, len(sql), ukuran):
        alasan = p.tambah(sql[i:i + ukuran])
        if alasan is not None:
            return alasan
    return p.akhir()


@pytest.mark.parametrize("sql", [
    AWAL + b"INSERT INTO `t` VALUES (1,'a -- b # c /* d */ ; PARTITION'),(2,\"x\\\"; -- y\");\n",
    # Komentar di AWAL pernyataan dibuang connector (tanpa_komentar_awal).
    b"/*!40101 SET NAMES utf8mb4 */;\n-- kepala\n# lagi\n" + AWAL,
    b"-- a\n/* b */ CREATE TABLE `partition` (`subpartition` int);\n",
    # Kutip ganda dua kali di dalam teks dan escape backslash.
    b"INSERT INTO `t` VALUES ('it''s','a\\'b','c\\\\');\n",
    # "--" tanpa spasi sesudahnya bukan komentar (pengurai connector).
    b"INSERT INTO `t` VALUES (1--1),(2---1),(3/2);\n",
    b"",
])
def test_terapkan_menerima(sql):
    assert periksa_terapkan(sql) is None
    for ukuran in (1, 2, 3, 7):
        assert _bertahap(sql, ukuran) is None


@pytest.mark.parametrize("sql,alasan", [
    (b"CREATE TABLE `t` (`id` int) /*!50100 PARTITION BY HASH (`id`) */;\n", ALASAN_KOMENTAR),
    (b"CREATE TABLE `t` (`id` int /* x */);\n", ALASAN_KOMENTAR),
    (b"INSERT INTO `t` VALUES (1); INSERT INTO `t` VALUES (2) # x\n;", ALASAN_KOMENTAR),
    (b"INSERT INTO `t` VALUES (1) -- x\n;", ALASAN_KOMENTAR),
    (b"INSERT INTO `t` VALUES (1)--\t\n;", ALASAN_KOMENTAR),
    # Sama seperti ada_komentar() connector: "--- " memuat "-- " di posisi kedua.
    (b"INSERT INTO `t` VALUES (1)--- x\n;", ALASAN_KOMENTAR),
    (b"CREATE TABLE `t` (`id` int) PARTITION BY HASH (`id`) PARTITIONS 4;\n", ALASAN_PARTISI),
    (b"CREATE TABLE `t` (`id` int) partition by key () subpartition by hash(id);\n", ALASAN_PARTISI),
    (b"CREATE TABLE `t` (`id` int) PARTITION BY KEY ()", ALASAN_PARTISI),
    (b"INSERT INTO `t` VALUES ('tidak ditutup);\n", ALASAN_TERBUKA),
    (b"CREATE TABLE `t (`id` int);\n", ALASAN_TERBUKA),
    (b"/* kepala tanpa penutup", ALASAN_TERBUKA),
])
def test_terapkan_menolak(sql, alasan):
    assert periksa_terapkan(sql) == alasan
    for ukuran in (1, 2, 3, 7):
        assert _bertahap(sql, ukuran) == alasan


def test_terapkan_kata_partisi_terpotong_antar_bagian_tidak_salah_tangkap():
    # "PARTITIONED_X" terpotong tepat setelah "PARTITION": bukan kata PARTITION.
    p = PemeriksaTerapkan()
    assert p.tambah(b"CREATE TABLE `t` (`id` int) COMMENT='x' PARTITION") is None
    assert p.tambah(b"ED_X=1;\n") is None
    assert p.akhir() is None


def test_berkas_terapkan_digabung_berurutan(tmp_path):
    a, b = tmp_path / "a.sql", tmp_path / "b.sql"
    a.write_bytes(b"INSERT INTO `t` VALUES ('a;")
    b.write_bytes(b"b');\nCREATE TABLE `t` (`id` int) /* x */;\n")
    assert periksa_berkas_terapkan([a, b]) == ALASAN_KOMENTAR
    b.write_bytes(b"b');\n")
    assert periksa_berkas_terapkan([a, b]) is None


@pytest.mark.parametrize("unit", [b"'a',", b"`a` ", b"1,", b"\\", b"/", b"-", b"--", b"/*x*/;", b"'\\"])
def test_terapkan_linear(unit):
    # 2 MB: versi kuadratik butuh menit, versi linear di bawah satu detik;
    # cukup jauh dari batas walau mesin sedang sibuk.
    sql = b"INSERT INTO `t` VALUES (" + unit * (2 * 1024 * 1024 // len(unit))
    mulai = time.perf_counter()
    periksa_terapkan(sql)
    assert time.perf_counter() - mulai < BATAS_DETIK
