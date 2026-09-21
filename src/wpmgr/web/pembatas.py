import time
from collections import defaultdict, deque

from fastapi import Request

_PEER_TEPERCAYA = frozenset({"127.0.0.1", "::1"})


def ip_klien(request: Request) -> str:
    """IP klien sesungguhnya, dengan header proxy hanya dipercaya dari loopback.

    Di balik nginx pada deploy standar, `request.client.host` sudah berisi IP
    klien sebenarnya: uvicorn (proxy_headers aktif secara bawaan, hanya
    mempercayai 127.0.0.1/::1) menggantinya dengan entri X-Forwarded-For
    paling kanan yang tidak tepercaya -- alamat yang ditambahkan nginx
    sendiri. Cabang loopback di bawah hanya berlaku bila itu tidak terjadi
    (mis. uvicorn dijalankan dengan --no-proxy-headers).

    Membaca X-Real-IP tanpa syarat berarti siapa pun yang dapat menjangkau
    aplikasi secara langsung dapat mengarang identitas dan melewati pembatas
    laju. Mempercayainya hanya ketika peer TCP adalah loopback -- satu-satunya
    tempat nginx berada -- menutup itu.
    """
    peer = request.client.host if request.client else ""
    if peer in _PEER_TEPERCAYA:
        nyata = request.headers.get("X-Real-IP", "").strip()
        if nyata:
            return nyata
        diteruskan = request.headers.get("X-Forwarded-For", "")
        if diteruskan:
            # nginx menambahkan IP peer di posisi paling KANAN, sehingga entri
            # itulah yang tidak dapat dipalsukan klien.
            return diteruskan.split(",")[-1].strip()
    return peer or "tidak-diketahui"


class PembatasLaju:
    """Jendela geser per IP, disimpan di memori proses.

    Cukup untuk satu proses web di belakang nginx. Bila kelak web dijalankan
    dengan beberapa worker uvicorn, setiap proses menghitung sendiri-sendiri
    dan batas efektifnya menjadi kelipatan jumlah proses.
    """

    def __init__(self, batas: int, jendela_detik: float = 60.0) -> None:
        self.batas = batas
        self.jendela_detik = jendela_detik
        self.jejak: dict[str, deque] = defaultdict(deque)

    def lolos(self, ip: str) -> bool:
        sekarang = time.monotonic()

        # Buang IP yang jendelanya sudah lewat sebelum mengakses jejak[ip],
        # karena akses itu sendiri akan membuat entri baru (defaultdict). Tanpa
        # ini, setiap IP berbeda yang pernah mampir meninggalkan entri selamanya.
        basi = [k for k, v in self.jejak.items() if not v or sekarang - v[-1] > self.jendela_detik]
        for k in basi:
            del self.jejak[k]

        jejak = self.jejak[ip]
        while jejak and sekarang - jejak[0] > self.jendela_detik:
            jejak.popleft()
        if len(jejak) >= self.batas:
            return False
        jejak.append(sekarang)
        return True
