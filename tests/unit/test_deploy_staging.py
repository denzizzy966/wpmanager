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
        "listen 443 ssl http2;",
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


def test_sudoers_hanya_untuk_skrip_pembantu():
    baris = [b for b in _teks("sudoers-wpmgr-staging").splitlines() if b and not b.startswith("#")]
    assert baris == [
        "Defaults!/usr/local/sbin/wpmgr-staging env_reset",
        "wpmgr ALL=(root) NOPASSWD: /usr/local/sbin/wpmgr-staging",
    ]


def test_unit_siapkan_setelah_docker():
    t = _teks("wpmgr-staging-siapkan.service")
    assert "After=docker.service" in t and "Requires=docker.service" in t
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
                  "WPMGR_STAGING_DOMAIN", "Izinkan staging", "digest.lock"):
        assert wajib in readme, wajib
