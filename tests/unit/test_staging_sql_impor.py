import time

import pytest

from wpmgr.staging.sql_impor import BATAS_CREATE, periksa_sql, sesuaikan_mariadb

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
