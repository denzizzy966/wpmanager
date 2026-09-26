import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from wpmgr import db
from wpmgr.config import get_settings
from wpmgr.keamanan import nilai_keamanan, status_error
from wpmgr.kesehatan import susun_kesehatan
from wpmgr.models import (
    ActivityLog,
    CatatanError,
    KejadianLogin,
    LoginGagal,
    PackageType,
    Site,
    SitePackage,
    SiteStatus,
    UptimeInsiden,
    User,
)
from wpmgr.ssl_cek import sisa_hari_ssl
from wpmgr.traffic import (
    CATATAN_DUA_SUMBER,
    POLA_PROPERTY,
    anomali_site,
    ringkasan_traffic,
)
from wpmgr.uagent import urai_ua
from wpmgr.uptime import persen_uptime, rata_waktu_ms, teks_durasi, uptime_harian
from wpmgr.web.auth import pengguna_api

router = APIRouter()
PenggunaApi = Annotated[User, Depends(pengguna_api)]

TEKS_STATUS_ERROR = {"selesai": "Selesai", "baru": "Baru", "masih_terjadi": "Masih terjadi",
                     "berhenti": "Berhenti"}
TEKS_STATUS_UPTIME = {"naik": "Naik", "mati": "Mati", "terblokir": "Terblokir",
                      "belum_dicek": "Belum dicek"}
TEKS_KOMPONEN = {"plugin": "Plugin", "mu-plugin": "Must-use plugin", "theme": "Tema",
                 "core": "WordPress core", "lainnya": "Lainnya"}
# Batas JUMLAH IP (bukan baris mentah) yang dikembalikan /logins: agregasi
# jumlah per IP dilakukan penuh di SQL atas SEMUA baris yang cocok (supaya
# totalnya selalu akurat), lalu hasil yang sudah diringkas itu yang dipotong
# ke sekian IP paling deras. Ini beda dari LIMIT baris mentah sebelum agregasi
# (bug lama): LIMIT baris mentah bisa menjatuhkan baris "kecil" -- misalnya
# serangan username-spray yang menyebar jadi ribuan baris ber-jumlah=1 per
# jam/username -- sehingga total per IP salah dan IP itu bisa hilang total
# dari daftar walau ia sebenarnya salah satu yang paling deras.
BATAS_IP_LOGIN_GAGAL = 200
# Per IP yang lolos ke 200 besar itu, jumlah username teratas dan sampel user
# agent yang diperiksa juga dibatasi -- serangan username-spray terhadap SATU
# IP bisa mencoba ribuan username berbeda, dan kita hanya perlu representasi,
# bukan semuanya.
BATAS_USERNAME_PER_IP = 5
BATAS_UA_SAMPEL_PER_IP = 20
# login_gagal disimpan per ember jam yang berlabel AWAL jam: ember berlabel
# 24 jam 30 menit lalu masih memuat percobaan dari 23 jam 30 menit lalu.
# Jendela `hari` dilonggarkan satu jam supaya ember di tepinya tidak hilang
# (sama seperti jendela status keamanan, koreksi #11).
_MARJIN_EMBER = timedelta(hours=1)


@router.get("/api/kesehatan")
def kesehatan(pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        return susun_kesehatan(sesi)


def _sekarang() -> datetime:
    return datetime.now(timezone.utc)


def _iso(nilai):
    return nilai.isoformat() if nilai else None


def _site(sesi, site_id: uuid.UUID) -> Site:
    site = sesi.get(Site, site_id)
    if site is None:
        raise HTTPException(status_code=404, detail="Site tidak ditemukan")
    return site


@router.get("/api/sites/{site_id}/uptime")
def uptime_site(site_id: uuid.UUID, pengguna: PenggunaApi, hari: int = 30):
    hari = max(1, min(hari, 90))
    sekarang = _sekarang()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        insiden = sesi.scalars(
            select(UptimeInsiden).where(UptimeInsiden.site_id == site.id)
            .order_by(UptimeInsiden.mulai.desc()).limit(50)
        ).all()
        sisa = sisa_hari_ssl(site, sekarang)
        if site.ssl_error:
            ssl_teks = site.ssl_error
        elif sisa is None:
            ssl_teks = "Belum dicek"
        else:
            ssl_teks = f"Berlaku sampai {site.ssl_kedaluwarsa:%Y-%m-%d} ({sisa} hari lagi)"
        return {
            "status": site.uptime_status.value,
            "status_teks": TEKS_STATUS_UPTIME[site.uptime_status.value],
            "sejak": _iso(site.uptime_sejak),
            "persen": {
                "24j": persen_uptime(sesi, site.id, sekarang - timedelta(days=1)),
                "7h": persen_uptime(sesi, site.id, sekarang - timedelta(days=7)),
                "30h": persen_uptime(sesi, site.id, sekarang - timedelta(days=30)),
            },
            "rata_ms": rata_waktu_ms(sesi, site.id, sekarang - timedelta(days=1)),
            "harian": uptime_harian(sesi, site.id, sekarang, hari),
            "insiden": [
                {"id": i.id, "mulai": _iso(i.mulai), "selesai": _iso(i.selesai),
                 "durasi": teks_durasi(((i.selesai or sekarang) - i.mulai).total_seconds()),
                 "penyebab": i.penyebab}
                for i in insiden
            ],
            "ssl": {"sisa_hari": sisa, "error": site.ssl_error, "teks": ssl_teks},
        }


@router.get("/api/sites/{site_id}/traffic")
def traffic_site(site_id: uuid.UUID, pengguna: PenggunaApi, hari: int = 30):
    hari = max(1, min(hari, 90))
    sampai = _sekarang().date()
    dari = sampai - timedelta(days=hari - 1)
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        return {
            "plugin": ringkasan_traffic(sesi, site.id, dari, sampai, "plugin"),
            "ga4": ringkasan_traffic(sesi, site.id, dari, sampai, "ga4") if site.ga4_property_id else None,
            "ga4_terpasang": bool(site.ga4_property_id),
            "ga4_aktif": bool(get_settings().ga4_credentials),
            "ga4_error": site.ga4_error,
            "anomali": anomali_site(sesi, site.id, sampai),
            "catatan": CATATAN_DUA_SUMBER,
        }


def _nama_komponen(paket: list[SitePackage], tipe: str, slug: str | None) -> str | None:
    if not slug:
        return None
    for p in paket:
        if tipe == "plugin" and p.tipe == PackageType.plugin and (
            p.slug == slug or p.slug.startswith(slug + "/")
        ):
            return p.nama
        if tipe == "theme" and p.tipe == PackageType.theme and p.slug == slug:
            return p.nama
    return None


@router.get("/api/sites/{site_id}/errors")
def errors_site(site_id: uuid.UUID, pengguna: PenggunaApi):
    sekarang = _sekarang()
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        paket = sesi.scalars(select(SitePackage).where(SitePackage.site_id == site.id)).all()
        daftar = sesi.scalars(
            select(CatatanError).where(CatatanError.site_id == site.id)
            .order_by(CatatanError.terakhir_terlihat.desc()).limit(500)
        ).all()
        hasil = []
        for e in daftar:
            status = status_error(e, sekarang).value
            nama = _nama_komponen(paket, e.komponen_tipe, e.komponen_slug) or e.komponen_slug
            komponen = TEKS_KOMPONEN.get(e.komponen_tipe, e.komponen_tipe)
            setelah = e.setelah_update or None
            hasil.append({
                "id": e.id,
                "tingkat": e.tingkat,
                "status": status,
                "status_teks": TEKS_STATUS_ERROR[status],
                "komponen_teks": f"{komponen} {nama}" if nama else komponen,
                "pesan": e.pesan,
                "lokasi": f"{e.file}:{e.baris}" if e.file else "—",
                "jumlah": e.jumlah,
                "pertama_terlihat": _iso(e.pertama_terlihat),
                "terakhir_terlihat": _iso(e.terakhir_terlihat),
                "konteks": e.konteks,
                "setelah_update": setelah,
                "setelah_update_teks": (
                    f"muncul setelah update ke v{setelah.get('versi_sesudah')}" if setelah else None
                ),
            })
        return hasil


@router.post("/api/sites/{site_id}/errors/{error_id}/selesai")
def tandai_error_selesai(site_id: uuid.UUID, error_id: int, pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        e = sesi.get(CatatanError, error_id)
        if e is None or e.site_id != site_id:
            raise HTTPException(status_code=404, detail="Error tidak ditemukan")
        e.ditandai_selesai_pada = _sekarang()
        sesi.add(ActivityLog(site_id=site_id, user_id=pengguna.id, level="info",
                             pesan=f"Error ditandai selesai oleh {pengguna.email}",
                             detail={"sidik_jari": e.sidik_jari, "pesan": e.pesan[:200]}))
        sesi.commit()
    return {"ok": True}


@router.get("/api/sites/{site_id}/logins")
def logins_site(site_id: uuid.UUID, pengguna: PenggunaApi, hari: int = 30):
    hari = max(1, min(hari, 90))
    sekarang = _sekarang()
    sejak = sekarang - timedelta(days=hari)
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        keamanan = nilai_keamanan(sesi, site, sekarang)

        def bentuk(k):
            ua = urai_ua(k.user_agent)
            return {"waktu": _iso(k.waktu), "jenis": k.jenis, "username": k.username, "role": k.role,
                    "ip": k.ip, "negara": k.negara, "jalur": k.jalur, "peramban": ua["peramban"],
                    "os": ua["os"], "skrip": ua["skrip"]}

        # Dua query terpisah, bukan satu LIMIT 500 atas semua jenis: admin_baru/
        # jadi_admin jarang tapi login berhasil bisa sangat sering. Satu LIMIT
        # bersama membuat login berhasil yang deras menenggelamkan kejadian admin
        # yang lebih lama tapi masih dalam jendela `hari` -- tabel "Administrator
        # baru" bisa bilang "Tidak ada" padahal `alasan` di atasnya justru
        # menyebut admin itu.
        admin_kejadian = sesi.scalars(
            select(KejadianLogin).where(
                KejadianLogin.site_id == site.id, KejadianLogin.waktu >= sejak,
                KejadianLogin.jenis != "berhasil",
            ).order_by(KejadianLogin.waktu.desc()).limit(500)
        ).all()
        berhasil_kejadian = sesi.scalars(
            select(KejadianLogin).where(
                KejadianLogin.site_id == site.id, KejadianLogin.waktu >= sejak,
                KejadianLogin.jenis == "berhasil",
            ).order_by(KejadianLogin.waktu.desc()).limit(500)
        ).all()

        # Langkah 1: total per IP dihitung SQL SUM atas SEMUA baris yang cocok
        # (tanpa LIMIT baris mentah apa pun di sini) -- baru hasil yang sudah
        # diringkas ini yang dipotong ke BATAS_IP_LOGIN_GAGAL IP paling deras.
        # Totalnya selalu akurat berapa pun banyak baris mentah di baliknya.
        agregat_ip = sesi.execute(
            select(
                LoginGagal.ip,
                func.sum(LoginGagal.jumlah).label("jumlah"),
                func.max(LoginGagal.negara).label("negara"),
                func.array_agg(LoginGagal.jalur.distinct()).label("jalur"),
            )
            .where(LoginGagal.site_id == site.id, LoginGagal.jam > sejak - _MARJIN_EMBER)
            .group_by(LoginGagal.ip)
            .order_by(func.sum(LoginGagal.jumlah).desc(), LoginGagal.ip)
            .limit(BATAS_IP_LOGIN_GAGAL)
        ).all()
        ip_teratas = [baris.ip for baris in agregat_ip]

        # Langkah 2: username teratas per IP, dibatasi PER IP lewat row_number()
        # (bukan lagi dengan menyortir baris mentah global) -- serangan
        # username-spray terhadap satu IP tetap menampilkan 5 username
        # terbanyak IP itu, bukan lima nama pertama yang kebetulan lolos LIMIT
        # global.
        username_per_ip: dict = defaultdict(list)
        if ip_teratas:
            peringkat_username = (
                select(
                    LoginGagal.ip,
                    LoginGagal.username,
                    func.sum(LoginGagal.jumlah).label("jumlah"),
                    func.row_number().over(
                        partition_by=LoginGagal.ip,
                        order_by=func.sum(LoginGagal.jumlah).desc(),
                    ).label("rn"),
                )
                .where(LoginGagal.site_id == site.id, LoginGagal.jam > sejak - _MARJIN_EMBER,
                       LoginGagal.ip.in_(ip_teratas))
                .group_by(LoginGagal.ip, LoginGagal.username)
            ).subquery()
            baris_username = sesi.execute(
                select(peringkat_username.c.ip, peringkat_username.c.username)
                .where(peringkat_username.c.rn <= BATAS_USERNAME_PER_IP)
                .order_by(peringkat_username.c.ip, peringkat_username.c.rn)
            ).all()
            for ip, username in baris_username:
                username_per_ip[ip].append(username)

        # Langkah 3: "skrip" hanya butuh SATU baris yang User-Agent-nya
        # menyerupai skrip, jadi cukup sampel N baris terbaru per IP (bukan
        # agregat presisi seperti total/username) -- diperiksa di Python lewat
        # urai_ua() yang sama dipakai di tempat lain, bukan diduplikasi ke SQL.
        skrip_per_ip: dict = defaultdict(bool)
        if ip_teratas:
            peringkat_ua = (
                select(
                    LoginGagal.ip,
                    LoginGagal.user_agent,
                    func.row_number().over(
                        partition_by=LoginGagal.ip, order_by=LoginGagal.jam.desc(),
                    ).label("rn"),
                )
                .where(LoginGagal.site_id == site.id, LoginGagal.jam > sejak - _MARJIN_EMBER,
                       LoginGagal.ip.in_(ip_teratas))
            ).subquery()
            baris_ua = sesi.execute(
                select(peringkat_ua.c.ip, peringkat_ua.c.user_agent)
                .where(peringkat_ua.c.rn <= BATAS_UA_SAMPEL_PER_IP)
            ).all()
            for ip, user_agent in baris_ua:
                if urai_ua(user_agent)["skrip"]:
                    skrip_per_ip[ip] = True

        gagal = [
            {"ip": baris.ip or "(IP lain)", "negara": baris.negara, "jumlah": int(baris.jumlah),
             "username": username_per_ip.get(baris.ip, []), "jalur": sorted(baris.jalur),
             "skrip": skrip_per_ip.get(baris.ip, False)}
            for baris in agregat_ip
        ]

        return {
            "status": keamanan.status.value,
            "alasan": keamanan.alasan,
            "percobaan_sejam": keamanan.percobaan_sejam,
            "diperiksa_pada": _iso(site.keamanan_diperiksa_pada),
            "berhasil": [bentuk(k) for k in berhasil_kejadian],
            "admin": [bentuk(k) for k in admin_kejadian],
            "gagal": gagal,
        }


@router.post("/api/sites/{site_id}/keamanan/diperiksa")
def keamanan_diperiksa(site_id: uuid.UUID, pengguna: PenggunaApi):
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        site.keamanan_diperiksa_pada = _sekarang()
        sesi.add(ActivityLog(site_id=site.id, user_id=pengguna.id, level="info",
                             pesan=f"Keamanan ditandai sudah diperiksa oleh {pengguna.email}"))
        sesi.commit()
    return {"ok": True}


# Batas jumlah IP yang dikembalikan /api/keamanan/penyerang. Sama seperti
# BATAS_IP_LOGIN_GAGAL di atas: agregasi (SUM/COUNT DISTINCT/MAX) dilakukan
# penuh di SQL atas SEMUA baris login_gagal yang cocok, GROUP BY ip, baru
# hasil yang sudah diringkas itu dipotong LIMIT ke sekian IP paling deras --
# tidak pernah menarik baris mentah ke Python sebelum agregasi (bug yang
# sama seperti /logins sebelum Task 22 memperbaikinya, tapi lebih parah di
# sini karena lintas SEMUA site sekaligus).
BATAS_IP_PENYERANG = 500
# Per IP yang lolos ke 500 besar itu, jumlah nama site/username yang
# ditampilkan di string gabungan juga dibatasi PER IP lewat row_number() --
# satu IP bisa menyerang ratusan site atau mencoba ribuan username.
BATAS_SITE_PER_IP = 5
BATAS_USERNAME_PER_IP_PENYERANG = 5
BATAS_UA_SAMPEL_PER_IP_PENYERANG = 20
# LoginGagal.jam adalah AWAL jam (dibulatkan ke bawah oleh connector), bukan
# tengahnya -- koreksi #11 rencana Lapis 2. Jendela "N jam terakhir" mundur
# satu jam ekstra supaya ember yang sedang berjalan ikut terhitung di menit
# berapa pun permintaan ini dibuat, bukan cuma tepat pukul xx:00.
_MARJIN_EMBER_PENYERANG = timedelta(hours=1)


@router.get("/api/keamanan/penyerang")
def penyerang(pengguna: PenggunaApi, jam: int = 24):
    jam = max(1, min(jam, 24 * 30))
    sejak = _sekarang() - timedelta(hours=jam) - _MARJIN_EMBER_PENYERANG
    # ip != "": baris "(IP lain)" adalah ember overflow yang mencampur banyak
    # IP berbeda dari banyak site -- lihat catatan koreksi #3. Memasukkannya
    # di sini akan membuat satu baris palsu yang seolah-olah satu IP
    # menyerang hampir semua site sekaligus.
    filter_dasar = (LoginGagal.jam > sejak, LoginGagal.ip != "", Site.status != SiteStatus.disabled)

    with db.SessionLocal() as sesi:
        # Langkah 1: SUM/COUNT DISTINCT/MAX di SQL atas SEMUA baris yang
        # cocok, GROUP BY ip -- baru hasil yang sudah diringkas ini yang
        # dipotong ke BATAS_IP_PENYERANG IP paling deras. jumlah_site dihitung
        # dari site_id (COUNT DISTINCT), BUKAN dari nama site: nama site tidak
        # unik, dua site berbeda boleh bernama sama.
        agregat_ip = sesi.execute(
            select(
                LoginGagal.ip,
                func.sum(LoginGagal.jumlah).label("jumlah"),
                func.count(func.distinct(LoginGagal.site_id)).label("jumlah_site"),
                func.max(LoginGagal.jam).label("terakhir"),
                func.max(LoginGagal.negara).label("negara"),
            )
            .join(Site, Site.id == LoginGagal.site_id)
            .where(*filter_dasar)
            .group_by(LoginGagal.ip)
            .order_by(func.sum(LoginGagal.jumlah).desc(), LoginGagal.ip)
            .limit(BATAS_IP_PENYERANG)
        ).all()
        ip_teratas = [baris.ip for baris in agregat_ip]

        site_per_ip: dict = defaultdict(list)
        username_per_ip: dict = defaultdict(list)
        jalur_per_ip: dict = defaultdict(set)
        skrip_per_ip: dict = defaultdict(bool)

        if ip_teratas:
            filter_ip = (*filter_dasar, LoginGagal.ip.in_(ip_teratas))

            # Langkah 2a: nama site teratas per IP, dibatasi PER IP lewat
            # row_number() atas SITE_ID (bukan nama) -- dua site berbeda
            # dengan nama sama harus tetap tampil sebagai dua entri terpisah
            # di string gabungan, bukan menyusut jadi satu lewat GROUP BY nama.
            peringkat_site = (
                select(
                    LoginGagal.ip,
                    Site.nama,
                    func.sum(LoginGagal.jumlah).label("jumlah"),
                    func.row_number().over(
                        partition_by=LoginGagal.ip,
                        order_by=(func.sum(LoginGagal.jumlah).desc(), Site.nama, LoginGagal.site_id),
                    ).label("rn"),
                )
                .join(Site, Site.id == LoginGagal.site_id)
                .where(*filter_ip)
                .group_by(LoginGagal.ip, LoginGagal.site_id, Site.nama)
            ).subquery()
            baris_site = sesi.execute(
                select(peringkat_site.c.ip, peringkat_site.c.nama)
                .where(peringkat_site.c.rn <= BATAS_SITE_PER_IP)
                .order_by(peringkat_site.c.ip, peringkat_site.c.rn)
            ).all()
            for ip, nama in baris_site:
                site_per_ip[ip].append(nama)

            # Langkah 2b: username teratas per IP -- pola sama seperti /logins
            # (Task 22): row_number() PER IP, bukan LIMIT baris mentah global.
            peringkat_username = (
                select(
                    LoginGagal.ip,
                    LoginGagal.username,
                    func.sum(LoginGagal.jumlah).label("jumlah"),
                    func.row_number().over(
                        partition_by=LoginGagal.ip,
                        order_by=func.sum(LoginGagal.jumlah).desc(),
                    ).label("rn"),
                )
                .join(Site, Site.id == LoginGagal.site_id)
                .where(*filter_ip)
                .group_by(LoginGagal.ip, LoginGagal.username)
            ).subquery()
            baris_username = sesi.execute(
                select(peringkat_username.c.ip, peringkat_username.c.username)
                .where(peringkat_username.c.rn <= BATAS_USERNAME_PER_IP_PENYERANG)
                .order_by(peringkat_username.c.ip, peringkat_username.c.rn)
            ).all()
            for ip, username in baris_username:
                username_per_ip[ip].append(username)

            # Langkah 2c: jalur hanya berisi tiga nilai tetap ("form",
            # "xmlrpc", "app_password") -- DISTINCT langsung tanpa
            # row_number(), tidak akan pernah membengkak seberapa pun banyak
            # baris login_gagal di baliknya.
            baris_jalur = sesi.execute(
                select(LoginGagal.ip, LoginGagal.jalur).distinct()
                .join(Site, Site.id == LoginGagal.site_id)
                .where(*filter_ip)
            ).all()
            for ip, jalur in baris_jalur:
                jalur_per_ip[ip].add(jalur)

            # Langkah 2d: "skrip" cukup sampel N baris terbaru per IP lintas
            # site -- pola sama seperti /logins, diperiksa lewat urai_ua()
            # yang sama, bukan diduplikasi ke SQL.
            peringkat_ua = (
                select(
                    LoginGagal.ip,
                    LoginGagal.user_agent,
                    func.row_number().over(
                        partition_by=LoginGagal.ip, order_by=LoginGagal.jam.desc(),
                    ).label("rn"),
                )
                .join(Site, Site.id == LoginGagal.site_id)
                .where(*filter_ip)
            ).subquery()
            baris_ua = sesi.execute(
                select(peringkat_ua.c.ip, peringkat_ua.c.user_agent)
                .where(peringkat_ua.c.rn <= BATAS_UA_SAMPEL_PER_IP_PENYERANG)
            ).all()
            for ip, user_agent in baris_ua:
                if urai_ua(user_agent)["skrip"]:
                    skrip_per_ip[ip] = True

    return [
        {
            "ip": baris.ip,
            "negara": baris.negara,
            "jumlah": int(baris.jumlah),
            "jumlah_site": baris.jumlah_site,
            "site": ", ".join(site_per_ip.get(baris.ip, [])),
            "username": ", ".join(username_per_ip.get(baris.ip, [])),
            "jalur": ", ".join(sorted(jalur_per_ip.get(baris.ip, ()))),
            "skrip": skrip_per_ip.get(baris.ip, False),
            "terakhir": _iso(baris.terakhir),
        }
        for baris in agregat_ip
    ]


class PermintaanGA4(BaseModel):
    property_id: str = ""


@router.put("/api/sites/{site_id}/ga4")
def atur_ga4(site_id: uuid.UUID, req: PermintaanGA4, pengguna: PenggunaApi):
    nilai = req.property_id.strip()
    if nilai and not POLA_PROPERTY.match(nilai):
        raise HTTPException(status_code=422,
                            detail="Property ID GA4 berupa 6 sampai 12 digit angka, bukan Measurement ID (G-...).")
    with db.SessionLocal() as sesi:
        site = _site(sesi, site_id)
        baru = nilai or None
        if baru != site.ga4_property_id:
            # Hanya bersihkan status lama kalau property ID SUNGGUHAN berubah:
            # menyimpan ulang nilai yang sama (klik "Simpan" tanpa mengubah apa
            # pun) sebelumnya diam-diam menghapus ga4_error yang justru masih
            # berlaku untuk property yang sama itu.
            site.ga4_property_id = baru
            site.ga4_error = None
            site.ga4_diambil_pada = None
        sesi.commit()
    return {"ok": True}
