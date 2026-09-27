"""Skrip pembantu tiruan: dipanggil sebagai `python pembantu_palsu.py <subperintah> ...`."""

import json
import os
import signal
import subprocess
import sys
import time


def _catat(catatan: str | None, data: dict) -> None:
    if catatan:
        with open(catatan, "a", encoding="utf-8") as f:
            f.write(json.dumps(data) + "\n")


def main() -> int:
    argumen = sys.argv[1:]
    perintah = argumen[0] if argumen else ""
    catatan = os.environ.get("PALSU_CATATAN")
    if perintah == "sopan":
        # Berhenti rapi saat menerima SIGTERM; mencatat bahwa sinyal itu datang.
        def berhenti(_sinyal, _bingkai):
            _catat(catatan, {"sinyal": "TERM"})
            sys.exit(0)

        signal.signal(signal.SIGTERM, berhenti)
        time.sleep(30)
        return 0
    if perintah == "keras-kepala":
        # Mengabaikan SIGTERM dan punya anak di grup proses yang sama, seperti
        # `sudo` yang menjalankan skrip pembantu.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        anak = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        _catat(catatan, {"anak": anak.pid})
        time.sleep(60)
        return 0
    masukan = sys.stdin.buffer.read()
    _catat(catatan, {"argv": argumen, "stdin": masukan.decode("utf-8", "replace")})
    keluar = os.environ.get("PALSU_KELUAR")
    if keluar:
        sys.stderr.write(os.environ.get("PALSU_STDERR", ""))
        return int(keluar)
    if perintah == "status":
        sys.stdout.write(os.environ.get("PALSU_STATUS", "{}"))
    elif perintah == "tidur":
        time.sleep(30)
    elif perintah == "gagal":
        sys.stderr.write("baris lain\nGALAT ditolak: container wp-x sudah ada dan bukan milik staging\n")
        return 3
    elif perintah == "gagal-tanpa-pesan":
        return 4
    elif perintah == "berisik":
        sys.stdout.write("x" * (5 * 1024 * 1024))
    else:
        sys.stdout.write("ok " + " ".join(argumen))
    return 0


if __name__ == "__main__":
    sys.exit(main())
