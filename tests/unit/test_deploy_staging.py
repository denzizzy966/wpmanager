import re
from pathlib import Path

AKAR = Path(__file__).resolve().parents[2]
STAGING = AKAR / "deploy" / "staging"


def _teks(nama: str) -> str:
    return (STAGING / nama).read_text(encoding="utf-8")


def test_nginx_host_memuat_direktif_wajib():
    t = _teks("nginx-wpmgr-staging.conf")
    for wajib in (
        "map $ssl_server_name $wpmgr_stg_host {",
        r"~^(?<wpmgr_stg_nama>[a-z0-9-]{1,40})\.staging\.halosocia\.my\.id$",
        "listen 80;",
        "listen 443 ssl;",
        "location ^~ /.well-known/acme-challenge/ {",
        "root /var/lib/wpmgr/acme;",
        "return 301 https://$host$request_uri;",
        "ssl_certificate     /var/lib/wpmgr/certs/$wpmgr_stg_host/fullchain.pem;",
        "ssl_certificate_key /var/lib/wpmgr/certs/$wpmgr_stg_host/privkey.pem;",
        "include /etc/letsencrypt/options-ssl-nginx.conf;",
        "client_max_body_size 64M;",
        "proxy_pass http://127.0.0.1:8090;",
        "proxy_set_header X-Forwarded-Proto https;",
    ):
        assert wajib in t, wajib
    # SNI mentah tidak boleh menjadi bagian path berkas yang dibuka root.
    assert "certs/$ssl_server_name" not in t
    assert "default_server" not in t
    # http2 di nginx 1.24 adalah opsi per soket, bukan per server block:
    # mengaktifkannya di sini akan ikut menyalakan HTTP/2 untuk site lain
    # yang berbagi soket :443 (review Task 21, item penting #4). Diperiksa
    # per baris "listen", bukan seluruh berkas, supaya komentar yang
    # menjelaskan alasannya (yang sah menyebut kata "http2") tidak ikut
    # menggagalkan test ini.
    for baris in t.splitlines():
        if baris.strip().startswith("listen"):
            assert "http2" not in baris, baris
    # Router staging (bukan host) yang memasang X-Robots-Tag di setiap
    # respons; host tidak boleh menggandakannya (review Task 21, minor).
    assert "add_header X-Robots-Tag" not in t


def test_server_name_wildcard_bukan_regex():
    """Review Task 21 item penting #5: server_name memakai wildcard nginx
    biasa di kedua server, bukan regex -- map $ssl_server_name tetap
    satu-satunya validator nama staging."""
    t = _teks("nginx-wpmgr-staging.conf")
    assert t.count("server_name *.staging.halosocia.my.id;") == 2
    assert "server_name ~" not in t


def test_pola_regex_yang_tersisa_dikutip_f6():
    """Putusan F6: setiap pola regex nginx yang memuat "{" wajib dikutip,
    supaya nginx tidak membacanya sebagai awal blok, bukan bagian pola."""
    t = _teks("nginx-wpmgr-staging.conf")
    baris_regex = [b for b in t.splitlines() if "~^" in b and "{" in b]
    # Pola map $ssl_server_name seharusnya masih ada dan jadi satu-satunya
    # pola regex berkurung kurawal di berkas ini (server_name bukan regex).
    assert len(baris_regex) == 1
    assert re.search(r'"~\^', baris_regex[0]), baris_regex[0]


def test_sudoers_hanya_untuk_skrip_pembantu():
    baris = [b for b in _teks("sudoers-wpmgr-staging").splitlines() if b and not b.startswith("#")]
    assert baris == [
        "Defaults!/usr/local/sbin/wpmgr-staging env_reset",
        "wpmgr ALL=(root) NOPASSWD: /usr/local/sbin/wpmgr-staging",
    ]


def test_unit_siapkan_setelah_docker():
    t = _teks("wpmgr-staging-siapkan.service")
    assert "After=docker.service" in t and "Requires=docker.service" in t
    assert "network-online.target" in t
    assert "ExecStart=/usr/local/sbin/wpmgr-staging siapkan" in t
    assert "Type=oneshot" in t


def test_contoh_konfigurasi_hanya_kunci_yang_dikenal_skrip():
    skrip = _teks("wpmgr-staging")
    dikenal = set(re.search(r"case \"\$kunci\" in\n\s+(.+?)\)", skrip, re.DOTALL).group(1)
                  .replace("\\\n", "").replace(" ", "").split("|"))
    for baris in _teks("staging.conf.contoh").splitlines():
        if baris and not baris.startswith("#"):
            assert baris.split("=", 1)[0] in dikenal, baris


def test_crontab_dan_worker_staging():
    crontab = (AKAR / "deploy" / "crontab").read_text(encoding="utf-8")
    for perintah in ("staging-jeda-otomatis", "renew-staging-certs", "prune-staging"):
        assert f"wpmgr.cli {perintah}" in crontab
    assert "Environment=WPMGR_WORKER_INSTANS=%i" in (AKAR / "deploy" / "wpmgr-worker@.service").read_text(encoding="utf-8")


def test_readme_menjelaskan_pemasangan():
    readme = (AKAR / "README.md").read_text(encoding="utf-8")
    for wajib in ("## Staging (Lapis 3)", "wpmgr-worker@staging", "/etc/sudoers.d/wpmgr-staging",
                  "visudo -cf", "wpmgr-staging siapkan", "/etc/nginx/sites-enabled/wpmgr-staging.conf",
                  "WPMGR_STAGING_DOMAIN", "Izinkan staging", "digest.lock",
                  # Review Task 21 fix round 1:
                  "NGINX_GROUP",  # kunci staging.conf baru untuk grup pemilik privkey
                  "sedang diterbitkan",  # perilaku sebelum sertifikat terbit (item penting #3)
                  r"grep -rn 'server_name.*\*\.halosocia' /etc/nginx/sites-enabled",  # pra-cek item #5
                  "root-owned",  # /var/lib/wpmgr harus tetap root-owned (minor)
                  ):
        assert wajib in readme, wajib


def test_crontab_hosting_zona_vps_tanpa_cron_tz():
    """cron VPS tidak mengenal CRON_TZ (putusan L19): jadwal hosting dalam zona VPS Europe/Berlin."""
    teks = (AKAR / "deploy" / "crontab").read_text(encoding="utf-8")
    baris = [b for b in teks.splitlines() if b.strip() and not b.startswith("#")]
    assert not any("CRON_TZ" in b for b in baris)
    assert "Europe/Berlin" in teks
    assert any(b.startswith("*/10 * * * *") and "wpmgr.cli hosting-cek-dns" in b for b in baris)
    assert any(b.startswith("50   21 * * *") and "wpmgr.cli renew-hosting-certs" in b for b in baris)


def test_crontab_backup_hosting_zona_vps():
    """Backup harian ±02:30 WIB = 21:30 Europe/Berlin (putusan L19), bukan 02:30 zona VPS."""
    teks = (AKAR / "deploy" / "crontab").read_text(encoding="utf-8")
    baris = [b for b in teks.splitlines() if b.strip() and not b.startswith("#")]
    backup = [b for b in baris if "wpmgr.cli backup-hosting" in b]
    assert len(backup) == 1 and backup[0].startswith("30   21 * * *")
    assert ">> /var/log/wpmgr/cron.log 2>&1" in backup[0]
    assert "02:30 WIB" in teks


def test_crontab_berakhiran_baris_lf():
    assert b"\r" not in (AKAR / "deploy" / "crontab").read_bytes()
    assert "deploy/crontab text eol=lf" in (AKAR / ".gitattributes").read_text(encoding="utf-8").splitlines()


def test_nginx_hosting_hanya_include_direktori_domain():
    baris = [b.strip() for b in _teks("nginx-wpmgr-hosting.conf").splitlines()
             if b.strip() and not b.strip().startswith("#")]
    assert baris == ["include /etc/nginx/wpmgr-hosting/*.conf;"]


def test_unit_siapkan_juga_menyiapkan_produksi():
    t = _teks("wpmgr-staging-siapkan.service")
    assert t.index("ExecStart=/usr/local/sbin/wpmgr-staging siapkan") \
        < t.index("ExecStart=/usr/local/sbin/wpmgr-staging prod-siapkan")


def test_unit_siapkan_berurutan_sesudah_docker():
    """siapkan/prod-siapkan memanggil docker, jadi unit WAJIB sesudah Docker (tidak mungkin Before=).
    Jendela reboot (container hidup sebelum iptables) didokumentasikan, bukan ditutup."""
    t = _teks("wpmgr-staging-siapkan.service")
    baris = [b.strip() for b in t.splitlines() if b.strip() and not b.strip().startswith("#")]
    assert "After=docker.service network-online.target" in baris
    assert "Requires=docker.service" in baris
    assert not any(b.startswith("Before=") and "docker" in b for b in baris)
    assert "JENDELA SESUDAH REBOOT" in t
    assert "docker" in (STAGING / "wpmgr-staging").read_text(encoding="utf-8")


def test_contoh_konfigurasi_memuat_kunci_hosting():
    t = _teks("staging.conf.contoh")
    for kunci in ("HOSTING_DIR=/var/lib/wpmgr/hosting", "PROD_SUBNET=172.31.251.0/24",
                  "PROD_ROUTER_PORT=127.0.0.1:8091", "PROD_CERT_DIR=/var/lib/wpmgr/hosting-certs",
                  "NGINX_HOSTING_DIR=/etc/nginx/wpmgr-hosting", "BACKUP_DIR=/var/lib/wpmgr/backup",
                  "IP_PUBLIK=169.58.91.181"):
        assert kunci in t, kunci
    assert "NGINX_UJI_SAJA=1" not in t and "SERTIFIKAT_SENDIRI=1" not in t


def test_readme_menjelaskan_pindah_hosting():
    readme = (AKAR / "README.md").read_text(encoding="utf-8")
    assert readme.index("## Staging (Lapis 3)") < readme.index("## Pindah hosting (Lapis 4)") \
        < readme.index("## Keterbatasan yang diketahui")
    for wajib in ("wpmgr-staging prod-siapkan", "install -d -o wpmgr -g wpmgr -m 0700 /var/lib/wpmgr/hosting",
                  "/etc/nginx/sites-enabled/wpmgr-hosting.conf", "install -d -m 0755 /etc/nginx/wpmgr-hosting",
                  "nginx -T | grep -n server_name", "grep -rn 'allow\\|deny' /etc/nginx/sites-enabled",
                  'grep -rn "set_real_ip_from\\|allow 172\\|allow 10\\.\\|allow 192\\.168" /etc/nginx/',
                  "WPMGR_HOSTING_IPV4", "WPMGR_HOSTING_IPV6", "2a02:c207:2347:2607::1",
                  "tanpa `CRON_TZ`", "02:30 WIB", "03:30 WIB", "21:50 Berlin", "deploy/crontab",
                  "wpmgr-worker@staging", "Izinkan staging", "AAAA", "cdn.hstgr.net", "TTL",
                  "matikan CDN di hPanel", "rizkycahayaraya.com", "Sesudah reboot VPS",
                  "### Pemulihan backup manual", "docker exec wpmgr-prod-db", "wpmgr-staging prod-jalan",
                  "db.sql.gz", "files.tar.gz", "files.sebelum-pulih-", "off-site belum dibuat",
                  "### Melepas site aktif secara manual", "DELETE FROM hosting_vps",
                  "rsync", "mail()", "src/wpmgr/hosting/", "ESTABLISHED,RELATED"):
        assert wajib in readme, wajib
    assert "CRON_TZ=Asia/Jakarta" not in readme

