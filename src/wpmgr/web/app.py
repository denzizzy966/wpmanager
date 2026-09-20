from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from wpmgr.config import get_settings
from wpmgr.web.auth import KUNCI_SESI, ButuhLogin, pengguna_saat_ini, periksa_sandi

AKAR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(AKAR / "templates"))


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

    @app.exception_handler(ButuhLogin)
    async def _ke_login(request: Request, exc: ButuhLogin):
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def beranda(request: Request):
        pengguna = pengguna_saat_ini(request)
        return f"<p>Masuk sebagai {pengguna.email}</p>"

    @app.get("/login")
    async def form_login(request: Request):
        return templates.TemplateResponse(request, "login.html", {"galat": None})

    @app.post("/login")
    async def proses_login(request: Request, email: str = Form(...), password: str = Form(...)):
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
    from wpmgr.web.routes_pages import router as pages_router
    from wpmgr.web.routes_pair import router as pair_router

    app.include_router(api_router)
    app.include_router(pair_router)
    app.include_router(pages_router)
    return app


app = buat_app()
