"""Capa 0 — superficie REST de Fisherboy. Ver FISHERBOY-build §10.

Tres endpoints en v1: encolar job, consultar job, health. El router de UI se monta
solo si APP_MODE=standalone (ADR-001). El REST queda disponible en los dos modos.

Orden de validación en POST /api/jobs (no negociable, ADR-004):
  1. schema (Pydantic valida JobRequest)
  2. rol × modo (privacy_policy; 403 si el rol no habilita el modo)
  3. callback_url contra bloques SSRF (400 si apunta a destino prohibido)
  4. recién entonces se encola
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .config import Settings, get_settings
from .logging import get_logger, setup_logging
from .models import JobRequest, JobStatus, Rol, RevertRequest, Sobre
from .privacy_policy import PolicyDenied, PrivacyPolicy, get_policy
from .queue import JobQueue, get_queue
from .security import auth, ratelimit
from .security.ssrf import SSRFError, validate_callback_url, validate_proxy_url

log = get_logger("fisherboy.api")

_RANK = {"humano": 0, "angel": 1, "dios": 2}


class LoginRequest(BaseModel):
    key: str


class ProxyTestRequest(BaseModel):
    proxy: str


class DownloadZipRequest(BaseModel):
    urls: list[str]
    name: str | None = None


def create_app(
    settings: Settings | None = None,
    *,
    queue: JobQueue | None = None,
    policy: PrivacyPolicy | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    # Avisos de arranque ruidosos (fail-closed igual protege con 401, pero que se vea).
    _warns = [auth.insecure_open_warning(), auth.secret_key_warning()]
    if settings.allow_private_targets:
        _warns.append("ALLOW_PRIVATE_TARGETS activo: la defensa SSRF está DESACTIVADA "
                      "(fetch y callback pueden alcanzar la red interna/metadata). Solo dev/test.")
    for _w in _warns:
        if _w:
            log.warning("SEGURIDAD: %s", _w)

    app = FastAPI(title="Fisherboy", version="1.0.0")
    app.state.settings = settings
    app.state.queue = queue
    app.state.policy = policy or get_policy(settings.privacy_matrix_path)

    def _queue() -> JobQueue:
        if app.state.queue is None:  # construcción perezosa: el worker/redis no hace falta en tests
            app.state.queue = get_queue(settings)
        return app.state.queue

    app.state.reversible = None
    app.state.reversible_built = False

    def _reversible():
        if not app.state.reversible_built:
            app.state.reversible_built = True
            try:
                from .privacy.anonimal_client import AnonimalClient
                from .privacy.reversible import build_reversible_anonymizer
                client = AnonimalClient(settings.anonimal_url, service_token=settings.anonimal_token)
                redis_client = None
                try:
                    from .queue import build_redis
                    redis_client = build_redis(settings.redis_url)
                except Exception:  # noqa: BLE001
                    redis_client = None
                app.state.reversible = build_reversible_anonymizer(
                    settings, client, policy=app.state.policy, redis_client=redis_client
                )
            except Exception as exc:  # noqa: BLE001 — sin cripto, reversible queda None
                log.warning("reversible no disponible", extra={"error": type(exc).__name__})
                app.state.reversible = None
        return app.state.reversible

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "app_mode": settings.app_mode.value, "version": "1.0.0",
                "download_mode": settings.file_download_mode}

    # --- Auth (espejo de Escriba): login por rol, cookie + Bearer ---------------
    @app.post("/api/login")
    async def login(body: LoginRequest, request: Request):
        # Anti fuerza-bruta: rate-limit por IP sobre los intentos de login (falla
        # abierto si no hay Redis, igual que el resto). Cuenta TODOS los intentos,
        # válidos o no, para que un atacante no pueda sondear claves sin freno.
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"login:{client_ip}",
                               limit=settings.max_logins_per_min):
            raise HTTPException(status_code=429, detail="Demasiados intentos; probá en un minuto.")
        role = auth.role_for_password(body.key)
        if role is None:
            raise HTTPException(status_code=401, detail="Clave inválida.")
        resp = JSONResponse({"role": role, "caps": auth.caps_for(role)})
        resp.set_cookie(auth.COOKIE_NAME, auth.make_token(role), httponly=True,
                        secure=settings.cookie_secure, samesite="lax",
                        max_age=auth.SESSION_TTL, path="/")
        return resp

    @app.post("/api/logout")
    async def logout():
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(auth.COOKIE_NAME, path="/", samesite="lax",
                           secure=settings.cookie_secure)
        return resp

    @app.get("/api/me")
    async def me(request: Request):
        role = auth.role_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        from .net import gallery, instagram, media
        proxy_modes = settings.file_download_mode in ("both", "proxy")
        can_capture = bool(auth.caps_for(role).get("capture"))
        video_ok = proxy_modes and media.ytdlp_available() and can_capture
        gallery_ok = proxy_modes and gallery.gallerydl_available() and can_capture
        ig_ok = (proxy_modes and role == "dios" and instagram.instaloader_available()
                 and bool(settings.ig_sessionid))
        allowed_modes = sorted(m.value for m in app.state.policy.allowed_modes(Rol(role)))
        return {"role": role, "caps": auth.caps_for(role), "auth_enabled": auth.auth_enabled(),
                "download_mode": settings.file_download_mode,
                "video_download": video_ok, "ffmpeg": media.ffmpeg_available(),
                "gallery_download": gallery_ok, "instagram_data": ig_ok,
                "comments_download": proxy_modes and can_capture,
                "allowed_modes": allowed_modes, "default_mode": app.state.policy._default.value}

    def _effective_role(request: Request, body_rol, session: str | None = None) -> str:
        """Rol de sesión; si el body pide un rol IGUAL o MENOR, se respeta (downgrade).
        Nunca se puede escalar por encima del rol de sesión (anti-escalada)."""
        if session is None:
            session = auth.role_from_request(request)
        if session is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        if body_rol is not None and _RANK.get(body_rol.value, 9) <= _RANK[session]:
            return body_rol.value
        return session

    @app.post("/api/jobs", status_code=202)
    async def create_job(req: JobRequest, request: Request):
        # 1. autenticación + rol efectivo (de la sesión, no escalable) + identidad
        session_role, owner_jti = auth.identity_from_request(request)
        role = _effective_role(request, req.rol, session_role)
        # 1b. rate-limit de admisión por IP (anti-flood; falla abierto si no hay Redis)
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"jobs:{client_ip}", limit=settings.max_jobs_per_min):
            raise HTTPException(status_code=429, detail="Demasiados jobs; probá en un minuto.")
        rol_enum = Rol(role)
        caps = auth.caps_for(role)

        # 2. rol × modo de privacidad (matriz)
        try:
            mode = app.state.policy.resolve_mode(rol_enum, req.privacy_mode)
        except PolicyDenied as e:
            raise HTTPException(status_code=403, detail=str(e))

        # 2b. gating de capacidades (mismo helper que el MCP; incluye veto por sidekick)
        try:
            auth.enforce_job_caps(role, req, is_sidekick=settings.is_sidekick)
        except auth.CapDenied as e:
            raise HTTPException(status_code=403, detail=str(e))

        # 3. callback_url + proxy contra bloques SSRF
        if req.callback_url is not None:
            try:
                validate_callback_url(
                    str(req.callback_url),
                    allowlist=settings.callback_allowlist,
                    allow_private=settings.allow_private_targets,
                )
            except SSRFError as e:
                raise HTTPException(status_code=400, detail=f"callback_url inválido: {e}")
        if req.proxy:
            try:
                validate_proxy_url(req.proxy, allow_private=settings.allow_private_targets)
            except SSRFError as e:
                raise HTTPException(status_code=400, detail=f"proxy inválido: {e}")

        # 4. encolar
        job_id = uuid.uuid4().hex
        sobre = Sobre(
            job_id=job_id,
            source_url=req.url,
            privacy_mode=mode,
            rol=rol_enum,
            output_format=req.output_format,
        )
        sobre.meta["max_tier"] = caps["max_tier"]   # cap del escalado automático por rol
        if owner_jti:
            sobre.meta["owner_jti"] = owner_jti      # dueño de la sesión (ownership en lectura)
        if req.callback_url is not None:
            sobre.meta["callback_url"] = str(req.callback_url)
        if req.tier_hint is not None:
            sobre.meta["tier_hint"] = int(req.tier_hint)
        sobre.meta["crawl_depth"] = int(req.crawl_depth)
        sobre.meta["max_pages"] = min(int(req.max_pages), settings.crawl_max_pages)  # tope duro
        if req.paginate:
            sobre.meta["paginate"] = True
        if req.crawl_scope == "path":
            sobre.meta["crawl_scope"] = "path"
        if req.capture_api:
            sobre.meta["capture_api"] = True
        if req.tarantula:
            sobre.meta["tarantula"] = True
        if req.extract_schema is not None:
            sobre.meta["extract_schema"] = req.extract_schema
        if req.proxy:
            sobre.meta["proxy"] = req.proxy
        if req.captcha_api_url and req.captcha_api_key:
            sobre.meta["captcha_api_url"] = req.captcha_api_url
            sobre.meta["captcha_api_key"] = req.captcha_api_key
        if req.cookies:
            sobre.meta["cookies"] = req.cookies
        if req.cookies_browser:
            sobre.meta["cookies_browser"] = req.cookies_browser
        _queue().enqueue(sobre)

        log.info("job encolado", extra={"job_id": job_id, "mode": mode.value, "rol": role})
        return {"job_id": job_id, "status": JobStatus.PENDIENTE.value}

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str, request: Request):
        role, jti = auth.identity_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        sobre = _queue().get(job_id)
        if sobre is None:
            raise HTTPException(status_code=404, detail="job no encontrado")
        # Ownership: si el job tiene dueño y no sos vos (ni dios), 404 (no filtra existencia).
        owner = sobre.meta.get("owner_jti")
        if owner and role != "dios" and owner != jti:
            raise HTTPException(status_code=404, detail="job no encontrado")
        # Nunca devolver los secretos por-job (proxy/captcha/cookies). Ver public_dump.
        return JSONResponse(sobre.public_dump(mode="json"))

    @app.post("/api/revert")
    async def revert(req: "RevertRequest", request: Request):
        """Rehidrata contenido pseudonimizado con un mapping_ref (modo reversible).

        El rol sale de la SESIÓN (no del body, sin downgrade): valida contra el dueño del
        mapeo y la matriz. Un solo uso: el mapeo se borra tras revertir. Ver ADR-005.
        """
        role = auth.role_from_request(request)   # NO _effective_role: el downgrade del body
        if role is None:                          # permitiría a un rol alto hacerse pasar por el owner
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        rev = _reversible()
        if rev is None:
            raise HTTPException(status_code=503, detail="Modo reversible no disponible (falta cripto).")
        try:
            content = rev.revert(req.content, req.mapping_ref, Rol(role))
        except Exception as e:  # ReversibleError u otra falla controlada
            raise HTTPException(status_code=403, detail=str(e))
        return {"content": content}

    @app.post("/api/proxy/test")
    async def proxy_test(req: ProxyTestRequest, request: Request):
        """Prueba un proxy: rutea una request por él y devuelve la IP de salida + latencia.

        Da feedback inmediato en la UI antes de correr un job. Gateado al rol que habilita
        proxy (ángel/dios), rate-limited, y el proxy se valida contra la denylist SSRF.
        El destino de prueba es FIJO (echo de IP), no controlado por el usuario.
        """
        role, _ = auth.identity_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        if not auth.caps_for(role).get("proxy"):
            raise HTTPException(status_code=403, detail=f"Tu rol '{role}' no habilita usar proxy.")
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"proxytest:{client_ip}", limit=settings.max_jobs_per_min):
            raise HTTPException(status_code=429, detail="Demasiadas pruebas; probá en un minuto.")
        try:
            validate_proxy_url(req.proxy, allow_private=settings.allow_private_targets)
        except SSRFError as e:
            raise HTTPException(status_code=400, detail=f"proxy inválido: {e}")

        import time as _time

        import httpx

        def _egress_ip() -> str:
            """IP de salida REAL (sin proxy), para sugerirle al usuario qué autorizar."""
            try:
                rr = httpx.get("http://ip-api.com/json/?fields=query", timeout=6.0)
                return (rr.json() or {}).get("query", "")
            except Exception:  # noqa: BLE001
                return ""

        # Echo de IP a través del proxy (destino fijo y seguro). ip-api da IP + país.
        t0 = _time.monotonic()
        try:
            with httpx.Client(proxy=req.proxy, timeout=12.0, follow_redirects=False) as c:
                r = c.get("http://ip-api.com/json/?fields=status,country,countryCode,query")
                ms = int((_time.monotonic() - t0) * 1000)
                data = {}
                try:
                    data = r.json()
                except Exception:  # noqa: BLE001
                    data = {}
                ip = data.get("query") or ""
                if not ip:  # fallback: solo IP, por HTTPS
                    rr = c.get("https://api.ipify.org?format=json")
                    try:
                        ip = (rr.json() or {}).get("ip", "")
                    except Exception:  # noqa: BLE001
                        ip = ""
                if not ip:
                    return {"ok": False, "error": "el proxy respondió pero no se pudo leer la IP de salida"}
                return {"ok": True, "ip": ip, "country": data.get("country", ""),
                        "country_code": data.get("countryCode", ""), "ms": ms}
        except httpx.ProxyError:
            return {"ok": False, "kind": "auth",
                    "error": "el proxy rechazó la autenticación — revisá usuario y clave."}
        except (httpx.ConnectError, httpx.ConnectTimeout):
            # El proxy no contesta a nivel TCP: caído, puerto mal, o (lo más común) tu IP
            # no está autorizada. Muchos proveedores (InstantProxies, etc.) usan whitelist de IP.
            return {"ok": False, "kind": "noconnect", "your_ip": _egress_ip(),
                    "error": "no conecta con el proxy (timeout). Si tu proveedor autoriza por IP, "
                             "habilitá tu IP de salida:"}
        except httpx.HTTPError as e:
            return {"ok": False, "error": f"falló por el proxy ({type(e).__name__})"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"falló la prueba ({type(e).__name__})"}

    # --- Descarga de archivos vía Fisherboy (proxy stream) ----------------------
    def _download_enabled() -> None:
        if settings.file_download_mode in ("off", "direct"):
            raise HTTPException(status_code=403,
                                detail="La descarga vía Fisherboy está deshabilitada (FILE_DOWNLOAD_MODE).")

    @app.get("/api/download")
    async def download(request: Request, url: str):
        """Baja un archivo remoto a través de Fisherboy (stream SSRF-seguro, tope de tamaño).

        Útil para hotlink-protection o para usar el egress del server. Gateado a sesión
        y rate-limited. La URL se valida contra la denylist SSRF en cada salto."""
        role, _ = auth.identity_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        _download_enabled()
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"dl:{client_ip}", limit=settings.max_jobs_per_min):
            raise HTTPException(status_code=429, detail="Demasiadas descargas; probá en un minuto.")

        from .fetchers.base import FetchError
        from .net.download import open_stream, safe_filename
        from .security.ssrf import SSRFError
        try:
            dl_client, resp = open_stream(url, allow_private=settings.allow_private_targets,
                                          timeout_s=settings.fetch_timeout_s)
        except SSRFError as e:
            raise HTTPException(status_code=400, detail=f"URL no permitida: {e}")
        except FetchError as e:
            raise HTTPException(status_code=502, detail=str(e))

        ctype = resp.headers.get("content-type", "application/octet-stream")
        clen = resp.headers.get("content-length")
        if clen and clen.isdigit() and int(clen) > settings.download_max_bytes:
            resp.close()
            dl_client.close()
            raise HTTPException(status_code=413,
                                detail=f"El archivo supera el límite de {settings.download_max_bytes} bytes.")
        fname = safe_filename(url, resp.headers.get("content-disposition", ""), ctype)
        cap = settings.download_max_bytes

        def _gen():
            total = 0
            try:
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > cap:
                        break   # corta el stream si excede el tope (sin content-length previo)
                    yield chunk
            finally:
                resp.close()
                dl_client.close()

        from urllib.parse import quote
        disp = f"attachment; filename*=UTF-8''{quote(fname)}"
        return StreamingResponse(_gen(), media_type=ctype, headers={"Content-Disposition": disp})

    @app.post("/api/download/zip")
    async def download_zip(req: DownloadZipRequest, request: Request):
        """Empaqueta varios archivos remotos en un ZIP (a memoria, con tope total)."""
        role, _ = auth.identity_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        _download_enabled()
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"dlzip:{client_ip}", limit=settings.max_jobs_per_min):
            raise HTTPException(status_code=429, detail="Demasiadas descargas; probá en un minuto.")
        urls = [u for u in (req.urls or []) if isinstance(u, str) and u.strip()][:100]
        if not urls:
            raise HTTPException(status_code=400, detail="No hay URLs para empaquetar.")

        import io
        import zipfile

        from .fetchers.base import FetchError
        from .net.download import fetch_bytes
        from .security.ssrf import SSRFError

        buf = io.BytesIO()
        budget = settings.download_max_bytes
        used = 0
        added = 0
        seen_names: set[str] = set()
        errors: list[str] = []
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for u in urls:
                remaining = budget - used
                if remaining <= 0:
                    errors.append("tope total alcanzado")
                    break
                try:
                    data, name, _ct = fetch_bytes(
                        u, max_bytes=remaining,
                        allow_private=settings.allow_private_targets,
                        timeout_s=settings.fetch_timeout_s)
                except (SSRFError, FetchError) as e:
                    errors.append(f"{u}: {e}")
                    continue
                used += len(data)
                base = name or "archivo"
                final = base
                i = 1
                while final in seen_names:   # evita colisiones de nombre en el zip
                    stem, dot, ext = base.rpartition(".")
                    final = f"{stem}_{i}.{ext}" if dot else f"{base}_{i}"
                    i += 1
                seen_names.add(final)
                zf.writestr(final, data)
                added += 1
            if added == 0:
                raise HTTPException(status_code=502,
                                    detail="No se pudo bajar ningún archivo. " + (errors[0] if errors else ""))
        buf.seek(0)
        headers = {"Content-Disposition": "attachment; filename=fisherboy-archivos.zip"}
        return StreamingResponse(iter([buf.getvalue()]), media_type="application/zip", headers=headers)

    @app.post("/api/download/video")
    async def download_video(request: Request):
        """Baja un video (mp4) o solo el audio (mp3) de YouTube/Vimeo/etc. con yt-dlp.

        Body JSON {url, quality?, audio?, cookies?, proxy?}. Las cookies y el proxy que el
        usuario cargó en la UI (este job) GANAN sobre YT_COOKIES/YT_PROXY del server: las
        cookies se escriben a un cookiefile temporal Netscape (secreto efímero, se borra al
        terminar). `quality`: 'best' o altura ('1080','720','480','360'), capada a
        VIDEO_MAX_HEIGHT. `audio=true`: solo audio (mp3 si hay ffmpeg, si no nativo).
        Gateado a rol con capacidad (ángel/dios), rate-limited, y la URL se restringe a
        plataformas conocidas (no es un proxy genérico). Tope de tamaño por formato.
        Si YouTube/la plataforma exige sesión (IP bloqueada) devuelve 422 needs_cookies en
        vez de un 502 (que el gateway se come y deja el error ilegible)."""
        role, _ = auth.identity_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        if not auth.caps_for(role).get("capture"):   # ángel/dios (operación cara)
            raise HTTPException(status_code=403, detail=f"Tu rol '{role}' no habilita descargar video.")
        _download_enabled()
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        url = (body.get("url") or "").strip()
        quality = body.get("quality") or "best"
        audio = bool(body.get("audio"))
        raw_cookies = body.get("cookies") or ""
        req_proxy = (body.get("proxy") or "").strip()
        from .net import media
        if not url:
            raise HTTPException(status_code=400, detail="Falta la URL.")
        if not media.ytdlp_available():
            raise HTTPException(status_code=503, detail="yt-dlp no está instalado en el servidor.")
        if not media.host_allowed(url):
            raise HTTPException(status_code=400,
                                detail="Solo se pueden bajar videos de plataformas conocidas (YouTube, Vimeo, etc.).")
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"dlvid:{client_ip}", limit=settings.max_jobs_per_min):
            raise HTTPException(status_code=429, detail="Demasiadas descargas; probá en un minuto.")

        import os
        import shutil
        import tempfile
        from urllib.parse import urlsplit

        from starlette.concurrency import run_in_threadpool

        from .security import cookies as cookmod

        quality = quality if quality in media.QUALITIES else "best"

        # Cookies de la UI (este job) > YT_COOKIES del server. Cookiefile temporal Netscape,
        # se borra abajo. El proxy de la UI gana sobre YT_PROXY del server.
        host = (urlsplit(url).hostname or "").lower()
        dom = ("." + (host[4:] if host.startswith("www.") else host)) if host else ".youtube.com"
        netscape = cookmod.to_netscape(raw_cookies, dom) if raw_cookies else ""
        tmp_cookiefile = ""
        if netscape:
            fd, tmp_cookiefile = tempfile.mkstemp(prefix="fb-vid-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(netscape)
        cookiefile = tmp_cookiefile or settings.yt_cookies
        had_cookies = bool(tmp_cookiefile or settings.yt_cookies)
        proxy = req_proxy or settings.yt_proxy

        tmpdir = tempfile.mkdtemp(prefix="fbvid_")
        try:
            path, name = await run_in_threadpool(
                media.download_video, url, tmpdir=tmpdir,
                max_bytes=settings.download_max_bytes, max_height=settings.video_max_height,
                quality=quality, audio_only=audio,
                proxy=proxy, cookiefile=cookiefile,
                timeout_s=int(settings.fetch_timeout_s),
            )
        except Exception as e:  # noqa: BLE001 — yt-dlp lanza tipos varios
            shutil.rmtree(tmpdir, ignore_errors=True)
            reason = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
            # Anti-bot / pide sesión: el front muestra el modal de cookies (distinto si ya
            # había cookies → entonces están vencidas/inválidas). 422, no 502: así el detalle
            # llega al navegador en vez de que el gateway lo reemplace por un "Bad Gateway".
            if media.is_auth_required(reason):
                return JSONResponse(status_code=422, content={
                    "needs_cookies": True, "had_cookies": had_cookies, "detail": reason})
            raise HTTPException(status_code=400, detail=f"No se pudo bajar: {reason}")
        finally:
            if tmp_cookiefile:
                try:
                    os.unlink(tmp_cookiefile)
                except OSError:
                    pass

        def _gen():
            try:
                with open(path, "rb") as fh:
                    while True:
                        chunk = fh.read(65536)
                        if not chunk:
                            break
                        yield chunk
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)

        import mimetypes
        from urllib.parse import quote
        ctype = mimetypes.guess_type(name)[0] or ("audio/mpeg" if audio else "video/mp4")
        disp = f"attachment; filename*=UTF-8''{quote(name)}"
        return StreamingResponse(_gen(), media_type=ctype,
                                 headers={"Content-Disposition": disp})

    @app.get("/api/download/gallery")
    async def download_gallery(request: Request, url: str):
        """Baja las imágenes/galería de Instagram/X/Reddit/etc. con gallery-dl → ZIP.

        Gemelo del de video. Gateado a rol con capacidad (ángel/dios), rate-limited, y la
        URL se restringe a plataformas conocidas. Tope de cantidad y de bytes total."""
        role, _ = auth.identity_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        if not auth.caps_for(role).get("capture"):   # ángel/dios
            raise HTTPException(status_code=403, detail=f"Tu rol '{role}' no habilita descargar galerías.")
        _download_enabled()
        from .net import gallery
        if not gallery.gallerydl_available():
            raise HTTPException(status_code=503, detail="gallery-dl no está instalado en el servidor.")
        if not gallery.gallery_host_allowed(url):
            raise HTTPException(status_code=400,
                                detail="Solo se pueden bajar galerías de plataformas conocidas (Instagram, X, Reddit, etc.).")
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"dlgal:{client_ip}", limit=settings.max_jobs_per_min):
            raise HTTPException(status_code=429, detail="Demasiadas descargas; probá en un minuto.")

        import io
        import os
        import shutil
        import tempfile
        import zipfile

        from starlette.concurrency import run_in_threadpool

        tmpdir = tempfile.mkdtemp(prefix="fbgal_")
        try:
            files = await run_in_threadpool(
                gallery.download_gallery, url, tmpdir=tmpdir, max_files=50,
                proxy=settings.yt_proxy, cookiefile=settings.yt_cookies,
                timeout_s=max(60, int(settings.fetch_timeout_s) * 6),
            )
            buf = io.BytesIO()
            budget = settings.download_max_bytes
            used = 0
            seen: set[str] = set()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for fp in files:
                    size = os.path.getsize(fp)
                    if used + size > budget:
                        break
                    used += size
                    base = os.path.basename(fp)
                    final, i = base, 1
                    while final in seen:
                        stem, dot, ext = base.rpartition(".")
                        final = f"{stem}_{i}.{ext}" if dot else f"{base}_{i}"
                        i += 1
                    seen.add(final)
                    zf.write(fp, final)
            payload = buf.getvalue()
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=f"No se pudo bajar la galería: {e}")
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

        return StreamingResponse(iter([payload]), media_type="application/zip",
                                 headers={"Content-Disposition": "attachment; filename=fisherboy-galeria.zip"})

    # --- Datos de Instagram (instaloader): comentarios + seguidores/seguidos -----
    async def _ig_data(request: Request, url: str, fetch_fn, expect_kind: str, **fn_kw):
        role, _ = auth.identity_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        if role != "dios":   # usa la sesión personal de IG → solo dios
            raise HTTPException(status_code=403, detail="Los datos de Instagram son solo para el rol dios.")
        _download_enabled()
        from .net import instagram
        if not instagram.instaloader_available():
            raise HTTPException(status_code=503, detail="instaloader no está instalado en el servidor.")
        if not settings.ig_sessionid:
            raise HTTPException(status_code=503,
                                detail="Falta IG_SESSIONID (el cookie de sesión de Instagram) en la config.")
        if instagram.url_kind(url) != expect_kind:
            raise HTTPException(status_code=400,
                                detail=f"La URL no es un {'post' if expect_kind == 'post' else 'perfil'} de Instagram.")
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"ig:{client_ip}", limit=settings.max_jobs_per_min):
            raise HTTPException(status_code=429, detail="Demasiados pedidos; probá en un minuto.")

        from starlette.concurrency import run_in_threadpool
        try:
            items = await run_in_threadpool(fetch_fn, url, settings.ig_sessionid,
                                            max_items=settings.ig_max_items, **fn_kw)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))
        except Exception as e:  # noqa: BLE001 — instaloader lanza tipos varios
            raise HTTPException(status_code=502,
                                detail=f"Instagram falló: {str(e).splitlines()[0][:160] if str(e) else type(e).__name__}")
        return JSONResponse({"count": len(items), "items": items})

    @app.get("/api/instagram/comments")
    async def ig_comments(request: Request, url: str):
        from .net import instagram
        return await _ig_data(request, url, instagram.get_comments, "post")

    @app.get("/api/instagram/follows")
    async def ig_follows(request: Request, url: str, which: str = "followers"):
        from .net import instagram
        which = "followees" if which == "followees" else "followers"
        return await _ig_data(request, url, instagram.get_follows, "profile", which=which)

    @app.post("/api/comments")
    async def comments_ep(request: Request):
        """Comentarios de Reddit/YouTube (confiable) o X/TikTok (experimental, puede fallar).

        Body JSON {url, cookies?}. Las cookies que el usuario cargó en la UI ganan sobre el
        YT_COOKIES del servidor: se escriben a un cookiefile temporal Netscape (secreto efímero,
        se borra al terminar). Si la plataforma pide sesión y no alcanzan, devuelve needs_cookies.
        """
        role, _ = auth.identity_from_request(request)
        if role is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        if not auth.caps_for(role).get("capture"):
            raise HTTPException(status_code=403, detail=f"Tu rol '{role}' no habilita traer comentarios.")
        _download_enabled()
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        url = (body.get("url") or "").strip()
        raw_cookies = body.get("cookies") or ""
        if not url:
            raise HTTPException(status_code=400, detail="Falta la URL.")
        from .net import comments as cmod
        from .net import media
        from .security import cookies as cookmod
        plat = cmod.comment_platform(url)
        if not plat:
            raise HTTPException(status_code=400, detail="Plataforma sin soporte de comentarios.")
        if plat != "reddit" and not media.ytdlp_available():
            raise HTTPException(status_code=503, detail="yt-dlp no está instalado en el servidor.")
        client_ip = request.client.host if request.client else "?"
        if not ratelimit.allow(_queue()._r, f"cmt:{client_ip}", limit=settings.max_jobs_per_min):
            raise HTTPException(status_code=429, detail="Demasiados pedidos; probá en un minuto.")

        # Cookies de la UI (este job) > YT_COOKIES del server. Cookiefile temporal, se borra abajo.
        import os
        import tempfile
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or "").lower()
        dom = ("." + (host[4:] if host.startswith("www.") else host)) if host else ".youtube.com"
        netscape = cookmod.to_netscape(raw_cookies, dom) if raw_cookies else ""
        tmp_cookiefile = ""
        if netscape:
            fd, tmp_cookiefile = tempfile.mkstemp(prefix="fb-cmt-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(netscape)
        cookiefile = tmp_cookiefile or settings.yt_cookies
        had_cookies = bool(tmp_cookiefile or settings.yt_cookies)

        from starlette.concurrency import run_in_threadpool
        try:
            items = await run_in_threadpool(
                cmod.get_comments, url, max_items=settings.ig_max_items,
                timeout_s=int(settings.fetch_timeout_s),
                proxy=settings.yt_proxy, cookiefile=cookiefile)
        except cmod.CommentsAuthRequired as e:
            # La plataforma pide sesión: el front muestra el modal de cookies (distinto si ya
            # había cookies cargadas → entonces están vencidas/inválidas).
            return JSONResponse(status_code=422, content={
                "needs_cookies": True, "had_cookies": had_cookies,
                "platform": plat, "detail": str(e)})
        except Exception as e:  # noqa: BLE001
            reason = str(e).splitlines()[0][:160] if str(e) else type(e).__name__
            raise HTTPException(status_code=502, detail=f"No se pudieron traer los comentarios: {reason}")
        finally:
            if tmp_cookiefile:
                try:
                    os.unlink(tmp_cookiefile)
                except OSError:
                    pass
        return JSONResponse({"count": len(items), "items": items, "platform": plat,
                             "experimental": plat in cmod.EXPERIMENTAL})

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        # Si hay auth configurada, /metrics también la exige (no exponer telemetría abierta).
        if auth.auth_enabled() and auth.role_from_request(request) is None:
            raise HTTPException(status_code=401, detail="Necesitás iniciar sesión.")
        from .obs.metrics import get_metrics

        body, ctype = get_metrics().render()
        return Response(content=body, media_type=ctype)

    # UI solo en standalone (ADR-001): el núcleo REST/MCP queda en los dos modos.
    if settings.is_standalone:
        from .ui.router import build_ui_router

        app.include_router(build_ui_router(settings.escriba_web_url))

    return app


app = create_app()
