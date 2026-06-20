"""Manifiesto de archivos y media de una página.

Olfatea el HTML renderizado (y, opcionalmente, el JSON capturado) y devuelve los
descargables clasificados: documentos, comprimidos, audio, video, imágenes y
embebidos/visores. Desincrusta visores conocidos (Google Docs viewer) para sacar el
archivo real. Resuelve URLs relativas a absolutas y deduplica.

Es una función pura y testeable: no hace red. El que decide cómo se baja (link directo
o vía proxy) es la capa de arriba (config FILE_DOWNLOAD_MODE).
"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urljoin, urlsplit

# Extensiones por categoría.
_EXT = {
    "document": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp",
                 "rtf", "epub", "csv", "tsv", "txt", "md", "json", "xml", "yaml", "yml"},
    "archive": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz"},
    "audio": {"mp3", "wav", "m4a", "ogg", "flac", "aac", "opus", "weba"},
    "video": {"mp4", "webm", "mkv", "mov", "avi", "m4v", "flv", "wmv"},
    "image": {"jpg", "jpeg", "png", "gif", "svg", "webp", "bmp", "tif", "tiff", "ico", "avif"},
}
_EXT_TO_KIND = {ext: kind for kind, exts in _EXT.items() for ext in exts}

# Tope por categoría para no devolver manifiestos gigantes.
_CAP = {"document": 200, "archive": 200, "audio": 200, "video": 200, "image": 300, "embed": 100}

# Proveedores de embed que se pueden bajar como video (yt-dlp).
_VIDEO_PROVIDERS = frozenset({"youtube", "vimeo"})


def _ext_of(url: str) -> str:
    path = urlsplit(url).path.lower()
    seg = path.rsplit("/", 1)[-1]
    return seg.rsplit(".", 1)[-1] if "." in seg else ""


def _name_of(url: str) -> str:
    path = urlsplit(url).path
    seg = unquote(path.rsplit("/", 1)[-1]) if path else ""
    return seg or (urlsplit(url).hostname or url)


def _unwrap_viewer(url: str) -> str | None:
    """Visores que llevan la URL del archivo real en un query param (Google Docs/Drive,
    Office viewer, Mozilla pdf.js...). Devuelve el archivo real o None."""
    low = url.lower()
    if not any(h in low for h in ("docs.google.com", "drive.google.com",
                                  "view.officeapps.live.com", "mozilla.github.io/pdf.js",
                                  "/viewer")):
        return None
    q = parse_qs(urlsplit(url).query)
    for key in ("url", "file", "src", "document"):
        if q.get(key):
            cand = unquote(q[key][0])
            if cand.lower().startswith(("http://", "https://")):
                return cand
    return None


def _embed_provider(url: str):
    u = url.lower()
    if "youtube.com/embed" in u or "youtu.be/" in u or "youtube-nocookie.com/embed" in u:
        return ("youtube", "Video de YouTube")
    if "player.vimeo.com" in u or "vimeo.com/video" in u:
        return ("vimeo", "Video de Vimeo")
    if "scribd.com/embeds" in u or "scribd.com/doc" in u:
        return ("scribd", "Documento de Scribd")
    if "slideshare.net" in u:
        return ("slideshare", "Presentación de SlideShare")
    if "docs.google.com" in u or "drive.google.com" in u:
        return ("gdocs", "Google Docs")
    if "soundcloud.com" in u:
        return ("soundcloud", "Audio de SoundCloud")
    if "spotify.com/embed" in u:
        return ("spotify", "Spotify")
    return None


def harvest_assets(html_text: str, base_url: str, capture_json=None) -> dict:
    """Devuelve {document, archive, audio, video, image, embed} con items
    {url, name, ext[, via]} (los embed llevan {url, name, provider})."""
    out: dict[str, list] = {k: [] for k in ("document", "archive", "audio", "video", "image", "embed")}
    seen: set = set()

    def add(kind: str, raw: str | None, name: str | None = None, via: str | None = None) -> None:
        if not raw:
            return
        absu = urljoin(base_url, raw.strip())
        if not absu.lower().startswith(("http://", "https://")):
            return
        absu = absu.split("#", 1)[0]
        key = (kind, absu)
        if key in seen or len(out[kind]) >= _CAP[kind]:
            return
        seen.add(key)
        item = {"url": absu, "name": (name or _name_of(absu))[:120], "ext": _ext_of(absu)}
        if via:
            item["via"] = via
        out[kind].append(item)

    try:
        from lxml import html as lxml_html
        doc = lxml_html.fromstring(html_text or "")
    except Exception:  # noqa: BLE001 — HTML inválido / vacío
        doc = None

    if doc is not None:
        for a in doc.xpath("//a[@href]"):
            href = a.get("href")
            kind = _EXT_TO_KIND.get(_ext_of(href))
            txt = (a.text_content() or "").strip()
            if kind:
                add(kind, href, name=txt or None)
            elif a.get("download") is not None:   # <a download> sin extensión clara
                add("document", href, name=(a.get("download") or txt or None))
        for v in doc.xpath("//video"):
            add("video", v.get("src"))
            for s in v.xpath(".//source[@src]"):
                add("video", s.get("src"))
            if v.get("poster"):
                add("image", v.get("poster"))
        for au in doc.xpath("//audio"):
            add("audio", au.get("src"))
            for s in au.xpath(".//source[@src]"):
                add("audio", s.get("src"))
        for img in doc.xpath("//img[@src]"):
            add("image", img.get("src"))
        for ob in doc.xpath("//object[@data]"):
            kind = _EXT_TO_KIND.get(_ext_of(ob.get("data")), "document")
            add(kind, ob.get("data"))
        for fr in doc.xpath("//iframe[@src] | //embed[@src]"):
            src = fr.get("src")
            unwrapped = _unwrap_viewer(src)
            if unwrapped:
                kind = _EXT_TO_KIND.get(_ext_of(unwrapped), "document")
                add(kind, unwrapped, via="visor desincrustado")
                continue
            prov = _embed_provider(src)
            if prov:
                absu = urljoin(base_url, src)
                if absu not in {e["url"] for e in out["embed"]} and len(out["embed"]) < _CAP["embed"]:
                    item = {"url": absu, "name": prov[1], "provider": prov[0]}
                    if prov[0] in _VIDEO_PROVIDERS:   # bajable con yt-dlp → la UI ofrece "Descargar video"
                        item["dl_video"] = True
                    out["embed"].append(item)
            else:                                  # embed con extensión de archivo directa
                kind = _EXT_TO_KIND.get(_ext_of(src))
                if kind:
                    add(kind, src)

    # Archivos que aparezcan como URLs en el JSON capturado (SPAs).
    if capture_json is not None:
        _walk_json_for_files(capture_json, base_url, add)

    out["total"] = sum(len(v) for k, v in out.items() if k != "total")
    return out


def _walk_json_for_files(obj, base_url: str, add, _budget: list | None = None) -> None:
    budget = _budget if _budget is not None else [4000]
    stack = [obj]
    while stack and budget[0] > 0:
        cur = stack.pop()
        budget[0] -= 1
        if isinstance(cur, str):
            if cur.lower().startswith(("http://", "https://", "/")):
                kind = _EXT_TO_KIND.get(_ext_of(cur))
                if kind and kind != "image":   # evita inundar con thumbnails del JSON
                    add(kind, cur)
        elif isinstance(cur, list):
            stack.extend(cur[:200])
        elif isinstance(cur, dict):
            stack.extend(list(cur.values())[:200])
