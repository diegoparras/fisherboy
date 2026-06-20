"""Configuración: lee APP_MODE y el resto del entorno una sola vez.

El modo se resuelve en tiempo de arranque (ADR-001). El núcleo es idéntico en
los dos modos; `APP_MODE` solo decide si se monta el router de UI y a quién se
delega la conversión documental.
"""
from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path

# privacy_matrix.yaml vive en la raíz del repo, al lado del paquete `app/`. Esta
# ruta resuelve igual en local (repo) y en Docker (WORKDIR /app, paquete /app/app).
_DEFAULT_MATRIX = str(Path(__file__).resolve().parent.parent / "privacy_matrix.yaml")


class AppMode(str, Enum):
    SIDEKICK = "sidekick"
    STANDALONE = "standalone"


class Settings:
    """Snapshot del entorno. Inmutable de hecho: se lee una vez vía get_settings()."""

    def __init__(self, env: dict[str, str] | None = None) -> None:
        e = env if env is not None else os.environ

        raw_mode = (e.get("APP_MODE") or "sidekick").strip().lower()
        try:
            self.app_mode = AppMode(raw_mode)
        except ValueError:
            raise ValueError(
                f"APP_MODE inválido: {raw_mode!r}. Usá 'sidekick' o 'standalone'."
            )

        self.redis_url = e.get("REDIS_URL", "redis://fisherboy-redis:6379/0")
        self.anonimal_url = (e.get("ANONIMAL_URL", "") or "").rstrip("/")
        self.escriba_url = (e.get("ESCRIBA_URL", "") or "").rstrip("/")
        # Sitio PÚBLICO de Escriba (para el botón "Enviar a Escriba" de la UI). Distinto del
        # ESCRIBA_URL de arriba, que es la API interna que usa el backend para convertir docs.
        self.escriba_web_url = (e.get("ESCRIBA_WEB_URL", "") or "").strip()
        self.anonimal_token = e.get("ANONIMAL_TOKEN", "")  # ADR-003 (v2): auth de servicio
        self.escriba_token = e.get("ESCRIBA_TOKEN", "")    # auth de servicio hacia Escriba (pendiente)

        self.llm_api_base_url = e.get("LLM_API_BASE_URL", "")
        self.llm_api_key = e.get("LLM_API_KEY", "")
        self.llm_model = e.get("LLM_MODEL", "gpt-4o-mini")
        self.database_url = e.get("DATABASE_URL", "")  # Postgres + pgvector (Capa 7)

        # Modo reversible (Capa 6): clave Fernet para cifrar la tabla de mapeo y TTL.
        self.reversible_key = e.get("REVERSIBLE_KEY", "")
        self.reversible_ttl_s = int(e.get("REVERSIBLE_TTL_S", str(24 * 3600)))

        # Crawling: respetar robots.txt al recorrer multipágina.
        self.respect_robots = (
            (e.get("RESPECT_ROBOTS", "1") or "1").strip().lower() in ("1", "true", "yes", "on")
        )

        # Embeddings / vector store (Capa 7, opcional). Necesita Postgres+pgvector + LLM.
        self.embeddings_enabled = (
            (e.get("EMBEDDINGS_ENABLED", "") or "").strip().lower() in ("1", "true", "yes", "on")
        )
        self.embedding_model = e.get("EMBEDDING_MODEL", "text-embedding-3-small")

        self.privacy_matrix_path = e.get("PRIVACY_MATRIX_PATH", _DEFAULT_MATRIX)

        # Límites de fetch (SSRF / costos). Ver ADR-004.
        self.fetch_timeout_s = float(e.get("FETCH_TIMEOUT_S", "20"))
        self.fetch_max_bytes = int(e.get("FETCH_MAX_BYTES", str(10 * 1024 * 1024)))
        self.fetch_max_redirects = int(e.get("FETCH_MAX_REDIRECTS", "5"))

        # Cliente HTTP hacia Anonimal/Escriba.
        self.anonimal_timeout_s = float(e.get("ANONIMAL_TIMEOUT_S", "180"))
        self.callback_timeout_s = float(e.get("CALLBACK_TIMEOUT_S", "15"))

        # --- Fetch escalonado: tiers, proxies, anti-captcha (Capa 3, ADR-006) ---
        # Tope de escalado: 0 estático, 1 TLS, 2 stealth, 3 browser. Aunque las libs
        # de tiers altos estén instaladas, no se sube más allá de este número.
        self.max_fetch_tier = int(e.get("MAX_FETCH_TIER", "3"))
        # Cache del tier ganador por dominio.
        self.tier_cache_ttl_s = int(e.get("TIER_CACHE_TTL_S", str(7 * 24 * 3600)))

        # Proxies: lista coma/salto-separada de URLs (scheme://user:pass@host:port).
        self.proxies_raw = e.get("PROXIES", "") or ""
        self.proxy_rotation = (e.get("PROXY_ROTATION", "round_robin") or "round_robin").strip().lower()
        self.proxy_cooldown_s = float(e.get("PROXY_COOLDOWN_S", "120"))
        self.proxy_attempts = int(e.get("PROXY_ATTEMPTS", "2"))

        # Anti-CAPTCHA: 'none' (solo escalar, prevención) | 'external' (solver por API).
        self.captcha_solver = (e.get("CAPTCHA_SOLVER", "none") or "none").strip().lower()
        self.captcha_solver_url = e.get("CAPTCHA_SOLVER_URL", "")
        self.captcha_solver_key = e.get("CAPTCHA_SOLVER_KEY", "")

        # Browser tiers (2/3): stealth. headless=False es menos detectable pero necesita
        # display (en server usar xvfb o dejar True). settle = espera tras cargar; scroll
        # dispara contenido lazy. UA realista.
        self.browser_headless = (
            (e.get("BROWSER_HEADLESS", "1") or "1").strip().lower() in ("1", "true", "yes", "on")
        )
        self.browser_settle_s = float(e.get("BROWSER_SETTLE_S", "3.5"))
        self.browser_scroll = (
            (e.get("BROWSER_SCROLL", "1") or "1").strip().lower() in ("1", "true", "yes", "on")
        )
        self.browser_user_agent = e.get(
            "BROWSER_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        self.browser_locale = e.get("BROWSER_LOCALE", "es-AR")

        # Allowlist de destinos de callback en producción. Vacío = solo bloqueo SSRF.
        # Coma-separada de hosts. Ver ADR-004 punto 3.
        raw_allow = (e.get("CALLBACK_ALLOWLIST", "") or "").strip()
        self.callback_allowlist = (
            [h.strip().lower() for h in raw_allow.split(",") if h.strip()] if raw_allow else []
        )

        # Permitir destinos privados (SOLO para tests/dev local). Nunca en prod.
        self.allow_private_targets = (
            (e.get("ALLOW_PRIVATE_TARGETS", "") or "").strip().lower() in ("1", "true", "yes", "on")
        )

        # Cookie de sesión: Secure por defecto (solo viaja por HTTPS). Para dev local
        # sobre http://127.0.0.1 poné COOKIE_SECURE=0 o el navegador no la mandará.
        self.cookie_secure = (
            (e.get("COOKIE_SECURE", "1") or "1").strip().lower() in ("1", "true", "yes", "on")
        )

        # Archivos y media (manifiesto de descargables). 'both' = ofrecer link directo
        # Y descarga vía Fisherboy (proxy); 'direct' = solo link directo; 'proxy' = solo
        # vía Fisherboy; 'off' = no harvestear archivos. La detección siempre corre salvo
        # 'off'; el modo solo cambia qué botones muestra la UI.
        self.file_download_mode = (
            (e.get("FILE_DOWNLOAD_MODE", "both") or "both").strip().lower()
        )
        if self.file_download_mode not in ("both", "direct", "proxy", "off"):
            self.file_download_mode = "both"
        # Tope de tamaño para la descarga vía Fisherboy (proxy stream). Protege RAM/ancho
        # de banda del worker. La descarga directa no pasa por acá (la hace el navegador).
        self.download_max_bytes = int(e.get("DOWNLOAD_MAX_BYTES", str(200 * 1024 * 1024)))
        # Descarga de video (yt-dlp): mp4 de YouTube/Vimeo/etc. Para sitios que bloquean
        # por IP, YT_PROXY enruta los pedidos y YT_COOKIES (ruta a un cookies.txt Netscape)
        # da una sesión logueada. Mismos nombres que Escriba para reusar config.
        self.yt_proxy = e.get("YT_PROXY", "") or ""
        self.yt_cookies = e.get("YT_COOKIES", "") or ""   # ruta a cookies.txt
        # Altura máxima del video (calidad). En Docker con ffmpeg sube hasta acá muxeando;
        # sin ffmpeg cae al mejor progresivo (un solo archivo), típicamente ≤360p.
        self.video_max_height = int(e.get("VIDEO_MAX_HEIGHT", "1080"))
        # Datos de Instagram (instaloader): comentarios de un post + seguidores/seguidos.
        # IG_SESSIONID = cookie de sesión de una cuenta logueada (sin él no hay datos).
        # IG_MAX_ITEMS = tope de items por pedido (protege la cuenta del rate-limit de IG).
        self.ig_sessionid = e.get("IG_SESSIONID", "") or ""
        self.ig_max_items = int(e.get("IG_MAX_ITEMS", "500"))

        # Límites anti-DoS (auditoría 2026-06).
        # Rate-limit de admisión de jobs (ventana fija por minuto, por IP). 0 = sin límite.
        self.max_jobs_per_min = int(e.get("MAX_JOBS_PER_MIN", "60"))
        # Tope DURO de páginas por job (crawl/paginado). Acota RAM/tiempo del worker.
        self.crawl_max_pages = int(e.get("CRAWL_MAX_PAGES", "100"))
        # Tope de bytes ACUMULADOS por job (además del cap por página). 0 = sin tope.
        self.job_max_total_bytes = int(e.get("JOB_MAX_TOTAL_BYTES", str(80 * 1024 * 1024)))

        self.log_level = (e.get("LOG_LEVEL", "INFO") or "INFO").upper()

    @property
    def is_standalone(self) -> bool:
        return self.app_mode is AppMode.STANDALONE

    @property
    def is_sidekick(self) -> bool:
        return self.app_mode is AppMode.SIDEKICK


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
