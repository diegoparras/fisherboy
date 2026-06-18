"""Router de UI. Se monta SOLO en APP_MODE=standalone (ADR-001).

Sirve una sola página autocontenida (sin build step) que habla con el mismo REST
de la Capa 0. El pipeline es idéntico al de sidekick; lo único propio es esta cara.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

_INDEX = Path(__file__).parent / "index.html"


def build_ui_router() -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return _INDEX.read_text(encoding="utf-8")

    return router
