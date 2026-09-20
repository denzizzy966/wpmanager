"""Membangkitkan tests/fixtures/hmac-test-vectors.json.

Canonical string di tiap kasus adalah kontrak yang ditulis manusia. Tanda
tangannya dihitung dari implementasi Python, lalu dibaca juga oleh test PHP.
Dengan begitu kedua bahasa terikat pada satu dokumen yang sama, dan perubahan
di salah satu sisi langsung merah di sisi itu.
"""

import json
from pathlib import Path

from wpmgr.signing import canonical_string, sign

KASUS = [
    {
        "nama": "get_ping_body_kosong",
        "secret_hex": "a" * 64,
        "method": "GET",
        "path": "/wp-json/wpmgr/v1/ping",
        "timestamp": 1758387600,
        "nonce": "0123456789abcdef0123456789abcdef",
        "body": "",
    },
    {
        "nama": "get_inventory_body_kosong",
        "secret_hex": "b3" * 32,
        "method": "GET",
        "path": "/wp-json/wpmgr/v1/inventory",
        "timestamp": 1700000000,
        "nonce": "ffffffffffffffffffffffffffffffff",
        "body": "",
    },
    {
        "nama": "post_update_dengan_body",
        "secret_hex": "0f" * 32,
        "method": "POST",
        "path": "/wp-json/wpmgr/v1/update",
        "timestamp": 1758387600,
        "nonce": "00112233445566778899aabbccddeeff",
        "body": '{"tipe":"plugin","slug":"elementor/elementor.php","ke_versi":"3.20.1"}',
    },
    {
        "nama": "body_dengan_karakter_non_ascii",
        "secret_hex": "12" * 32,
        "method": "POST",
        "path": "/wp-json/wpmgr/v1/update",
        "timestamp": 1758387601,
        "nonce": "aabbccddeeff00112233445566778899",
        "body": '{"nama":"Tema Café — Ñandú"}',
    },
]


def main() -> None:
    keluaran = []
    for k in KASUS:
        body = k["body"].encode("utf-8")
        keluaran.append(
            {
                **k,
                "canonical": canonical_string(
                    k["method"], k["path"], k["timestamp"], k["nonce"], body
                ),
                "signature": sign(
                    k["secret_hex"],
                    k["method"],
                    k["path"],
                    k["timestamp"],
                    k["nonce"],
                    body,
                ),
            }
        )
    tujuan = Path(__file__).resolve().parents[1] / "tests/fixtures/hmac-test-vectors.json"
    tujuan.parent.mkdir(parents=True, exist_ok=True)
    tujuan.write_text(json.dumps(keluaran, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Ditulis {len(keluaran)} vector ke {tujuan}")


if __name__ == "__main__":
    main()
