def _komponen(versi: str) -> tuple[int, ...] | None:
    # Akhiran pra-rilis ("2.0.1-uji") dibuang: penanda "usang" hanya perlu
    # tahu apakah nomor rilisnya tertinggal.
    try:
        return tuple(int(bagian) for bagian in versi.strip().split("-")[0].split("."))
    except ValueError:
        return None


def lebih_lama(a: str | None, b: str | None) -> bool:
    """Apakah versi `a` secara numerik lebih rendah dari `b`.

    Versi yang tidak dapat diurai menghasilkan False: lebih baik tidak
    menandai "usang" daripada menandainya berdasarkan tebakan.
    """
    if not a or not b:
        return False
    ka, kb = _komponen(a), _komponen(b)
    if ka is None or kb is None:
        return False
    panjang = max(len(ka), len(kb))
    return ka + (0,) * (panjang - len(ka)) < kb + (0,) * (panjang - len(kb))
