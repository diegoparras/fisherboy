"""Respeto de robots.txt, cacheado por origen. Ver Capa 1.

El parser de robots se inyecta el texto del robots.txt vía un callable (que el
crawler obtiene con su fetcher SSRF-safe), así este módulo no toca la red y se
testea con texto fijo. Si no hay robots o no se pudo traer, el default es PERMITIR
(comportamiento estándar de la web abierta); el límite real lo ponen depth/max_pages.
"""
from __future__ import annotations

from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


class RobotsChecker:
    def __init__(self, fetch_text: Callable[[str], str | None], *, user_agent: str = "Fisherboy") -> None:
        self._fetch_text = fetch_text
        self._ua = user_agent
        self._cache: dict[str, RobotFileParser | None] = {}

    def _parser_for(self, url: str) -> RobotFileParser | None:
        origin = _origin(url)
        if origin in self._cache:
            return self._cache[origin]
        parser: RobotFileParser | None = None
        try:
            text = self._fetch_text(f"{origin}/robots.txt")
            if text:
                parser = RobotFileParser()
                parser.parse(text.splitlines())
        except Exception:  # noqa: BLE001 — sin robots accesible: permitir
            parser = None
        self._cache[origin] = parser
        return parser

    def allowed(self, url: str) -> bool:
        parser = self._parser_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self._ua, url)
