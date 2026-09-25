from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from wpmgr.config import get_settings
from wpmgr.web.auth import KUNCI_SESI, ButuhLogin, periksa_sandi
from wpmgr.web.pembatas import PembatasLaju, ip_klien

AKAR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(AKAR / "templates"))

METODE_MENGUBAH = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Dipanggil server site client, bukan browser, dan diamankan HMAC -- bukan
# sesi. Origin/Referer apa pun yang dibawanya tidak berarti apa-apa.
JALUR_TANPA_CEK_ASAL = frozenset({"/api/pair/confirm"})

BATAS_LOGIN_PER_MENIT = 10

_PORT_BAWAAN = {"http": 80, "https": 443}


def asal(url: str) -> str | None:
    """Origin (skema://host[:port]) dari sebuah URL, atau None bila tak sah.

    Port bawaan dibuang supaya "https://x:443" dan "https://x" dianggap sama,
    seperti browser memperlakukannya. "null" -- yang dikirim browser dari
    iframe ber-sandbox -- tidak punya host, jadi hasilnya None.
    """
    try:
        bagian = urlsplit(url)
        port = bagian.port
    except ValueError:
        return None
    skema = bagian.scheme.lower()
    if not skema or not bagian.hostname:
        return None
    host = bagian.hostname
    if ":" in host:
        host = f"[{host}]"
    if port is not None and port != _PORT_BAWAAN.get(skema):
        return f"{skema}://{host}:{port}"
    return f"{skema}://{host}"


def buat_app() -> FastAPI:
    app = FastAPI(title="WP Manager")
    app.add_middleware(
        SessionMiddleware,
        secret_key=get_settings().session_secret,
        https_only=True,
        same_site="lax",
        max_age=60 * 60 * 12,
    )
    app.mount("/static", StaticFiles(directory=str(AKAR / "static")), name="static")

    asal_dashboard = asal(get_settings().base_url)

    @app.middleware("http")
    async def _tolak_permintaan_lintas_asal(request: Request, call_next):
        # Cookie sesi SameSite=Lax sudah menahan sebagian besar CSRF, tetapi
        # tidak dari subdomain "same-site" (site client yang disusupi di
        # domain yang sama dengan dashboard, misalnya) dan tidak di browser
        # yang tidak menghormatinya. Browser selalu mengirim Origin pada
        # permintaan fetch/form non-GET; ketika Origin tidak ada, Referer
        # adalah pengganti terbaik. Keduanya tidak ada berarti bukan browser
        # (curl, skrip operator) -- dan tanpa cookie curiannya, permintaan
        # itu tidak membawa kewenangan apa pun.
        if request.method in METODE_MENGUBAH and request.url.path not in JALUR_TANPA_CEK_ASAL:
            origin = request.headers.get("origin")
            referer = request.headers.get("referer")
            if origin is not None:
                sah = asal(origin) == asal_dashboard
            elif referer is not None:
                sah = asal(referer) == asal_dashboard
            else:
                sah = True
            if not sah:
                return JSONResponse(
                    {"detail": "Asal permintaan tidak diizinkan"}, status_code=403
                )
        return await call_next(request)

    # Per app, bukan level modul: hitungannya tidak boleh bocor antar-instans
    # app (setiap test membangun app-nya sendiri dan login di dalamnya).
    pembatas_login = PembatasLaju(BATAS_LOGIN_PER_MENIT)

    @app.exception_handler(ButuhLogin)
    async def _ke_login(request: Request, exc: ButuhLogin):
        return RedirectResponse("/login", status_code=303)

    @app.get("/login")
    async def form_login(request: Request):
        return templates.TemplateResponse(request, "login.html", {"galat": None})

    @app.post("/login")
    async def proses_login(request: Request, email: str = Form(...), password: str = Form(...)):
        # Diperiksa sebelum argon2: selain menahan tebakan sandi, ini juga
        # menahan siapa pun yang ingin menghabiskan CPU lewat hash yang
        # sengaja mahal.
        if not pembatas_login.lolos(ip_klien(request)):
            return templates.TemplateResponse(
                request, "login.html",
                {"galat": "Terlalu banyak percobaan masuk. Coba lagi dalam satu menit."},
                status_code=429,
            )
        pengguna = periksa_sandi(email, password)
        if pengguna is None:
            return templates.TemplateResponse(
                request, "login.html", {"galat": "Email dan kata sandi tidak cocok."}
            )
        request.session[KUNCI_SESI] = str(pengguna.id)
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    from wpmgr.web.routes_api import router as api_router
    from wpmgr.web.routes_monitoring import router as monitoring_router
    from wpmgr.web.routes_pages import router as pages_router
    from wpmgr.web.routes_pair import router as pair_router

    app.include_router(api_router)
    app.include_router(pair_router)
    app.include_router(monitoring_router)
    app.include_router(pages_router)
    return app


app = buat_app()
