"""Router de UI. Se monta SOLO en APP_MODE=standalone (ADR-001).

Sirve una sola página autocontenida (sin build step) que habla con el mismo REST
de la Capa 0. El pipeline es idéntico al de sidekick; lo único propio es esta cara.
"""
from __future__ import annotations

import html
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

_INDEX = Path(__file__).parent / "index.html"
_I18N = Path(__file__).parent / "i18n.js"
_CSS = Path(__file__).parent / "escriba-ui.css"


def build_ui_router(escriba_web_url: str = "", auth_mode: str = "local") -> APIRouter:
    router = APIRouter()

    # Inyecta el sitio de Escriba (ESCRIBA_WEB_URL) en el <meta> al arrancar. Vacío → "/".
    page = _INDEX.read_text(encoding="utf-8").replace(
        '<meta name="fb-escriba-url" content="" />',
        f'<meta name="fb-escriba-url" content="{html.escape(escriba_web_url, quote=True)}" />',
    )
    # Modo de auth: en 'federado' la pantalla de login muestra "Entrar con Lockatus"
    # (delega en el hub) en vez del campo de clave por rol.
    page = page.replace(
        '<meta name="fb-auth-mode" content="" />',
        f'<meta name="fb-auth-mode" content="{html.escape(auth_mode, quote=True)}" />',
    )

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return page

    @router.get("/i18n.js", include_in_schema=False)
    async def i18n() -> Response:
        return Response(_I18N.read_text(encoding="utf-8"),
                        media_type="application/javascript; charset=utf-8")

    @router.get("/escriba-ui.css", include_in_schema=False)
    async def escriba_ui_css() -> Response:
        # Sistema de diseño canónico de la Suite (branding/escriba-ui.css). Se importa
        # ANTES del CSS propio en index.html; lo único que la app agrega es su acento.
        return Response(_CSS.read_text(encoding="utf-8"),
                        media_type="text/css; charset=utf-8")

    return router
